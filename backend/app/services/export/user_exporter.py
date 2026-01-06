import os
import httpx
from app.logging_config import backend_logger
from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin


def calc_auth_data(username):
    h = 0
    for c in username:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return str(h % 100000)


class UserExporter(ExporterBase, LoggingMixin, MMApiMixin):
    # Cache for Mattermost config to avoid repeated API calls
    _mm_config_cache = None
    _config_cache_checked = False

    def _is_slack_bot(self):
        """Check if the Slack user is a bot."""
        raw_data = self.entity.raw_data or {}
        return raw_data.get("is_bot", False)

    async def _is_bot_creation_enabled(self) -> bool:
        """Check if bot account creation is enabled in Mattermost config.

        Returns True if EnableBotAccountCreation is true, False otherwise.
        Caches the result to avoid repeated API calls.
        """
        # Return cached value if already checked
        if UserExporter._config_cache_checked:
            return UserExporter._mm_config_cache

        try:
            resp = await self.mm_api_get("/api/v4/config")
            if resp.status_code == 200:
                config = resp.json()
                # Navigate nested config structure
                service_settings = config.get("ServiceSettings", {})
                bot_enabled = service_settings.get("EnableBotAccountCreation")

                # Cache the result
                UserExporter._mm_config_cache = bool(bot_enabled)
                UserExporter._config_cache_checked = True

                backend_logger.info(
                    f"Mattermost EnableBotAccountCreation: {UserExporter._mm_config_cache}"
                )
                return UserExporter._mm_config_cache
            else:
                backend_logger.warning(
                    f"Failed to get Mattermost config: status={resp.status_code}, "
                    f"assuming bot creation is enabled"
                )
                # Assume enabled if we can't check (fail open)
                UserExporter._mm_config_cache = True
                UserExporter._config_cache_checked = True
                return True
        except Exception as e:
            backend_logger.warning(
                f"Error checking Mattermost config: {e}, assuming bot creation is enabled"
            )
            # Assume enabled if error occurs (fail open)
            UserExporter._mm_config_cache = True
            UserExporter._config_cache_checked = True
            return True

    async def _get_mm_team_id(self):
        """Resolve Mattermost team ID from env or via API."""
        team_id = os.environ.get("MM_TEAM_ID")
        if team_id:
            return team_id
        team_name = os.environ.get("MM_TEAM", "test")
        try:
            resp = await self.mm_api_get(f"/api/v4/teams/name/{team_name}")
            if resp.status_code == 200:
                data = resp.json()
                tid = data.get("id")
                if tid:
                    return tid
        except Exception as e:
            backend_logger.error(f"Ошибка при получении team id: {e}")
        # Fallback (dev default)
        return "b7u9rycm43nip86mdiuqsxdcbe"

    async def _ensure_user_in_team(self, mm_user_id: str):
        """Ensure the given user is a member of the configured team."""
        team_id = await self._get_mm_team_id()
        payload = {"team_id": team_id, "user_id": mm_user_id}
        try:
            resp = await self.mm_api_post(f"/api/v4/teams/{team_id}/members", payload)
            if resp.status_code not in (200, 201):
                # If already a member, server may return an error; log and continue
                try:
                    data = resp.json()
                    backend_logger.debug(
                        f"Добавление пользователя {mm_user_id} в команду {team_id}: {resp.status_code} {data}"
                    )
                except Exception:
                    backend_logger.debug(
                        f"Добавление пользователя {mm_user_id} в команду {team_id}: {resp.status_code} {resp.text}"
                    )
            else:
                backend_logger.debug(
                    f"Пользователь {mm_user_id} добавлен в команду {team_id}"
                )
        except Exception as e:
            backend_logger.error(
                f"Ошибка добавления пользователя {mm_user_id} в команду: {e}"
            )

    def _get_avatar_url(self, raw_data):
        profile = (raw_data or {}).get("profile") or {}
        # Предпочтение: image_original > image_1024 > image_512 > ...
        for key in [
            "image_original",
            "image_1024",
            "image_512",
            "image_192",
            "image_72",
            "image_48",
            "image_32",
            "image_24",
        ]:
            url = profile.get(key)
            if url and "secure.gravatar.com" not in url:
                return url
        return None

    async def _upload_avatar(self, mm_user_id, avatar_url):
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(avatar_url)
                if resp.status_code != 200:
                    backend_logger.error(
                        f"Не удалось скачать аватарку: {avatar_url}, статус: {resp.status_code}"
                    )
                    return
                # Отправить в Mattermost
                files = {"image": ("avatar.png", resp.content, "image/png")}
                mm_url = f"{os.environ['MM_URL']}/api/v4/users/{mm_user_id}/image"
                headers = {"Authorization": f"Bearer {os.environ['MM_TOKEN']}"}
                upload_resp = await client.post(mm_url, files=files, headers=headers)
                if upload_resp.status_code == 200:
                    backend_logger.debug(
                        f"Аватарка пользователя {mm_user_id} успешно загружена в Mattermost"
                    )
                else:
                    backend_logger.error(
                        f"Ошибка загрузки аватарки в Mattermost: {upload_resp.status_code}, {upload_resp.text}"
                    )
        except Exception as e:
            backend_logger.error(f"Ошибка при загрузке аватарки: {e}")

    async def _handle_avatar_upload(self, mm_user_id):
        """Helper method to handle avatar upload if available."""
        avatar_url = self._get_avatar_url(self.entity.raw_data)
        if avatar_url:
            await self._upload_avatar(mm_user_id, avatar_url)

    def _normalize_bot_username(self, username: str, slack_id: str) -> str:
        """Normalize bot username to meet Mattermost bot API requirements.

        Mattermost bot API requires usernames that:
        - Use only lowercase letters, numbers, periods, hyphens, and underscores
        - Start with a letter
        - Are between 1 and 64 characters

        Args:
            username: The original username (may be Slack ID like 'USLACKBOT')
            slack_id: The Slack user ID for generating unique suffix if needed

        Returns:
            A normalized username that meets Mattermost bot API requirements
        """
        import re
        import hashlib

        # Convert to lowercase
        normalized = username.lower()

        # Replace invalid characters with underscores
        # Valid chars are: a-z, 0-9, ., -, _
        normalized = re.sub(r"[^a-z0-9._-]", "_", normalized)

        # Ensure starts with a letter (not number, period, hyphen, or underscore)
        if not normalized or not normalized[0].isalpha():
            normalized = f"slack_{normalized}"

        # Enforce max length of 64 chars
        # Reserve 9 chars for potential uniqueness suffix (_XXXXXXXX)
        max_base_length = 55
        if len(normalized) > max_base_length:
            # Generate a short hash from the slack_id for uniqueness
            hash_suffix = hashlib.md5(slack_id.encode()).hexdigest()[:8]
            normalized = f"{normalized[:max_base_length]}_{hash_suffix}"

        # Final safety check
        if len(normalized) > 64:
            normalized = normalized[:64]

        return normalized

    def _build_bot_payload(self):
        """Build payload for Mattermost Bot creation."""
        raw_data = self.entity.raw_data or {}
        profile = raw_data.get("profile") or {}
        slack_id = self.entity.slack_id
        username = raw_data.get("name") or slack_id

        # Normalize username for Mattermost bot API requirements
        username = self._normalize_bot_username(username, slack_id)

        # For bots, use real_name or construct from first/last name
        display_name = profile.get("real_name", "")
        if not display_name:
            first = profile.get("first_name", "")
            last = profile.get("last_name", "")
            display_name = f"{first} {last}".strip() or username

        description = profile.get("title", "")

        payload = {
            "username": username,
            "display_name": display_name,
            "description": description,
        }
        return payload

    async def _find_existing_bot(self, username):
        """Try to find an existing bot by username.

        Note: Mattermost doesn't have a direct bot lookup by username API,
        so we need to list bots and search. This implementation uses a
        reasonable limit of 200 bots per page, which should cover most cases.
        For environments with >200 bots, pagination could be implemented.
        """
        try:
            # List bots with a reasonable page size
            # Most deployments have <200 bots, so this is typically sufficient
            resp = await self.mm_api_get("/api/v4/bots?per_page=200")
            if resp.status_code == 200:
                bots = resp.json()
                for bot in bots:
                    if bot.get("username") == username:
                        # Return the user_id associated with the bot
                        return bot.get("user_id")
        except Exception as e:
            backend_logger.debug(f"Ошибка поиска существующего бота: {e}")
        return None

    def _build_mm_payload(self):
        raw_data = self.entity.raw_data or {}
        profile = raw_data.get("profile") or {}
        slack_id = self.entity.slack_id
        username = raw_data.get("name") or slack_id
        email = profile.get("email") or f"{username or slack_id}@example.com"
        notify_props = {"email": "false"}
        payload = {
            "username": username,
            "email": email,
            "password": "",
            "first_name": profile.get("first_name", ""),
            "last_name": profile.get("last_name", ""),
            "position": profile.get("title", ""),
            "locale": raw_data.get("locale") or profile.get("locale", ""),
            "props": raw_data.get("props", {}),
            "notify_props": notify_props,
            "auth_service": "gitlab",
            "auth_data": calc_auth_data(username),
        }
        tz = raw_data.get("tz")
        if tz:
            payload["timezone"] = {"automaticTimezone": tz}
        return payload

    async def export_entity(self):
        self.log_export(f"Экспорт пользователя {self.entity.slack_id}")

        # Check if this is a bot
        is_bot = self._is_slack_bot()

        if is_bot:
            # Check if bot creation is enabled in Mattermost
            bot_creation_enabled = await self._is_bot_creation_enabled()

            if bot_creation_enabled:
                # Try to export as bot
                await self._export_as_bot()
            else:
                # Fallback: export bot as regular user when bot creation is disabled
                backend_logger.warning(
                    f"Bot creation disabled in Mattermost config, exporting bot {self.entity.slack_id} as regular user"
                )
                await self._export_as_user()
        else:
            await self._export_as_user()

    async def _export_as_bot(self):
        """Export Slack bot as Mattermost Bot Account."""
        payload = self._build_bot_payload()
        username = payload.get("username")

        try:
            # Check if bot already exists
            existing_bot_user_id = await self._find_existing_bot(username)
            if existing_bot_user_id:
                self.entity.mattermost_id = existing_bot_user_id
                await self.set_status("success")
                backend_logger.debug(
                    f"Бот {self.entity.slack_id} уже существует в Mattermost с user_id: {existing_bot_user_id}"
                )
                await self._handle_avatar_upload(existing_bot_user_id)
                return

            # Create new bot
            resp = await self.mm_api_post("/api/v4/bots", payload)
            # Support both real httpx.Response and mocked coroutines in tests
            # Some test mocks may accidentally return a coroutine object instead of Response
            if hasattr(resp, "__await__") and not hasattr(resp, "status_code"):
                resp = await resp  # type: ignore

            if resp.status_code == 201:
                bot_data = resp.json()
                # Bot response contains user_id field
                mm_user_id = bot_data.get("user_id")
                if mm_user_id:
                    self.entity.mattermost_id = mm_user_id
                    await self.set_status("success")
                    backend_logger.debug(
                        f"Бот {self.entity.slack_id} создан в Mattermost как Bot Account с user_id: {mm_user_id}"
                    )
                    await self._handle_avatar_upload(mm_user_id)
                    return
                else:
                    backend_logger.error(
                        f"Бот создан, но user_id не найден в ответе: {bot_data}"
                    )
                    await self.set_status("failed", error="user_id not in bot response")
                    return

            # Handle errors - including username conflicts
            data = resp.json() if hasattr(resp, "json") else {}
            error_id = data.get("id", "")
            error_msg = data.get("message", str(data))

            # Handle bot username already exists error by trying to retrieve existing bot
            if (
                "username" in error_msg.lower()
                or error_id == "store.sql_user.save.username_exists.app_error"
            ):
                backend_logger.debug(
                    f"Бот {username} уже существует, пытаюсь получить существующий"
                )
                # Try to find the bot again (may have been created outside the 200 bot limit)
                existing_bot_user_id = await self._find_existing_bot(username)
                if existing_bot_user_id:
                    self.entity.mattermost_id = existing_bot_user_id
                    await self.set_status("success")
                    await self._handle_avatar_upload(existing_bot_user_id)
                    return

            backend_logger.error(
                f"Ошибка создания бота {self.entity.slack_id}: {error_msg}; payload={payload}"
            )
            await self.set_status("failed", error=error_msg)

        except Exception as e:
            backend_logger.error(f"Ошибка экспорта бота {self.entity.slack_id}: {e}")
            await self.set_status("failed", error=str(e))

    async def _export_as_user(self):
        """Export Slack user as regular Mattermost user."""
        payload = self._build_mm_payload()
        try:
            # Fast-path reuse lookup (email then username) BEFORE create attempt
            email = payload.get("email")
            if email:
                try:
                    reuse_resp = await self.mm_api_get(f"/api/v4/users/email/{email}")
                    if hasattr(reuse_resp, "__await__") and not hasattr(
                        reuse_resp, "status_code"
                    ):
                        reuse_resp = await reuse_resp  # type: ignore
                    if reuse_resp.status_code == 200:
                        mm_id = reuse_resp.json().get("id")
                        if mm_id:
                            self.entity.mattermost_id = mm_id
                            await self.set_status("success")
                            await self._ensure_user_in_team(mm_id)
                            avatar_url = self._get_avatar_url(self.entity.raw_data)
                            if avatar_url:
                                await self._upload_avatar(mm_id, avatar_url)
                            return
                except Exception:
                    pass
            username = payload.get("username")
            if username:
                try:
                    reuse_u_resp = await self.mm_api_get(
                        f"/api/v4/users/username/{username}"
                    )
                    if hasattr(reuse_u_resp, "__await__") and not hasattr(
                        reuse_u_resp, "status_code"
                    ):
                        reuse_u_resp = await reuse_u_resp  # type: ignore
                    if reuse_u_resp.status_code == 200:
                        mm_id = reuse_u_resp.json().get("id")
                        if mm_id:
                            self.entity.mattermost_id = mm_id
                            await self.set_status("success")
                            await self._ensure_user_in_team(mm_id)
                            avatar_url = self._get_avatar_url(self.entity.raw_data)
                            if avatar_url:
                                await self._upload_avatar(mm_id, avatar_url)
                            return
                except Exception:
                    pass
            resp = await self.mm_api_post("/api/v4/users", payload)
            # Support both real httpx.Response and mocked coroutines accidentally returning coroutine objects
            if hasattr(resp, "__await__") and not hasattr(resp, "status_code"):
                # Await once more if a coroutine was injected incorrectly in tests
                resp = await resp  # type: ignore
            if resp.status_code == 201:
                mm_id = resp.json()["id"]
                self.entity.mattermost_id = mm_id
                await self.set_status("success")
                backend_logger.debug(
                    f"Пользователь {self.entity.slack_id} экспортирован в Mattermost"
                )
                # Автодобавление пользователя в команду
                await self._ensure_user_in_team(mm_id)
                # --- Загрузка аватарки ---
                avatar_url = self._get_avatar_url(self.entity.raw_data)
                if avatar_url:
                    await self._upload_avatar(mm_id, avatar_url)
                return
            data = resp.json() if hasattr(resp, "json") else {}
            err = data.get("id", "")
            if err == "app.user.save.email_exists.app_error":
                email = payload["email"]
                get_resp = await self.mm_api_get(f"/api/v4/users/email/{email}")
                if hasattr(get_resp, "__await__") and not hasattr(
                    get_resp, "status_code"
                ):
                    get_resp = await get_resp  # type: ignore
                if get_resp.status_code == 200:
                    mm_id = get_resp.json()["id"]
                    self.entity.mattermost_id = mm_id
                    await self.set_status("success")
                    backend_logger.debug(
                        f"Пользователь {self.entity.slack_id} экспортирован в Mattermost"
                    )
                    # Автодобавление пользователя в команду
                    await self._ensure_user_in_team(mm_id)
                    # --- Загрузка аватарки ---
                    avatar_url = self._get_avatar_url(self.entity.raw_data)
                    if avatar_url:
                        await self._upload_avatar(mm_id, avatar_url)
                    return
            if err == "app.user.save.username_exists.app_error":
                username = payload["username"]
                get_resp = await self.mm_api_get(f"/api/v4/users/username/{username}")
                if hasattr(get_resp, "__await__") and not hasattr(
                    get_resp, "status_code"
                ):
                    get_resp = await get_resp  # type: ignore
                if get_resp.status_code == 200:
                    mm_id = get_resp.json()["id"]
                    self.entity.mattermost_id = mm_id
                    await self.set_status("success")
                    backend_logger.debug(
                        f"Пользователь {self.entity.slack_id} экспортирован в Mattermost"
                    )
                    # Автодобавление пользователя в команду
                    await self._ensure_user_in_team(mm_id)
                    # --- Загрузка аватарки ---
                    avatar_url = self._get_avatar_url(self.entity.raw_data)
                    if avatar_url:
                        await self._upload_avatar(mm_id, avatar_url)
                    return
            backend_logger.error(
                f"Ошибка экспорта пользователя {self.entity.slack_id}: {data.get('message', str(data))}; payload={payload}"
            )
            await self.set_status("failed", error=data.get("message", str(data)))
        except Exception as e:
            backend_logger.error(
                f"Ошибка экспорта пользователя {self.entity.slack_id}: {e}"
            )
            await self.set_status("failed", error=str(e))

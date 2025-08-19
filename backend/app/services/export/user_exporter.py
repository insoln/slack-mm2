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
    async def _get_mm_team_id(self):
        """Resolve Mattermost team ID from env or via API."""
        import os
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
                    backend_logger.debug(f"Добавление пользователя {mm_user_id} в команду {team_id}: {resp.status_code} {data}")
                except Exception:
                    backend_logger.debug(f"Добавление пользователя {mm_user_id} в команду {team_id}: {resp.status_code} {resp.text}")
            else:
                backend_logger.debug(f"Пользователь {mm_user_id} добавлен в команду {team_id}")
        except Exception as e:
            backend_logger.error(f"Ошибка добавления пользователя {mm_user_id} в команду: {e}")

    def _get_avatar_url(self, raw_data):
        profile = (raw_data or {}).get("profile") or {}
        # Предпочтение: image_original > image_1024 > image_512 > ...
        for key in ["image_original", "image_1024", "image_512", "image_192", "image_72", "image_48", "image_32", "image_24"]:
            url = profile.get(key)
            if url and "secure.gravatar.com" not in url:
                return url
        return None

    async def _upload_avatar(self, mm_user_id, avatar_url):
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(avatar_url)
                if resp.status_code != 200:
                    backend_logger.error(f"Не удалось скачать аватарку: {avatar_url}, статус: {resp.status_code}")
                    return
                # Отправить в Mattermost
                files = {'image': ('avatar.png', resp.content, 'image/png')}
                mm_url = f"{os.environ['MM_URL']}/api/v4/users/{mm_user_id}/image"
                headers = {"Authorization": f"Bearer {os.environ['MM_TOKEN']}"}
                upload_resp = await client.post(mm_url, files=files, headers=headers)
                if upload_resp.status_code == 200:
                    backend_logger.debug(f"Аватарка пользователя {mm_user_id} успешно загружена в Mattermost")
                else:
                    backend_logger.error(f"Ошибка загрузки аватарки в Mattermost: {upload_resp.status_code}, {upload_resp.text}")
        except Exception as e:
            backend_logger.error(f"Ошибка при загрузке аватарки: {e}")

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
            payload["timezone"] = {
                "automaticTimezone": tz
            }
        return payload

    async def export_entity(self):
        self.log_export(f"Экспорт пользователя {self.entity.slack_id}")
        payload = self._build_mm_payload()
        try:
            resp = await self.mm_api_post("/api/v4/users", payload)
            if resp.status_code == 201:
                mm_id = resp.json()["id"]
                self.entity.mattermost_id = mm_id
                await self.set_status("success")
                backend_logger.debug(f"Пользователь {self.entity.slack_id} экспортирован в Mattermost")
                # Ручная политика: НЕ добавляем пользователя в команду автоматически
                # --- Загрузка аватарки ---
                avatar_url = self._get_avatar_url(self.entity.raw_data)
                if avatar_url:
                    await self._upload_avatar(mm_id, avatar_url)
                return
            data = resp.json()
            err = data.get("id", "")
            if err == "app.user.save.email_exists.app_error":
                email = payload["email"]
                get_resp = await self.mm_api_get(f"/api/v4/users/email/{email}")
                if get_resp.status_code == 200:
                    mm_id = get_resp.json()["id"]
                    self.entity.mattermost_id = mm_id
                    await self.set_status("success")
                    backend_logger.debug(f"Пользователь {self.entity.slack_id} экспортирован в Mattermost")
                    # Ручная политика: НЕ добавляем пользователя в команду автоматически
                    # --- Загрузка аватарки ---
                    avatar_url = self._get_avatar_url(self.entity.raw_data)
                    if avatar_url:
                        await self._upload_avatar(mm_id, avatar_url)
                    return
            if err == "app.user.save.username_exists.app_error":
                username = payload["username"]
                get_resp = await self.mm_api_get(f"/api/v4/users/username/{username}")
                if get_resp.status_code == 200:
                    mm_id = get_resp.json()["id"]
                    self.entity.mattermost_id = mm_id
                    await self.set_status("success")
                    backend_logger.debug(f"Пользователь {self.entity.slack_id} экспортирован в Mattermost")
                    # Ручная политика: НЕ добавляем пользователя в команду автоматически
                    # --- Загрузка аватарки ---
                    avatar_url = self._get_avatar_url(self.entity.raw_data)
                    if avatar_url:
                        await self._upload_avatar(mm_id, avatar_url)
                    return
            backend_logger.error(f"Ошибка экспорта пользователя {self.entity.slack_id}: {data.get('message', str(data))}; payload={payload}")
            await self.set_status("failed", error=data.get("message", str(data)))
        except Exception as e:
            backend_logger.error(f"Ошибка экспорта пользователя {self.entity.slack_id}: {e}")
            await self.set_status("failed", error=str(e)) 
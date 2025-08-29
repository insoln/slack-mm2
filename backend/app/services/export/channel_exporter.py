import os
import json
from app.logging_config import backend_logger
from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin


class ChannelExporter(ExporterBase, LoggingMixin, MMApiMixin):
    def __init__(self, entity):
        super().__init__(entity)
        self._cached_team_id = None

    def _get_channel_name(self, raw_data):
        """Получить название канала из raw_data"""
        return raw_data.get("name") if raw_data else None

    def _get_channel_display_name(self, raw_data):
        """Получить отображаемое название канала"""
        name = self._get_channel_name(raw_data)
        if not name:
            return None

        # Для DM каналов (начинаются с D) используем специальную логику
        if name.startswith("D"):
            return f"DM-{name}"

        # Для обычных каналов используем оригинальное название
        return name

    def _sanitize_display_name(
        self, display_name: str | None, fallback_name: str
    ) -> str:
        """Ограничить DisplayName до допустимых значений MM (<=64 символа, без переводов строк).
        Если не задано — используем fallback_name. Триммим пробелы, \n/\r заменяем на пробел.
        """
        val = display_name or fallback_name or "channel"
        if not isinstance(val, str):
            val = str(val)
        # Уберем переводы строк и контрольные символы (минимально)
        val = val.replace("\r", " ").replace("\n", " ").strip()
        # Порог 64 символа — обрежем по символам (unicode)
        if len(val) > 64:
            val = val[:64]
        # MM требует непустой DisplayName
        if not val:
            val = (fallback_name or "channel")[:64]
        return val

    def _get_channel_purpose(self, raw_data):
        """Получить описание канала"""
        purpose = raw_data.get("purpose", {}) if raw_data else {}
        return purpose.get("value", "") if purpose else ""

    def _get_channel_header(self, raw_data):
        """Получить заголовок канала"""
        topic = raw_data.get("topic", {}) if raw_data else {}
        return topic.get("value", "") if topic else ""

    def _is_dm_channel(self, raw_data):
        """Проверить, является ли канал DM"""
        return raw_data.get("id", "").startswith("D") if raw_data else False

    def _is_group_dm_channel(self, raw_data):
        """Проверить, является ли канал групповым DM (mpim в Slack)."""
        if not raw_data:
            return False
        # Slack экспорт помечает групповые диалоги флагом is_mpim
        if bool(raw_data.get("is_mpim")):
            return True
        # Фоллбек: иногда mpim приходит как канал с именем, начинающимся на 'mpdm-'
        nm = (raw_data or {}).get("name") or ""
        return nm.startswith("mpdm-")

    def _is_private_channel(self, raw_data):
        """Проверить, является ли канал приватным"""
        # В Slack приватные каналы имеют ID начинающийся с G
        return raw_data.get("id", "").startswith("G") if raw_data else False

    async def export_entity(self):
        # Сначала определяем тип канала и обрабатываем DM/GDM, где 'name' может отсутствовать
        self.log_export(f"Экспорт канала/диалога {self.entity.slack_id}")

        try:
            # Определяем тип канала
            is_dm = self._is_dm_channel(self.entity.raw_data)
            is_gdm = self._is_group_dm_channel(self.entity.raw_data)
            is_private = self._is_private_channel(self.entity.raw_data)

            # Для DM-каналов создаем/получаем канал через плагин /dm
            if is_dm:
                members = (self.entity.raw_data or {}).get("members") or []
                mm_user_ids = await self._resolve_mm_user_ids(members)
                if len(mm_user_ids) == 2:
                    dm_resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/dm",
                        {"user_ids": mm_user_ids},
                    )
                    if dm_resp.status_code in [200, 201]:
                        try:
                            dm_data = dm_resp.json()
                        except Exception:
                            backend_logger.error(
                                f"Плагин вернул не-JSON для DM: status={dm_resp.status_code} body={dm_resp.text[:200]}"
                            )
                            await self.set_status(
                                "failed",
                                error=f"Plugin invalid JSON for DM: {dm_resp.status_code}",
                            )
                            return
                        self.entity.mattermost_id = dm_data.get("channel_id")
                        await self.set_status("success")
                        backend_logger.debug(
                            f"DM канал создан/получен, ID: {self.entity.mattermost_id}"
                        )
                        return
                    else:
                        backend_logger.error(
                            f"Ошибка создания DM через плагин: {dm_resp.status_code} {dm_resp.text}"
                        )
                        await self.set_status("failed", error=dm_resp.text)
                        return
                else:
                    backend_logger.warn(
                        f"Ожидалось 2 участника DM, найдено {len(mm_user_ids)}; пропускаю"
                    )
                    await self.set_status("skipped", error="Invalid DM members count")
                    return

            # Для групповых DM (mpim) используем плагин /gdm
            if is_gdm:
                members = (self.entity.raw_data or {}).get("members") or []
                mm_user_ids = await self._resolve_mm_user_ids(members)
                # В mpim обычно >= 3 участников, но на всякий случай требуем хотя бы 2
                if len(mm_user_ids) >= 2:
                    gdm_resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/gdm",
                        {"user_ids": mm_user_ids},
                    )
                    if gdm_resp.status_code in [200, 201]:
                        try:
                            gdm_data = gdm_resp.json()
                        except Exception:
                            backend_logger.error(
                                f"Плагин вернул не-JSON для GDM: status={gdm_resp.status_code} body={gdm_resp.text[:200]}"
                            )
                            await self.set_status(
                                "failed",
                                error=f"Plugin invalid JSON for GDM: {gdm_resp.status_code}",
                            )
                            return
                        self.entity.mattermost_id = gdm_data.get("channel_id")
                        await self.set_status("success")
                        backend_logger.debug(
                            f"GDM канал создан/получен, ID: {self.entity.mattermost_id}"
                        )
                        return
                    else:
                        backend_logger.error(
                            f"Ошибка создания GDM через плагин: {gdm_resp.status_code} {gdm_resp.text}"
                        )
                        await self.set_status("failed", error=gdm_resp.text)
                        return
                else:
                    backend_logger.warn(
                        f"Слишком мало участников для GDM: {len(mm_user_ids)}; пропускаю"
                    )
                    await self.set_status("skipped", error="Insufficient GDM members")
                    return

            # Обычные каналы (публичные/приватные) требуют имени
            channel_name = self._get_channel_name(self.entity.raw_data)
            if not channel_name:
                backend_logger.error(f"Нет названия для канала {self.entity.slack_id}")
                await self.set_status(
                    "failed",
                    error="No channel name found in raw_data for non-DM channel",
                )
                return

            self.log_export(f"Экспорт канала {channel_name}")

            # Определяем team_id надёжно (ENV MM_TEAM_ID или по имени MM_TEAM через API)
            team_id = await self._get_mm_team_id()

            # Строим payload для создания канала
            # Подготовим безопасный display_name
            safe_display = self._sanitize_display_name(
                self._get_channel_display_name(self.entity.raw_data),
                channel_name.replace("-", " "),
            )

            payload = {
                "team_id": team_id,
                "name": channel_name,
                "display_name": safe_display,
                "type": "P" if is_private else "O",  # P - приватный, O - публичный
            }

            # Добавляем описание и заголовок если есть
            purpose = self._get_channel_purpose(self.entity.raw_data)
            if purpose:
                payload["purpose"] = purpose

            header = self._get_channel_header(self.entity.raw_data)
            if header:
                payload["header"] = header

            # Создаем/получаем канал через плагин (включает нормализацию имени)
            response = await self.mm_api_post(
                "/plugins/mm-importer/api/v1/channel", payload
            )

            if response.status_code in [200, 201]:
                try:
                    channel_data = response.json()
                except Exception:
                    backend_logger.error(
                        f"Плагин вернул не-JSON для channel: status={response.status_code} body={response.text[:200]}"
                    )
                    await self.set_status(
                        "failed",
                        error=f"Plugin invalid JSON for channel: {response.status_code}",
                    )
                    return
                # Плагин возвращает { channel_id }
                self.entity.mattermost_id = channel_data.get(
                    "channel_id"
                ) or channel_data.get("id")

                # Добавляем участников, если они есть в raw_data
                members = (self.entity.raw_data or {}).get("members") or []
                if members:
                    # Нужно замапить Slack user_id -> Mattermost user_id из Entity
                    mm_user_ids = await self._resolve_mm_user_ids(members)
                    if mm_user_ids:
                        # Автоматически гарантируем членство пользователей в команде перед добавлением в канал
                        await self._ensure_team_membership(mm_user_ids)
                        add_resp = await self.mm_api_post(
                            "/plugins/mm-importer/api/v1/channel/members",
                            {
                                "channel_id": self.entity.mattermost_id,
                                "user_ids": mm_user_ids,
                            },
                        )
                        if add_resp.status_code not in [200, 201]:
                            backend_logger.error(
                                f"Не удалось добавить участников: {add_resp.status_code} {add_resp.text}"
                            )

                # Архивируем, если канал в Slack был архивирован
                if (self.entity.raw_data or {}).get("is_archived"):
                    arch_resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/channel/archive",
                        {"channel_id": self.entity.mattermost_id},
                    )
                    if arch_resp.status_code not in [200, 201]:
                        backend_logger.error(
                            f"Не удалось архивировать канал: {arch_resp.status_code} {arch_resp.text}"
                        )

                await self.set_status("success")
                backend_logger.debug(
                    f"Канал {channel_name} экспортирован в Mattermost, ID: {self.entity.mattermost_id}"
                )
                return

            # Проверяем ошибки дублирования
            data = response.json()
            backend_logger.error(
                f"Ошибка создания канала через плагин: {response.status_code}, {data}"
            )
            await self.set_status(
                "failed", error=data.get("error") or data.get("message") or str(data)
            )

        except Exception as e:
            backend_logger.error(f"Ошибка при создании канала: {e}")
            await self.set_status("failed", error=str(e))

    async def _resolve_mm_user_ids(self, slack_user_ids):
        """Получить Mattermost ID для списка Slack user ids из таблицы Entity."""
        from app.models.base import SessionLocal
        from sqlalchemy import select
        from app.models.entity import Entity

        mm_ids = []
        async with SessionLocal() as session:
            for sid in slack_user_ids:
                q = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "user") & (Entity.slack_id == sid)
                    )
                )
                ent = q.scalar_one_or_none()
                if ent is not None:
                    mm_id = getattr(ent, "mattermost_id", None)
                    if mm_id:
                        mm_ids.append(mm_id)
                else:
                    backend_logger.warn(f"MM user id not found for Slack user {sid}")
        return mm_ids

    async def _get_mm_team_id(self):
        """Определить ID команды Mattermost:
        - если задан MM_TEAM_ID — используем его
        - иначе получаем по имени MM_TEAM через API /api/v4/teams/name/{name}
        Кэшируем результат на время жизни экспортера.
        """
        if self._cached_team_id:
            return self._cached_team_id
        env_team_id = os.environ.get("MM_TEAM_ID")
        if env_team_id:
            self._cached_team_id = env_team_id
            return env_team_id
        team_name = os.environ.get("MM_TEAM", "test")
        try:
            resp = await self.mm_api_get(f"/api/v4/teams/name/{team_name}")
            if resp.status_code == 200:
                data = resp.json()
                tid = data.get("id")
                if tid:
                    self._cached_team_id = tid
                    return tid
            backend_logger.error(
                f"Не удалось получить team id по имени '{team_name}': {resp.status_code} {resp.text}"
            )
        except Exception as e:
            backend_logger.error(f"Ошибка при получении team id: {e}")
        # Fallback на ранее используемый тестовый ID
        return "b7u9rycm43nip86mdiuqsxdcbe"

    async def _ensure_team_membership(self, mm_user_ids):
        """Ensure all provided Mattermost users are members of the team before adding to channels."""
        team_id = await self._get_mm_team_id()
        for uid in mm_user_ids:
            try:
                resp = await self.mm_api_post(
                    f"/api/v4/teams/{team_id}/members",
                    {"team_id": team_id, "user_id": uid},
                )
                if resp.status_code not in (200, 201):
                    # Server may respond with an error if already a member; log for trace and continue
                    try:
                        data = resp.json()
                        backend_logger.debug(
                            f"ensure team member resp for user {uid}: {resp.status_code} {data}"
                        )
                    except Exception:
                        backend_logger.debug(
                            f"ensure team member resp for user {uid}: {resp.status_code} {resp.text}"
                        )
            except Exception as e:
                backend_logger.error(
                    f"Ошибка добавления пользователя {uid} в команду {team_id}: {e}"
                )

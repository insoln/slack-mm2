import os
import json
from app.logging_config import backend_logger
from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin

class ChannelExporter(ExporterBase, LoggingMixin, MMApiMixin):
    def __init__(self, entity):
        super().__init__(entity)

    def _get_channel_name(self, raw_data):
        """Получить название канала из raw_data"""
        return raw_data.get("name") if raw_data else None

    def _get_channel_display_name(self, raw_data):
        """Получить отображаемое название канала"""
        name = self._get_channel_name(raw_data)
        if not name:
            return None
        
        # Для DM каналов (начинаются с D) используем специальную логику
        if name.startswith('D'):
            return f"DM-{name}"
        
        # Для обычных каналов используем оригинальное название
        return name

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
        return raw_data.get("id", "").startswith('D') if raw_data else False

    def _is_private_channel(self, raw_data):
        """Проверить, является ли канал приватным"""
        # В Slack приватные каналы имеют ID начинающийся с G
        return raw_data.get("id", "").startswith('G') if raw_data else False

    async def export_entity(self):
        channel_name = self._get_channel_name(self.entity.raw_data)
        if not channel_name:
            backend_logger.error(f"Нет названия для канала {self.entity.slack_id}")
            await self.set_status("failed", error="No channel name found in raw_data")
            return

        self.log_export(f"Экспорт канала {channel_name}")

        try:
            # Определяем тип канала
            is_dm = self._is_dm_channel(self.entity.raw_data)
            is_private = self._is_private_channel(self.entity.raw_data)

            # Для DM каналов Mattermost создает автоматически, пропускаем
            if is_dm:
                backend_logger.debug(f"DM канал {channel_name} пропущен - Mattermost создает автоматически")
                await self.set_status("success")
                return

            # Строим payload для создания канала
            payload = {
                "team_id": os.environ.get("MM_TEAM_ID", "b7u9rycm43nip86mdiuqsxdcbe"),  # ID команды из init-mattermost.sql
                "name": channel_name,
                "display_name": self._get_channel_display_name(self.entity.raw_data),
                "type": "P" if is_private else "O",  # P - приватный, O - публичный
            }

            # Добавляем описание и заголовок если есть
            purpose = self._get_channel_purpose(self.entity.raw_data)
            if purpose:
                payload["purpose"] = purpose

            header = self._get_channel_header(self.entity.raw_data)
            if header:
                payload["header"] = header

            # Создаем канал в Mattermost
            response = await self.mm_api_post("/api/v4/channels", payload)

            if response.status_code in [200, 201]:
                channel_data = response.json()
                self.entity.mattermost_id = channel_data.get("id")
                await self.set_status("success")
                backend_logger.debug(f"Канал {channel_name} экспортирован в Mattermost, ID: {self.entity.mattermost_id}")
                return

            # Проверяем ошибки дублирования
            data = response.json()
            err = data.get("id", "")
            if err == "store.sql_channel.save_channel.exists.app_error":
                # Канал уже существует, получаем его ID
                get_resp = await self.mm_api_get(f"/api/v4/teams/{payload['team_id']}/channels/name/{channel_name}")
                if get_resp.status_code == 200:
                    channel_data = get_resp.json()
                    self.entity.mattermost_id = channel_data.get("id")
                    await self.set_status("success")
                    backend_logger.debug(f"Канал {channel_name} уже существует в Mattermost, ID: {self.entity.mattermost_id}")
                    return

            backend_logger.error(f"Ошибка создания канала в Mattermost: {response.status_code}, {response.text}")
            await self.set_status("failed", error=data.get("message", str(data)))

        except Exception as e:
            backend_logger.error(f"Ошибка при создании канала: {e}")
            await self.set_status("failed", error=str(e)) 
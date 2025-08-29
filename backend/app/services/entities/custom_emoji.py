# custom_emoji.py
# Сущность кастомного эмодзи Slack
import os
import httpx
from .base_mixin import BaseMapping
from app.logging_config import backend_logger


class CustomEmoji(BaseMapping):
    entity_type = "custom_emoji"

    @classmethod
    def from_entity(cls, entity):
        obj = cls(
            slack_id=entity.slack_id,
            mattermost_id=entity.mattermost_id,
            raw_data=entity.raw_data,
            status=entity.status,
            auto_save=False,
        )
        obj.id = entity.id
        return obj


async def get_slack_emoji_list():
    """Получить список всех кастомных эмодзи из Slack API"""
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    if not slack_token:
        backend_logger.warning(
            "SLACK_BOT_TOKEN не настроен, пропускаю получение списка эмодзи"
        )
        return {}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://slack.com/api/emoji.list",
                headers={"Authorization": f"Bearer {slack_token}"},
            )
            if resp.status_code != 200:
                backend_logger.error(f"Ошибка Slack API: {resp.status_code}")
                return {}

            data = resp.json()
            if not data.get("ok"):
                backend_logger.error(f"Slack API ошибка: {data.get('error')}")
                return {}

            emoji_list = data.get("emoji", {})
            backend_logger.info(
                f"Получен список эмодзи из Slack API: {len(emoji_list)} эмодзи"
            )
            return emoji_list
    except Exception as e:
        backend_logger.error(f"Ошибка при получении списка эмодзи из Slack API: {e}")
        return {}

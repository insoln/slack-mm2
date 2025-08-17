from app.services.entities.attachment import Attachment
from app.logging_config import backend_logger
from app.models.base import SessionLocal

async def parse_attachments_from_messages(export_dir, message_entities):
    attachments = []
    for msg in message_entities:
        raw = msg.raw_data or {}
        message_ts = raw.get("ts")
        files = raw.get("files") or []
        for file_obj in files:
            slack_id = file_obj.get("id")
            url_private = file_obj.get("url_private")
            if not slack_id:
                continue
            if not (url_private and url_private.startswith("https://files.slack.com")):
                continue
            attachment = Attachment(
                slack_id=slack_id,
                mattermost_id=None,
                raw_data=file_obj,
                status="pending",
                auto_save=False
            )
            attachments.append((attachment, message_ts))
    # Сохраняем все Attachment
    for attachment, _ in attachments:
        await attachment.save_to_db()
    # Создаём связи attached_to
    for attachment, message_ts in attachments:
        await attachment.create_attached_to_relation(message_ts)
    backend_logger.info(f"Импортировано аттачментов: {len(attachments)}") 
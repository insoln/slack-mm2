from app.services.entities.attachment import Attachment
from app.logging_config import backend_logger
import os
import glob
import ijson

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


async def parse_attachments_from_export(export_dir: str, folder_channel_map: dict) -> int:
    """Stream files in export and create attachment entities/relations incrementally."""
    total = 0
    for folder, _ in folder_channel_map.items():
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                with open(msg_file, 'r', encoding='utf-8') as f:
                    for msg in ijson.items(f, 'item'):
                        raw = msg or {}
                        message_ts = raw.get("ts")
                        for file_obj in raw.get("files") or []:
                            slack_id = file_obj.get("id")
                            url_private = file_obj.get("url_private")
                            if not slack_id or not (url_private and url_private.startswith("https://files.slack.com")):
                                continue
                            attachment = Attachment(slack_id=slack_id, mattermost_id=None, raw_data=file_obj, status="pending", auto_save=False)
                            ent = await attachment.save_to_db()
                            if ent is not None:
                                await attachment.create_attached_to_relation(message_ts)
                                total += 1
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file} при сборе аттачментов: {e}")
                continue
    backend_logger.info(f"Импортировано аттачментов из экспорта: {total}")
    return total
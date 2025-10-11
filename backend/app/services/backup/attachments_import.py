from typing import List, Optional, Tuple

from app.services.entities.attachment import Attachment
from app.logging_config import backend_logger


async def parse_attachments_from_messages(export_dir, message_entities):  # pragma: no cover - legacy
    """Previously used to extract Attachment entities from already imported messages.

    The unified importer now persists attachments inline; this function is kept only
    for backward compatibility in case external tooling still calls it.
    """
    attachments: List[Tuple[Attachment, Optional[str]]] = []
    for msg in message_entities:
        raw = getattr(msg, "raw_data", {}) or {}
        message_ts = raw.get("ts")
        for file_obj in raw.get("files") or []:
            slack_id = file_obj.get("id")
            url_private = file_obj.get("url_private")
            if not slack_id or not (
                url_private and url_private.startswith("https://files.slack.com")
            ):
                continue
            attachment = Attachment(
                slack_id=slack_id,
                mattermost_id=None,
                raw_data=file_obj,
                status="pending",
                auto_save=False,
                job_id=getattr(msg, "job_id", None),
            )
            attachments.append((attachment, message_ts))
    saved = 0
    for attachment, msg_ts in attachments:
        try:
            ent = await attachment.save_to_db()
            if ent is not None:
                await attachment.create_attached_to_relation(msg_ts)
                saved += 1
        except Exception:  # pragma: no cover
            pass
    backend_logger.info(
        f"(legacy) Импортировано аттачментов (из сообщений) : {saved}/{len(attachments)}"
    )
    return saved


async def parse_attachments_from_export(*_args, **_kwargs):  # pragma: no cover - deprecated
    backend_logger.warning(
        "parse_attachments_from_export deprecated: attachments handled in unified messages stage"
    )
    return 0

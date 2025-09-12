from app.services.entities.attachment import Attachment
from app.logging_config import backend_logger
import os
import glob
import ijson
# Uses same batching/orjson strategy as messages & reactions import.
try:
    import orjson  # type: ignore  # optional fast path
except Exception:  # pragma: no cover
    orjson = None  # type: ignore
from typing import Callable, Awaitable, Optional, List, Tuple
from app.models.base import SessionLocal
from app.models.entity import Entity
from sqlalchemy import select


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
                auto_save=False,
                job_id=getattr(msg, "job_id", None),
            )
            attachments.append((attachment, message_ts))
    # Сохраняем все Attachment
    for attachment, _ in attachments:
        await attachment.save_to_db()
    # Создаём связи attached_to
    for attachment, message_ts in attachments:
        await attachment.create_attached_to_relation(message_ts)
    backend_logger.info(f"Импортировано аттачментов: {len(attachments)}")


async def parse_attachments_from_export(
    export_dir: str,
    folder_channel_map: dict,
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
    batch_size: int | None = None,
) -> int:
    """Stream files in export and create attachment entities/relations incrementally with optional batching."""
    if batch_size in (None, 0):
        try:
            env_batch = int(os.environ.get("IMPORT_BATCH_SIZE", "0") or 0)
            batch_size = env_batch if env_batch > 0 else 0
        except Exception:
            batch_size = 0
    batch_mode = batch_size and batch_size > 1
    total = 0
    # message_ts may be None if ts missing; allow Optional[str]
    batch_attachments: List[Tuple[Attachment, Optional[str]]] = []

    async def flush_attachments(force: bool = False):
        nonlocal batch_attachments, total
        if not batch_mode:
            return
        if not force and len(batch_attachments) < batch_size:
            return
        if not batch_attachments:
            return
        bulk_ok = True
        async with SessionLocal() as session:
            try:
                values_sql_parts = []
                params = {}
                for idx, (a, _msg_ts) in enumerate(batch_attachments):
                    values_sql_parts.append(
                        f"(:entity_type{idx}, :slack_id{idx}, :mattermost_id{idx}, :raw_data{idx}::jsonb, :job_id{idx}, :status{idx}, :error_message{idx})"
                    )
                    params[f"entity_type{idx}"] = getattr(a, 'entity_type', 'attachment')
                    params[f"slack_id{idx}"] = getattr(a, 'slack_id', None)
                    params[f"mattermost_id{idx}"] = getattr(a, 'mattermost_id', None)
                    params[f"raw_data{idx}"] = getattr(a, 'raw_data', None)
                    params[f"job_id{idx}"] = getattr(a, 'job_id', None)
                    params[f"status{idx}"] = getattr(a, 'status', 'pending')
                    params[f"error_message{idx}"] = None
                if values_sql_parts:
                    from sqlalchemy import text as _text
                    sql = f"""
                        INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status, error_message)
                        VALUES {', '.join(values_sql_parts)}
                        ON CONFLICT (entity_type, slack_id, job_id) DO NOTHING
                    """
                    await session.execute(_text(sql), params)
                await session.commit()
            except Exception as e:
                backend_logger.error(f"Bulk insert attachments failed, fallback to row mode: {e}")
                bulk_ok = False
        if not bulk_ok:
            for a, _msg_ts in batch_attachments:
                try:
                    await a.save_to_db()
                except Exception as e:  # pragma: no cover
                    backend_logger.error(f"Fallback save attachment error {getattr(a,'slack_id',None)}: {e}")
        for a, msg_ts in batch_attachments:
            try:
                await a.create_attached_to_relation(msg_ts)
            except Exception as e:
                backend_logger.error(f"Relation creation error for attachment {getattr(a,'slack_id',None)}: {e}")
        if progress:
            try:
                await progress(len(batch_attachments))
            except Exception:
                pass
        batch_attachments = []
    try:
        orjson_threshold_kb = int(os.environ.get("IMPORT_ORJSON_THRESHOLD_KB", "0") or 0)
    except Exception:
        orjson_threshold_kb = 0

    for folder, _ in folder_channel_map.items():
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                file_size = 0
                try:
                    file_size = os.path.getsize(msg_file)
                except Exception:
                    pass
                use_fast_path = (
                    orjson is not None and orjson_threshold_kb > 0 and file_size > 0 and file_size <= orjson_threshold_kb * 1024
                )
                if use_fast_path:
                    try:
                        with open(msg_file, "rb") as bf:
                            data = bf.read()
                        parsed = orjson.loads(data)  # type: ignore[union-attr]
                        if not isinstance(parsed, list):
                            use_fast_path = False
                        else:
                            iterator = iter(parsed)
                    except Exception:
                        use_fast_path = False
                if not use_fast_path:
                    f = open(msg_file, "r", encoding="utf-8")
                    iterator = ijson.items(f, "item")  # type: ignore
                try:
                    for msg in iterator:  # type: ignore
                        raw = msg or {}
                        message_ts = raw.get("ts")
                        for file_obj in raw.get("files") or []:
                            slack_id = file_obj.get("id")
                            url_private = file_obj.get("url_private")
                            if not slack_id or not (
                                url_private
                                and url_private.startswith("https://files.slack.com")
                            ):
                                continue
                            attachment = Attachment(
                                slack_id=slack_id,
                                mattermost_id=None,
                                raw_data=file_obj,
                                status="pending",
                                auto_save=False,
                                job_id=job_id,
                            )
                            if batch_mode:
                                batch_attachments.append((attachment, message_ts))
                                if len(batch_attachments) >= batch_size:
                                    await flush_attachments(force=True)
                                total += 1
                            else:
                                ent = await attachment.save_to_db()
                                if ent is not None:
                                    await attachment.create_attached_to_relation(message_ts)
                                    total += 1
                                    if progress:
                                        await progress(1)
                finally:
                    try:
                        if not use_fast_path:
                            f.close()  # type: ignore
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(
                    f"Ошибка чтения {msg_file} при сборе аттачментов: {e}"
                )
                continue
    if batch_mode:
        await flush_attachments(force=True)
    backend_logger.info(f"Импортировано аттачментов из экспорта: {total}")
    return total

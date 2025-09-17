import os
import glob
import ijson
import re
import time
from typing import Awaitable, Callable, Optional, List, Tuple

try:  # Optional fast JSON for small files
    import orjson  # type: ignore
except Exception:  # pragma: no cover
    orjson = None  # type: ignore

from app.logging_config import backend_logger
from app.services.entities.message import Message
from app.services.entities.reaction import Reaction
from app.services.entities.attachment import Attachment
from app.services.entities.custom_emoji import CustomEmoji
from app.models.base import SessionLocal
from sqlalchemy import text as _text


async def parse_channel_messages(
    export_dir,
    folder_channel_map,
    batch_size: int = 1000,
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
    file_progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
    single_pass: bool | None = None,
    counters_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    emoji_list: Optional[dict] = None,
):
    """Stream parse Slack export channel message files.

    Single pass mode persists messages + reactions + attachments and tracks emojis.
    counters_callback receives PER-BATCH DELTAS (messages, reactions, attachments) and current unique emoji count.
    """
    if single_pass is None:
        single_pass = os.environ.get("IMPORT_SINGLE_PASS", "0") in ("1", "true", "TRUE")

    EMOJI_PATTERN = re.compile(r":([a-z0-9_+\-]+):")

    # Allow env overrides
    try:
        env_batch = int(os.environ.get("IMPORT_BATCH_SIZE", "0") or 0)
        if env_batch > 0:
            batch_size = env_batch
    except Exception:
        pass
    meta_interval_sec = float(os.environ.get("IMPORT_META_UPDATE_INTERVAL_SEC", "2"))
    meta_every = int(os.environ.get("IMPORT_META_UPDATE_EVERY", str(batch_size)))

    saved_count = 0
    reactions_count = 0
    attachments_count = 0
    emojis_seen: set[str] = set()
    last_emitted_reactions = 0
    last_emitted_attachments = 0

    batch_messages: List[Message] = []
    batch_reactions: List[Reaction] = []
    batch_attachments: List[Tuple[Attachment, str]] = []
    last_meta_emit = time.time()

    async def flush_batch(force: bool = False):
        nonlocal last_meta_emit, last_emitted_reactions, last_emitted_attachments
        if not force and len(batch_messages) < batch_size:
            return
        if not batch_messages and not batch_reactions and not batch_attachments:
            return

        batch_mode_ok = True
        async with SessionLocal() as session:
            try:
                def _row(entity_type, slack_id, raw_data, job):
                    return {
                        "entity_type": entity_type,
                        "slack_id": slack_id,
                        "mattermost_id": None,
                        "raw_data": raw_data,
                        "job_id": job,
                        "status": "pending",
                        "error_message": None,
                    }

                msg_rows = [
                    _row(getattr(m, "entity_type", "message"), getattr(m, "slack_id", None), getattr(m, "raw_data", None), getattr(m, "job_id", None))
                    for m in batch_messages
                ]
                react_rows = []
                for r in batch_reactions:
                    rd = getattr(r, "raw_data", None)
                    if isinstance(rd, dict) and "ts" not in rd and getattr(r, "slack_id", None):
                        try:
                            rd["ts"] = str(getattr(r, "slack_id")).split("_")[0]
                        except Exception:
                            pass
                    react_rows.append(_row(getattr(r, "entity_type", "reaction"), getattr(r, "slack_id", None), rd, getattr(r, "job_id", None)))
                attach_rows = [
                    _row(getattr(a, "entity_type", "attachment"), getattr(a, "slack_id", None), getattr(a, "raw_data", None), getattr(a, "job_id", None))
                    for a, _ in batch_attachments
                ]
                all_rows = msg_rows + react_rows + attach_rows
                if all_rows:
                    values_sql_parts = []
                    params = {}
                    for idx, row in enumerate(all_rows):
                        values_sql_parts.append(
                            f"(:entity_type{idx}, :slack_id{idx}, :mattermost_id{idx}, :raw_data{idx}::jsonb, :job_id{idx}, :status{idx}, :error_message{idx})"
                        )
                        for k, v in row.items():
                            params[f"{k}{idx}"] = v
                    sql = f"""
                        INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status, error_message)
                        VALUES {', '.join(values_sql_parts)}
                        ON CONFLICT (entity_type, slack_id, job_id) DO NOTHING
                    """
                    await session.execute(_text(sql), params)
                await session.commit()
            except Exception as e:
                backend_logger.error(f"Bulk insert failure, fallback to row mode: {e}")
                batch_mode_ok = False

        # Relations (and row-mode fallback for messages)
        for m in batch_messages:
            try:
                if not batch_mode_ok:
                    ch_id = getattr(m, "_channel_id", None)
                    if ch_id is None:
                        try:
                            raw_data = getattr(m, "raw_data", {}) or {}
                            if isinstance(raw_data, dict):
                                ch_id = raw_data.get("channel_id")
                        except Exception:
                            ch_id = None
                    try:
                        await m.save_to_db(ch_id)
                    except Exception:
                        pass
                raw = getattr(m, "raw_data", None)
                ch_arg = raw.get("channel_id") if isinstance(raw, dict) else getattr(m, "_channel_id", None)
                await m.create_posted_in_relation(ch_arg)
                await m.create_posted_by_relation()
                await m.create_thread_relation()
            except Exception as e:
                backend_logger.error(f"Связи для сообщения {getattr(m, 'slack_id', None)}: {e}")
        for r in batch_reactions:
            try:
                await r.create_reacted_by_relation()
                await r.create_reacted_to_relation()
            except Exception as e:
                backend_logger.error(f"Связи для реакции {getattr(r, 'slack_id', None)}: {e}")
        for a, msg_ts in batch_attachments:
            try:
                await a.create_attached_to_relation(msg_ts)
            except Exception as e:
                backend_logger.error(f"Связи для аттачмента {getattr(a, 'slack_id', None)}: {e}")

        emitted_msgs = len(batch_messages)
        if emitted_msgs and progress:
            try:
                await progress(emitted_msgs)
            except Exception:
                pass
        if single_pass and counters_callback:
            delta_reactions = reactions_count - last_emitted_reactions
            delta_attachments = attachments_count - last_emitted_attachments
            last_emitted_reactions = reactions_count
            last_emitted_attachments = attachments_count
            if delta_reactions or delta_attachments or emitted_msgs:
                try:
                    await counters_callback(
                        {
                            "messages": emitted_msgs,
                            "reactions": max(0, delta_reactions),
                            "attachments": max(0, delta_attachments),
                            "emojis": len(emojis_seen),
                        }
                    )
                except Exception:
                    pass

        batch_messages.clear()
        batch_reactions.clear()
        batch_attachments.clear()
        nonlocal saved_count  # allow maybe_emit_meta to trigger repeated saves
        last_meta_emit = time.time()

    def maybe_emit_meta():
        return (time.time() - last_meta_emit) >= meta_interval_sec or (len(batch_messages) >= meta_every)

    # Fast-path threshold
    try:
        orjson_threshold_kb = int(os.environ.get("IMPORT_ORJSON_THRESHOLD_KB", "0") or 0)
    except Exception:
        orjson_threshold_kb = 0  # type: ignore

    for folder, channel in folder_channel_map.items():
        backend_logger.debug(
            f"Обработка папки: {folder}, канал: {(channel.get('name') if channel else None) or (channel.get('slack_id') if channel else None)}"
        )
        if not channel:
            backend_logger.debug(f"Пропуск папки {folder}: канал не найден")
            continue
        channel_id = channel["id"]
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            backend_logger.debug(f"Пропуск: {folder_path} не является директорией")
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            backend_logger.debug(f"Чтение файла сообщений: {msg_file}")
            try:
                try:
                    file_size = os.path.getsize(msg_file)
                except Exception:
                    file_size = 0
                use_fast_path = (
                    orjson is not None and file_size > 0 and "orjson_threshold_kb" in locals() and orjson_threshold_kb > 0 and file_size <= orjson_threshold_kb * 1024
                )
                if use_fast_path:
                    try:
                        with open(msg_file, "rb") as bf:
                            raw_bytes = bf.read()
                        parsed = orjson.loads(raw_bytes)  # type: ignore[union-attr]
                        if not isinstance(parsed, list):
                            backend_logger.debug("Ожидался JSON-массив сообщений, получен другой тип — fallback ijson")
                            use_fast_path = False
                        else:
                            iterator = iter(parsed)
                    except Exception as e:  # fallback
                        backend_logger.debug(f"orjson fast path error ({msg_file}): {e}; fallback to streaming")
                        use_fast_path = False
                if not use_fast_path:
                    f = open(msg_file, "r", encoding="utf-8")
                    iterator = ijson.items(f, "item")  # type: ignore
                try:
                    for msg in iterator:  # type: ignore
                        try:
                            slack_id = (msg or {}).get("ts")
                            if not slack_id:
                                continue
                            message_entity = Message(
                                slack_id=slack_id,
                                mattermost_id=None,
                                raw_data=msg,
                                status="pending",
                                auto_save=False,
                                job_id=job_id,
                            )
                            if isinstance(message_entity.raw_data, dict):  # enrich channel id
                                message_entity.raw_data.setdefault("channel_id", channel_id)
                            try:
                                setattr(message_entity, "_channel_id", channel_id)
                            except Exception:
                                pass
                            batch_messages.append(message_entity)
                            saved_count += 1

                            if single_pass:
                                # Reactions
                                for reaction in (msg or {}).get("reactions") or []:
                                    rname = reaction.get("name")
                                    if not rname:
                                        continue
                                    for user_id in reaction.get("users") or []:
                                        reaction_data = dict(reaction)
                                        reaction_data["user"] = user_id
                                        reaction_data["message_ts"] = slack_id
                                        reaction_data["emoji_name"] = rname
                                        reaction_data["composite_id"] = f"{slack_id}_{rname}"
                                        reaction_entity = Reaction(
                                            slack_id=f"{slack_id}_{rname}_{user_id}",
                                            mattermost_id=None,
                                            raw_data=reaction_data,
                                            status="pending",
                                            auto_save=False,
                                            job_id=job_id,
                                        )
                                        batch_reactions.append(reaction_entity)
                                        reactions_count += 1
                                # Attachments
                                for file_obj in (msg or {}).get("files") or []:
                                    slack_file_id = file_obj.get("id")
                                    url_private = file_obj.get("url_private")
                                    if not slack_file_id or not (url_private and url_private.startswith("https://files.slack.com")):
                                        continue
                                    attachment = Attachment(
                                        slack_id=slack_file_id,
                                        mattermost_id=None,
                                        raw_data=file_obj,
                                        status="pending",
                                        auto_save=False,
                                        job_id=job_id,
                                    )
                                    batch_attachments.append((attachment, slack_id))
                                    attachments_count += 1
                                # Emojis discovery
                                if emoji_list:
                                    text = (msg or {}).get("text") or ""
                                    for name in EMOJI_PATTERN.findall(text):
                                        if emoji_list.get(name):
                                            emojis_seen.add(name)
                                    for a in (msg or {}).get("attachments") or []:
                                        for key in ("pretext", "title", "text", "fallback"):
                                            val = a.get(key)
                                            if isinstance(val, str):
                                                for name in EMOJI_PATTERN.findall(val):
                                                    if emoji_list.get(name):
                                                        emojis_seen.add(name)
                                    for b in (msg or {}).get("blocks") or []:
                                        if isinstance(b, dict):
                                            if b.get("type") == "rich_text":
                                                for el in b.get("elements", []) or []:
                                                    if isinstance(el, dict) and el.get("type") in ("text", "mrkdwn", "plain_text"):
                                                        t = el.get("text") or ""
                                                        for name in EMOJI_PATTERN.findall(t):
                                                            if emoji_list.get(name):
                                                                emojis_seen.add(name)
                                            else:
            										# plain text block variant
                                                t_obj = b.get("text")
                                                t = (t_obj or {}).get("text") if isinstance(t_obj, dict) else None
                                                if t:
                                                    for name in EMOJI_PATTERN.findall(t):
                                                        if emoji_list.get(name):
                                                            emojis_seen.add(name)

                            if len(batch_messages) >= batch_size or maybe_emit_meta():
                                await flush_batch(force=True)
                        except Exception as ie:
                            backend_logger.error(f"Ошибка при сохранении сообщения из {msg_file}: {ie}")
                finally:
                    try:
                        if not use_fast_path:
                            f.close()  # type: ignore
                    except Exception:
                        pass
                if file_progress:
                    try:
                        await file_progress(1)
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file}: {e}")
                continue

    await flush_batch(force=True)
    backend_logger.info(f"Импортировано сообщений: {saved_count}")

    created_emojis = 0
    if single_pass and emojis_seen:
        for name in sorted(emojis_seen):
            emoji_entity = CustomEmoji(
                slack_id=name,
                raw_data={"name": name, "url": emoji_list.get(name) if emoji_list else None},
                status="pending",
                auto_save=False,
            )
            ent_e = await emoji_entity.save_to_db()
            if ent_e is not None:
                created_emojis += 1

    return {
        "messages": saved_count,
        "reactions": reactions_count,
        "attachments": attachments_count,
        "emojis": created_emojis if created_emojis else len(emojis_seen),
    }


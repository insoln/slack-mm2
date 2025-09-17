import os
import glob
import json
import re
import time
import asyncio
from typing import Awaitable, Callable, Optional, Set, Any

from app.services.entities.message import Message
from app.services.entities.attachment import Attachment
from app.services.entities.reaction import Reaction
from app.services.entities.custom_emoji import CustomEmoji
from app.logging_config import backend_logger
from app.services.backup.progress_tracker import make_tracker

EMOJI_PATTERN = re.compile(r":([a-z0-9_+\-]+):")

__all__ = ["parse_messages_and_related"]


async def parse_messages_and_related(
    export_dir,
    folder_channel_map,
    emoji_list: Optional[dict] = None,
    batch_log_every: int = 1000,
    message_bulk_size: Optional[int] = None,
    reaction_bulk_size: Optional[int] = None,
    attachment_bulk_size: Optional[int] = None,
    progress_messages: Optional[Callable[[int], Awaitable[None]]] = None,
    file_progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
):
    """Single-pass import of messages + reactions + attachments + custom emojis.

    No streaming library (ijson) – assume daily files are reasonably sized.
    Minimizes file I/O passes. Still row-by-row inserts (bulk can be added later).
    """
    t_global_start = time.perf_counter()
    msg_tracker = make_tracker(job_id, "message")
    reaction_tracker = make_tracker(job_id, "reaction")
    attach_tracker = make_tracker(job_id, "attachment")
    emoji_tracker = make_tracker(job_id, "custom_emoji")

    total_messages = 0
    total_reactions = 0
    total_attachments = 0
    custom_emoji_names: Set[str] = set()

    # Resolve bulk size from env if not provided
    if message_bulk_size is None:
        try:
            import os as _os

            message_bulk_size = int(
                _os.environ.get("IMPORT_MESSAGES_BULK_SIZE", "500") or 500
            )
        except Exception:
            message_bulk_size = 500

    # Resolve reaction bulk size
    if reaction_bulk_size is None:
        try:
            import os as _os

            reaction_bulk_size = int(
                _os.environ.get("IMPORT_REACTIONS_BULK_SIZE", "1000") or 1000
            )
        except Exception:
            reaction_bulk_size = 1000

    # Resolve attachment bulk size
    if attachment_bulk_size is None:
        try:
            import os as _os

            attachment_bulk_size = int(
                _os.environ.get("IMPORT_ATTACHMENTS_BULK_SIZE", "500") or 500
            )
        except Exception:
            attachment_bulk_size = 500

    # Accumulators for bulk insert (shared across workers when concurrency > 1)
    pending_messages: list[dict] = []  # each: {slack_id, raw, channel_id}
    pending_reactions: list[dict] = []  # each: {slack_id, raw, msg_ts}
    pending_attachments: list[dict] = []  # each: {slack_id, raw, message_ts}
    pending_relations: list[tuple] = (
        []
    )  # tuples of (from_entity_id, to_entity_id, relation_type)

    # Lock to protect shared accumulators + flush operations under concurrency
    batch_lock = asyncio.Lock()

    # Allow tuning relation batch size via env
    try:
        REL_BATCH_SIZE = int(
            os.environ.get("IMPORT_RELATIONS_BATCH_SIZE", "2000") or 2000
        )
    except Exception:
        REL_BATCH_SIZE = 2000

    async def _flush_relations_batch():
        nonlocal pending_relations
        if not pending_relations:
            return
        t0_rel = time.perf_counter()
        try:
            from sqlalchemy import text as _text
            from app.models.base import SessionLocal

            values_parts = []
            params = {}
            for idx, (f_id, t_id, r_type) in enumerate(pending_relations):
                values_parts.append(f"(:f{idx}, :t{idx}, :rt{idx}, :job{idx})")
                params[f"f{idx}"] = f_id
                params[f"t{idx}"] = t_id
                params[f"rt{idx}"] = r_type
                params[f"job{idx}"] = job_id
            sql = f"""
                INSERT INTO entity_relations (from_entity_id, to_entity_id, relation_type, job_id)
                VALUES {', '.join(values_parts)}
                ON CONFLICT DO NOTHING
            """
            async with SessionLocal() as session:
                await session.execute(_text(sql), params)
                await session.commit()
            pending_relations.clear()
            backend_logger.debug(
                f"relations_batch_flush size>=1 ms={(time.perf_counter()-t0_rel)*1000:.2f}"
            )
        except Exception as re:
            backend_logger.error(
                f"Bulk relation insert failed, fallback sequential: {re}"
            )
            try:
                from app.models.entity_relation import EntityRelation
                from app.models.base import SessionLocal

                async with SessionLocal() as session:
                    for f_id, t_id, r_type in pending_relations:
                        try:
                            rel = EntityRelation(
                                from_entity_id=f_id,
                                to_entity_id=t_id,
                                relation_type=r_type,
                                job_id=job_id,
                                raw_data=None,
                            )
                            session.add(rel)
                        except Exception:
                            pass
                    await session.commit()
            except Exception as re2:
                backend_logger.error(f"Sequential relation fallback also failed: {re2}")
            pending_relations.clear()

    async def _flush_messages_batch(final: bool = False):
        nonlocal pending_messages, total_messages
        if not pending_messages:
            return
        t0 = time.perf_counter()
        try:
            from sqlalchemy import text as _text
            from app.models.base import SessionLocal

            values_sql_parts = []
            params = {}
            for idx, rec in enumerate(pending_messages):
                values_sql_parts.append(
                    f"(:etype{idx}, :sid{idx}, :mmid{idx}, :raw{idx}::jsonb, :job{idx}, :status{idx})"
                )
                params[f"etype{idx}"] = "message"
                params[f"sid{idx}"] = rec["slack_id"]
                params[f"mmid{idx}"] = None
                params[f"raw{idx}"] = rec["raw"]
                params[f"job{idx}"] = job_id
                params[f"status{idx}"] = "pending"
            sql = f"""
                INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status)
                VALUES {', '.join(values_sql_parts)}
                ON CONFLICT DO NOTHING
                RETURNING id, slack_id
            """
            inserted_map: dict[str, int] = {}
            async with SessionLocal() as session:
                res = await session.execute(_text(sql), params)
                rows = res.fetchall()
                for _id, _sid in rows:
                    inserted_map[str(_sid)] = int(_id)
                await session.commit()
            # processed = number of rows we attempted (optimistic) because duplicates for same job should not happen
            count_inserted = len(pending_messages)
            total_messages += count_inserted
            await msg_tracker.incr_processed(count_inserted)
            # Build relation rows for batch insert
            from sqlalchemy import select as _select
            from app.models.base import SessionLocal as _SessionLocal
            from app.models.entity import Entity as _Entity

            async with _SessionLocal() as _session:
                channel_cache = {}
                user_cache = {}
                message_id_cache = {}  # slack_id -> entity_id
                # Preload parent message ids for thread replies in this batch
                thread_parent_ids_needed = set()
                for rec in pending_messages:
                    raw_msg = rec["raw"]
                    thread_ts = (raw_msg or {}).get("thread_ts")
                    ts = (raw_msg or {}).get("ts")
                    if thread_ts and thread_ts != ts:
                        thread_parent_ids_needed.add(thread_ts)
                if thread_parent_ids_needed:
                    res = await _session.execute(
                        _select(_Entity).where(
                            (_Entity.entity_type == "message")
                            & (_Entity.slack_id.in_(list(thread_parent_ids_needed)))
                            & ((_Entity.job_id == job_id) | (_Entity.job_id.is_(None)))
                        )
                    )
                    for ent in res.scalars().all():
                        message_id_cache[ent.slack_id] = ent.id
                # Now build relations for each message
                for rec in pending_messages:
                    slack_id = rec["slack_id"]
                    raw_msg = rec["raw"]
                    channel_id = rec["channel_id"]
                    ent_id = inserted_map.get(slack_id)
                    if not ent_id:
                        continue
                    # posted_in: need channel entity id
                    if channel_id not in channel_cache:
                        q = await _session.execute(
                            _select(_Entity).where(
                                (_Entity.entity_type == "channel")
                                & (_Entity.slack_id == channel_id)
                                & (
                                    (_Entity.job_id == job_id)
                                    | (_Entity.job_id.is_(None))
                                )
                            )
                        )
                        ch_ent = q.scalar_one_or_none()
                        if ch_ent:
                            channel_cache[channel_id] = ch_ent.id
                    ch_id = channel_cache.get(channel_id)
                    if ch_id:
                        pending_relations.append((ent_id, ch_id, "posted_in"))
                    # posted_by
                    user_id = (raw_msg or {}).get("user") or (raw_msg or {}).get(
                        "bot_id"
                    )
                    if user_id:
                        if user_id not in user_cache:
                            q = await _session.execute(
                                _select(_Entity).where(
                                    (_Entity.entity_type == "user")
                                    & (_Entity.slack_id == user_id)
                                    & (
                                        (_Entity.job_id == job_id)
                                        | (_Entity.job_id.is_(None))
                                    )
                                )
                            )
                            u_ent = q.scalar_one_or_none()
                            if u_ent:
                                user_cache[user_id] = u_ent.id
                        u_id = user_cache.get(user_id)
                        if u_id:
                            pending_relations.append((u_id, ent_id, "posted_by"))
                    # thread_reply
                    thread_ts = (raw_msg or {}).get("thread_ts")
                    ts = (raw_msg or {}).get("ts")
                    if thread_ts and thread_ts != ts:
                        parent_id = message_id_cache.get(thread_ts)
                        if parent_id:
                            pending_relations.append(
                                (ent_id, parent_id, "thread_reply")
                            )
                    if len(pending_relations) >= REL_BATCH_SIZE:
                        await _flush_relations_batch()
            pending_messages.clear()
            backend_logger.debug(
                f"messages_batch_flush size={count_inserted} final={final} ms={(time.perf_counter()-t0)*1000:.2f}"
            )
        except Exception as be:
            backend_logger.error(
                f"Bulk insert messages batch failed (fallback row mode next iteration): {be}"
            )
            # Fallback: insert each individually (reuse old logic)
            for rec in pending_messages:
                raw_msg = rec["raw"]
                channel_id = rec["channel_id"]
                slack_id = rec["slack_id"]
                m_entity = Message(
                    slack_id=slack_id,
                    mattermost_id=None,
                    raw_data=raw_msg,
                    status="pending",
                    auto_save=False,
                    job_id=job_id,
                )
                try:
                    await m_entity.save_to_db(channel_id)
                    if getattr(m_entity, "id", None) is not None:
                        await m_entity.create_posted_in_relation(channel_id)
                        await m_entity.create_posted_by_relation()
                        await m_entity.create_thread_relation()
                        total_messages += 1
                        await msg_tracker.incr_processed(1)
                except Exception as ie:
                    backend_logger.error(
                        f"Row insert fallback failed for message {slack_id}: {ie}"
                    )
            pending_messages.clear()

    async def _flush_reactions_batch(final: bool = False):
        nonlocal pending_reactions, total_reactions
        if not pending_reactions:
            return
        t0 = time.perf_counter()
        try:
            from sqlalchemy import text as _text
            from app.models.base import SessionLocal

            values_sql_parts = []
            params = {}
            for idx, rec in enumerate(pending_reactions):
                values_sql_parts.append(
                    f"(:etype_r{idx}, :sid_r{idx}, :mmid_r{idx}, :raw_r{idx}::jsonb, :job_r{idx}, :status_r{idx})"
                )
                params[f"etype_r{idx}"] = "reaction"
                params[f"sid_r{idx}"] = rec["slack_id"]
                params[f"mmid_r{idx}"] = None
                params[f"raw_r{idx}"] = rec["raw"]
                params[f"job_r{idx}"] = job_id
                params[f"status_r{idx}"] = "pending"
            sql = f"""
                INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status)
                VALUES {', '.join(values_sql_parts)}
                ON CONFLICT DO NOTHING
                RETURNING id, slack_id
            """
            inserted_map: dict[str, int] = {}
            async with SessionLocal() as session:
                res = await session.execute(_text(sql), params)
                rows = res.fetchall()
                for _id, _sid in rows:
                    inserted_map[str(_sid)] = int(_id)
                await session.commit()
            count_inserted = len(pending_reactions)
            total_reactions += count_inserted
            await reaction_tracker.incr_processed(count_inserted)
            # Build reaction relations in batch mode
            from sqlalchemy import select as _select_r
            from app.models.base import SessionLocal as _SessionLocal_r
            from app.models.entity import Entity as _Entity_r

            async with _SessionLocal_r() as _session_r:
                user_cache = {}
                message_cache = {}
                for rec in pending_reactions:
                    slack_id = rec["slack_id"]
                    raw_r = rec["raw"]
                    ent_id = inserted_map.get(slack_id)
                    if not ent_id:
                        continue
                    # reacted_by: user -> reaction
                    user_id = (raw_r or {}).get("user")
                    if user_id:
                        if user_id not in user_cache:
                            q = await _session_r.execute(
                                _select_r(_Entity_r).where(
                                    (_Entity_r.entity_type == "user")
                                    & (_Entity_r.slack_id == user_id)
                                    & (
                                        (_Entity_r.job_id == job_id)
                                        | (_Entity_r.job_id.is_(None))
                                    )
                                )
                            )
                            u_ent = q.scalar_one_or_none()
                            if u_ent:
                                user_cache[user_id] = u_ent.id
                        u_id = user_cache.get(user_id)
                        if u_id:
                            pending_relations.append((u_id, ent_id, "reacted_by"))
                    # reacted_to: reaction -> message
                    ts = (raw_r or {}).get("ts") or (raw_r or {}).get("message_ts")
                    if ts:
                        if ts not in message_cache:
                            q = await _session_r.execute(
                                _select_r(_Entity_r).where(
                                    (_Entity_r.entity_type == "message")
                                    & (_Entity_r.slack_id == ts)
                                    & (
                                        (_Entity_r.job_id == job_id)
                                        | (_Entity_r.job_id.is_(None))
                                    )
                                )
                            )
                            m_ent = q.scalar_one_or_none()
                            if m_ent:
                                message_cache[ts] = m_ent.id
                        m_id = message_cache.get(ts)
                        if m_id:
                            pending_relations.append((ent_id, m_id, "reacted_to"))
                    if len(pending_relations) >= REL_BATCH_SIZE:
                        await _flush_relations_batch()
            pending_reactions.clear()
            backend_logger.debug(
                f"reactions_batch_flush size={count_inserted} final={final} ms={(time.perf_counter()-t0)*1000:.2f}"
            )
        except Exception as be:
            backend_logger.error(
                f"Bulk insert reactions batch failed (fallback row mode next iteration): {be}"
            )
            for rec in pending_reactions:
                slack_id = rec["slack_id"]
                raw_r = rec["raw"]
                r_entity = Reaction(
                    slack_id=slack_id,
                    mattermost_id=None,
                    raw_data=raw_r,
                    status="pending",
                    auto_save=False,
                    job_id=job_id,
                )
                try:
                    ent_r = await r_entity.save_to_db()
                    if ent_r is not None:
                        total_reactions += 1
                        await r_entity.create_reacted_by_relation()
                        await r_entity.create_reacted_to_relation()
                        await reaction_tracker.incr_processed(1)
                except Exception as ie:
                    backend_logger.error(
                        f"Row insert fallback failed for reaction {slack_id}: {ie}"
                    )
            pending_reactions.clear()

    async def _flush_attachments_batch(final: bool = False):
        nonlocal pending_attachments, total_attachments
        if not pending_attachments:
            return
        t0 = time.perf_counter()
        try:
            from sqlalchemy import text as _text
            from app.models.base import SessionLocal

            values_sql_parts = []
            params = {}
            for idx, rec in enumerate(pending_attachments):
                values_sql_parts.append(
                    f"(:etype_a{idx}, :sid_a{idx}, :mmid_a{idx}, :raw_a{idx}::jsonb, :job_a{idx}, :status_a{idx})"
                )
                params[f"etype_a{idx}"] = "attachment"
                params[f"sid_a{idx}"] = rec["slack_id"]
                params[f"mmid_a{idx}"] = None
                params[f"raw_a{idx}"] = rec["raw"]
                params[f"job_a{idx}"] = job_id
                params[f"status_a{idx}"] = "pending"
            sql = f"""
                INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status)
                VALUES {', '.join(values_sql_parts)}
                ON CONFLICT DO NOTHING
                RETURNING id, slack_id
            """
            inserted_map: dict[str, int] = {}
            async with SessionLocal() as session:
                res = await session.execute(_text(sql), params)
                rows = res.fetchall()
                for _id, _sid in rows:
                    inserted_map[str(_sid)] = int(_id)
                await session.commit()
            count_inserted = len(pending_attachments)
            total_attachments += count_inserted
            await attach_tracker.incr_processed(count_inserted)
            # Build attachment relations
            from sqlalchemy import select as _select_a
            from app.models.base import SessionLocal as _SessionLocal_a
            from app.models.entity import Entity as _Entity_a

            async with _SessionLocal_a() as _session_a:
                message_cache = {}
                for rec in pending_attachments:
                    slack_id = rec["slack_id"]
                    msg_ts = rec["message_ts"]
                    ent_id = inserted_map.get(slack_id)
                    if not ent_id:
                        continue
                    if msg_ts not in message_cache:
                        q = await _session_a.execute(
                            _select_a(_Entity_a).where(
                                (_Entity_a.entity_type == "message")
                                & (_Entity_a.slack_id == msg_ts)
                                & (
                                    (_Entity_a.job_id == job_id)
                                    | (_Entity_a.job_id.is_(None))
                                )
                            )
                        )
                        m_ent = q.scalar_one_or_none()
                        if m_ent:
                            message_cache[msg_ts] = m_ent.id
                    m_id = message_cache.get(msg_ts)
                    if m_id:
                        pending_relations.append((ent_id, m_id, "attached_to"))
                    if len(pending_relations) >= REL_BATCH_SIZE:
                        await _flush_relations_batch()
            pending_attachments.clear()
            backend_logger.debug(
                f"attachments_batch_flush size={count_inserted} final={final} ms={(time.perf_counter()-t0)*1000:.2f}"
            )
        except Exception as be:
            backend_logger.error(
                f"Bulk insert attachments batch failed (fallback row mode next iteration): {be}"
            )
            for rec in pending_attachments:
                slack_id = rec["slack_id"]
                raw_a = rec["raw"]
                msg_ts = rec["message_ts"]
                a_entity = Attachment(
                    slack_id=slack_id,
                    mattermost_id=None,
                    raw_data=raw_a,
                    status="pending",
                    auto_save=False,
                    job_id=job_id,
                )
                try:
                    ent_a = await a_entity.save_to_db()
                    if ent_a is not None:
                        total_attachments += 1
                        await a_entity.create_attached_to_relation(msg_ts)
                        await attach_tracker.incr_processed(1)
                except Exception as ie:
                    backend_logger.error(
                        f"Row insert fallback failed for attachment {slack_id}: {ie}"
                    )
            pending_attachments.clear()

    async def _process_channel(folder: str, channel: dict):
        if not channel:
            return
        channel_id = channel.get("id")
        if not channel_id:
            return
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            return
        # Local collection to minimize contention
        local_emoji_names = set()
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f) or []
                        if not isinstance(data, list):
                            backend_logger.error(
                                f"Формат файла {msg_file} не список — пропуск"
                            )
                            continue
                    except Exception as je:
                        backend_logger.error(f"Ошибка парсинга JSON {msg_file}: {je}")
                        continue
                for msg in data:
                    try:
                        slack_id = (msg or {}).get("ts")
                        if not slack_id:
                            continue
                        # Ensure channel_id field is present inside raw message for invariant / backfill
                        if (
                            channel_id
                            and isinstance(msg, dict)
                            and "channel_id" not in msg
                        ):
                            # Non-destructive enrichment of raw message
                            msg["channel_id"] = channel_id
                        await msg_tracker.incr_parsed(1)
                        has_related = bool((msg or {}).get("files")) or bool(
                            (msg or {}).get("reactions")
                        )
                        async with batch_lock:
                            pending_messages.append(
                                {
                                    "slack_id": slack_id,
                                    "raw": msg,
                                    "channel_id": channel_id,
                                }
                            )
                            if has_related and pending_messages:
                                await _flush_messages_batch()
                            if len(pending_messages) >= (message_bulk_size or 500):
                                await _flush_messages_batch()
                        # Attachments
                        for file_obj in (msg or {}).get("files") or []:
                            slack_aid = file_obj.get("id")
                            url_private = file_obj.get("url_private")
                            if not slack_aid or not (
                                url_private
                                and url_private.startswith("https://files.slack.com")
                            ):
                                continue
                            await attach_tracker.incr_parsed(1)
                            async with batch_lock:
                                pending_attachments.append(
                                    {
                                        "slack_id": slack_aid,
                                        "raw": file_obj,
                                        "message_ts": slack_id,
                                    }
                                )
                                if len(pending_attachments) >= (
                                    attachment_bulk_size or 500
                                ):
                                    await _flush_attachments_batch()
                        # Reactions
                        for reaction in (msg or {}).get("reactions") or []:
                            r_name = reaction.get("name")
                            if not r_name:
                                continue
                            users = reaction.get("users") or []
                            for user_id in users:
                                await reaction_tracker.incr_parsed(1)
                                reaction_data = dict(reaction)
                                reaction_data["user"] = user_id
                                reaction_data["message_ts"] = slack_id
                                reaction_data["emoji_name"] = r_name
                                reaction_data["composite_id"] = f"{slack_id}_{r_name}"
                                if "ts" not in reaction_data:
                                    try:
                                        reaction_data["ts"] = str(slack_id)
                                    except Exception:
                                        pass
                                async with batch_lock:
                                    pending_reactions.append(
                                        {
                                            "slack_id": f"{slack_id}_{r_name}_{user_id}",
                                            "raw": reaction_data,
                                            "msg_ts": slack_id,
                                        }
                                    )
                                    if len(pending_reactions) >= (
                                        reaction_bulk_size or 1000
                                    ):
                                        await _flush_reactions_batch()
                                if (
                                    emoji_list
                                    and r_name in emoji_list
                                    and emoji_list[r_name]
                                ):
                                    local_emoji_names.add(r_name)
                        # Emoji scans (no shared state except custom_emoji_names set — safe without lock as GIL + set operations atomic enough; still to be safe use lock when adding many?)
                        text = (msg or {}).get("text") or ""
                        for name in EMOJI_PATTERN.findall(text):
                            local_emoji_names.add(name)
                        for at in (msg or {}).get("attachments") or []:
                            for key in ("pretext", "title", "text", "fallback"):
                                val = at.get(key)
                                if isinstance(val, str):
                                    for name in EMOJI_PATTERN.findall(val):
                                        local_emoji_names.add(name)
                        for blk in (msg or {}).get("blocks") or []:
                            if isinstance(blk, dict):
                                if blk.get("type") == "rich_text":
                                    for el in blk.get("elements", []) or []:
                                        if isinstance(el, dict) and el.get("type") in (
                                            "text",
                                            "mrkdwn",
                                            "plain_text",
                                        ):
                                            for name in EMOJI_PATTERN.findall(
                                                el.get("text") or ""
                                            ):
                                                local_emoji_names.add(name)
                                else:
                                    txt_obj = blk.get("text")
                                    if isinstance(txt_obj, dict):
                                        for name in EMOJI_PATTERN.findall(
                                            txt_obj.get("text") or ""
                                        ):
                                            local_emoji_names.add(name)
                        if total_messages % batch_log_every == 0 and total_messages:
                            backend_logger.debug(
                                f"Сообщений: {total_messages}, реакций: {total_reactions}, аттачментов: {total_attachments}"
                            )
                            if progress_messages:
                                await progress_messages(batch_log_every)
                    except Exception as ie:
                        backend_logger.error(
                            f"Ошибка обработки сообщения в {msg_file}: {ie}"
                        )
                if file_progress:
                    try:
                        await file_progress(1)
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file}: {e}")
                continue
        if local_emoji_names:
            # Merge under lock to ensure thread safety (set operations atomic but be explicit)
            async with batch_lock:
                custom_emoji_names.update(local_emoji_names)

    # Concurrency orchestration
    try:
        channel_conc = int(os.environ.get("IMPORT_CHANNEL_CONCURRENCY", "1") or 1)
    except Exception:
        channel_conc = 1

    if channel_conc <= 1:
        # Fallback sequential (original behavior via worker reuse)
        for folder, channel in folder_channel_map.items():
            await _process_channel(folder, channel)
    else:
        sem = asyncio.Semaphore(channel_conc)

        async def _guarded(folder, channel):
            async with sem:
                await _process_channel(folder, channel)

        tasks = [
            asyncio.create_task(_guarded(folder, channel))
            for folder, channel in folder_channel_map.items()
        ]
        await asyncio.gather(*tasks)

    # Flush remaining messages
    async with batch_lock:
        await _flush_messages_batch(final=True)
        await _flush_reactions_batch(final=True)
        await _flush_attachments_batch(final=True)
        await _flush_relations_batch()

    # Final flush trackers
    await msg_tracker.flush()
    await reaction_tracker.flush()
    await attach_tracker.flush()

    # Custom emojis creation
    created_emojis = 0
    if emoji_list:
        for name in sorted(custom_emoji_names):
            if not emoji_list.get(name):
                continue
            await emoji_tracker.incr_parsed(1)
            c_entity = CustomEmoji(
                slack_id=name,
                raw_data={"name": name, "url": emoji_list.get(name)},
                status="pending",
                auto_save=False,
            )
            ent_c = await c_entity.save_to_db()
            if ent_c is not None:
                created_emojis += 1
                await emoji_tracker.incr_processed(1)
        await emoji_tracker.flush()

    backend_logger.info(
        f"Единый импорт завершён: messages={total_messages}, reactions={total_reactions}, attachments={total_attachments}, custom_emojis={created_emojis}, elapsed_sec={(time.perf_counter()-t_global_start):.2f}"
    )
    # Return summary
    return {
        "messages": total_messages,
        "reactions": total_reactions,
        "attachments": total_attachments,
        "custom_emojis": created_emojis,
    }


## End of module.

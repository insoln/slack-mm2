import asyncio
import os
import httpx
from app.models.base import SessionLocal
from sqlalchemy import select
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from typing import cast
from .user_exporter import UserExporter
from .custom_emoji_exporter import CustomEmojiExporter
from .attachment_exporter import AttachmentExporter
from .message_exporter import MessageExporter
from .reaction_exporter import ReactionExporter
from .channel_exporter import ChannelExporter
from app.logging_config import backend_logger
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
from app.services.entities.user import User
from app.services.entities.custom_emoji import CustomEmoji
from app.services.entities.attachment import Attachment
from app.utils.time import parse_slack_ts
from collections import Counter
import time as _time

DEFAULT_RELATION_CHUNK = 10_000

EXPORT_ORDER = [
    ("user", UserExporter),
    ("custom_emoji", CustomEmojiExporter),
    ("channel", ChannelExporter),
    # Upload attachments before messages so message payloads can include file_ids
    ("attachment", AttachmentExporter),
    ("message", MessageExporter),
    ("reaction", ReactionExporter),
]

# Ensure only one export runs globally at a time
EXPORT_LOCK = asyncio.Lock()

# Default poll interval (seconds) when waiting for earliest job to enter 'exporting'
EXPORT_QUEUE_POLL_DEFAULT: float = 2.0


def _categorize_error(message: str) -> str:
    text = str(message).strip()
    if ":" in text:
        return text.split(":", 1)[0].strip() or text[:80]
    return text[:80]


async def get_mm_user_id():
    """Получить ID пользователя-владельца токена из Mattermost"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{os.environ['MM_URL']}/api/v4/users/me",
                headers={"Authorization": f"Bearer {os.environ['MM_TOKEN']}"},
                timeout=10,
            )
            if resp.status_code == 200:
                user_data = resp.json()
                user_id = user_data.get("id")
                backend_logger.info(f"Получен ID пользователя Mattermost: {user_id}")
                return user_id
            else:
                backend_logger.error(
                    f"Ошибка получения ID пользователя: {resp.status_code}"
                )
                return None
    except Exception as e:
        backend_logger.error(f"Ошибка при получении ID пользователя: {e}")
        return None


async def get_entities_to_export(entity_type: str, job_id=None):
    async with SessionLocal() as session:
        # Only pick truly pending entities. Skipped/failed are terminal unless a future
        # explicit retry mechanism is introduced. Including them causes re-queuing
        # loops (especially visible for reactions) and stalls perceived progress.
        cond = (Entity.entity_type == entity_type) & (
            Entity.status == MappingStatus.pending
        )
        # job_scoped_condition now a no-op; we ignore job scoping for global uniqueness

        query = await session.execute(select(Entity).where(cond))
        entities = query.scalars().all()
        if entity_type == "user":
            return [User.from_entity(e) for e in entities]
        elif entity_type == "custom_emoji":
            return [CustomEmoji.from_entity(e) for e in entities]
        elif entity_type == "attachment":
            # For attachments we can use BaseMapping as-is (no special from_entity)
            return entities
        elif entity_type == "message":
            # Sort messages so that roots go before replies, and by timestamp ascending
            try:
                from app.models.entity_relation import EntityRelation

                ids = [e.id for e in entities]
                reply_set = set()
                if ids:
                    rel_rows = await session.execute(
                        select(EntityRelation.from_entity_id).where(
                            (EntityRelation.relation_type == "thread_reply")
                            & (EntityRelation.from_entity_id.in_(ids))
                        )
                    )
                    reply_set = {row[0] for row in rel_rows.all()}

                def ts_key(ent):
                    return parse_slack_ts(ent.slack_id)

                entities_sorted = sorted(
                    entities,
                    key=lambda ent: (0 if ent.id not in reply_set else 1, ts_key(ent)),
                )
                return entities_sorted
            except Exception as e:
                backend_logger.error(
                    f"Не удалось отсортировать сообщения для тредов: {e}"
                )
                return entities
        elif entity_type == "reaction":
            # Ensure reactions are processed after their target messages; simple ts sort as tie-breaker
            def ts_key(ent):
                return parse_slack_ts(ent.slack_id)

            return sorted(entities, key=ts_key)
        return entities


async def export_worker(queue, mm_user_id):
    while True:
        item = await queue.get()
        if item is None:
            # Mark sentinel as done to keep queue counters consistent
            queue.task_done()
            break
        entity, exporter_cls = item
        exporter = None
        try:
            # Передаем mm_user_id только для CustomEmojiExporter
            if exporter_cls == CustomEmojiExporter:
                exporter = exporter_cls(entity, mm_user_id=mm_user_id)
            else:
                exporter = exporter_cls(entity)
            # Вызов централизованной проверки зависимостей
            failed, reason = await exporter.guard_dependencies()
            if failed:
                await exporter.set_status("skipped", error=reason)
                backend_logger.info(
                    f"Skip {entity.entity_type} {entity.slack_id} due to unmet dependency: {reason}"
                )
                queue.task_done()
                continue
            await exporter.export_entity()
        except Exception as e:
            # Capture concise root cause signature for aggregation.
            err_txt = str(e)
            cat = _categorize_error(err_txt)
            backend_logger.error(
                f"Ошибка экспорта {entity.entity_type} {entity.slack_id}: {err_txt} (cat={cat})"
            )
            try:
                # Ensure status is not left pending on crash
                if exporter is not None:
                    await exporter.set_status("failed", error=err_txt)
                else:
                    # fallback: direct update
                    from sqlalchemy import update

                    async with SessionLocal() as session:
                        where_cond = (Entity.entity_type == entity.entity_type) & (
                            Entity.slack_id == entity.slack_id
                        )
                        # Scope by job only for job-scoped types (message/reaction/attachment)
                        # No job scoping; update by (type, slack_id)
                        await session.execute(
                            update(Entity)
                            .where(where_cond)
                            .values(status="failed", error_message=err_txt)
                        )
                        await session.commit()
            except Exception:
                pass
        queue.task_done()


async def _get_archived_channel_ids() -> set[str]:
    """Get all channel entity IDs that were archived (is_archived in raw_data).
    Returns set of mattermost_id strings for channels that should be kept archived.
    """
    async with SessionLocal() as session:
        q = await session.execute(
            select(Entity).where(
                (Entity.entity_type == "channel")
                & (Entity.status == MappingStatus.success)
                & (Entity.mattermost_id.is_not(None))
            )
        )
        channels = q.scalars().all()
        archived = set()
        for ch in channels:
            if ch.mattermost_id and (ch.raw_data or {}).get("is_archived"):
                archived.add(ch.mattermost_id)
        backend_logger.info(
            f"Found {len(archived)} archived channels to manage during reaction export"
        )
        return archived


async def _unarchive_channels(channel_ids: set[str]):
    """Unarchive specified channels via plugin API."""
    if not channel_ids:
        return
    mm_url = os.environ.get("MM_URL")
    mm_token = os.environ.get("MM_TOKEN")
    if not mm_url or not mm_token:
        backend_logger.warning("MM_URL or MM_TOKEN not set, skipping channel unarchive")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        for cid in channel_ids:
            try:
                resp = await client.post(
                    f"{mm_url}/plugins/mm-importer/api/v1/channel/unarchive",
                    json={"channel_id": cid},
                    headers={"Authorization": f"Bearer {mm_token}"},
                )
                if resp.status_code not in (200, 201):
                    backend_logger.warning(
                        f"Failed to unarchive channel {cid}: {resp.status_code} {resp.text[:200]}"
                    )
                else:
                    backend_logger.debug(
                        f"Unarchived channel {cid} for reaction export"
                    )
            except Exception as e:  # noqa: BLE001
                backend_logger.error(f"Error unarchiving channel {cid}: {e}")


async def _rearchive_channels(channel_ids: set[str]):
    """Re-archive specified channels via plugin API."""
    if not channel_ids:
        return
    mm_url = os.environ.get("MM_URL")
    mm_token = os.environ.get("MM_TOKEN")
    if not mm_url or not mm_token:
        backend_logger.warning(
            "MM_URL or MM_TOKEN not set, skipping channel re-archive"
        )
        return

    async with httpx.AsyncClient(timeout=30) as client:
        for cid in channel_ids:
            try:
                resp = await client.post(
                    f"{mm_url}/plugins/mm-importer/api/v1/channel/archive",
                    json={"channel_id": cid},
                    headers={"Authorization": f"Bearer {mm_token}"},
                )
                if resp.status_code not in (200, 201):
                    backend_logger.warning(
                        f"Failed to re-archive channel {cid}: {resp.status_code} {resp.text[:200]}"
                    )
                else:
                    backend_logger.debug(
                        f"Re-archived channel {cid} after reaction export"
                    )
            except Exception as e:  # noqa: BLE001
                backend_logger.error(f"Error re-archiving channel {cid}: {e}")


async def orchestrate_mm_export(job_id=None):
    # Ensure only one export runs at a time across the process
    async with EXPORT_LOCK:
        # Получаем ID пользователя-владельца токена
        mm_user_id = await get_mm_user_id()
        if not mm_user_id:
            backend_logger.error(
                "Не удалось получить ID пользователя Mattermost, прерываю экспорт"
            )
            return

        workers_count = int(os.getenv("EXPORT_WORKERS", 5))

        # Optional anchor: if job_id is provided, only consider jobs uploaded up to that anchor.
        anchor_cutoff: tuple | None = None
        async with SessionLocal() as session:
            if job_id is not None:
                anc = await session.get(ImportJob, job_id)
                if anc is not None:
                    anchor_cutoff = (anc.created_at, anc.id)

        sleep_s = float(os.getenv("EXPORT_QUEUE_POLL", str(EXPORT_QUEUE_POLL_DEFAULT)))

        async def _fetch_exporting_jobs() -> list[ImportJob]:
            async with SessionLocal() as s:
                q = select(ImportJob).where(ImportJob.status == JobStatus.running)
                if anchor_cutoff is not None:
                    q = q.where(
                        (ImportJob.created_at < anchor_cutoff[0])
                        | (
                            (ImportJob.created_at == anchor_cutoff[0])
                            & (ImportJob.id <= anchor_cutoff[1])
                        )
                    )
                q = q.order_by(ImportJob.created_at.asc(), ImportJob.id.asc())
                rows = await s.execute(q)
                all_running = rows.scalars().all()
                jobs = [
                    r for r in all_running if cast(str, r.current_stage) == "exporting"
                ]
                return jobs

        async def _promote_earliest_ready_export():
            """Promote the earliest job in 'ready_export' (by created_at, id) to 'exporting' if
            and only if there are no earlier jobs still not finished. This enforces strict FIFO.
            """
            async with SessionLocal() as s:
                # Earliest running job by ordering
                q_all = (
                    select(ImportJob)
                    .where(ImportJob.status == JobStatus.running)
                    .order_by(ImportJob.created_at.asc(), ImportJob.id.asc())
                )
                rows_all = await s.execute(q_all)
                ordered = list(rows_all.scalars().all())
                if not ordered:
                    return None
                # Find earliest that is either already exporting or ready_export
                earliest_ready = None
                for r in ordered:
                    stage = cast(str, r.current_stage)
                    if stage in {"exporting"}:
                        # Someone already exporting; do not promote new one yet
                        return None
                    if stage == "ready_export":
                        earliest_ready = r
                        break
                    if stage in {"extracting", "users", "channels", "messages"}:
                        # Still ingesting something earlier; can't promote any later job
                        return None
                if earliest_ready is None:
                    return None
                # Promote
                from sqlalchemy import update

                await s.execute(
                    update(ImportJob)
                    .where(ImportJob.id == earliest_ready.id)
                    .values(current_stage="exporting")
                )
                await s.commit()
                backend_logger.info(
                    f"Продвигаю job_id={earliest_ready.id} из 'ready_export' в 'exporting' (FIFO)"
                )
                return earliest_ready.id

        async def _has_pending_for_type(
            entity_type: str, jobs: list[ImportJob]
        ) -> bool:
            """Check if there are pending entities of a given type across jobs.

            Adds detailed debug logging with an exact count (cheap enough at our scale;
            if it becomes hot we can gate behind LOG_LEVEL).
            """
            async with SessionLocal() as s:
                from sqlalchemy import select, and_, func

                cond = (Entity.entity_type == entity_type) & (
                    Entity.status == MappingStatus.pending
                )
                if entity_type in ("message", "reaction", "attachment"):
                    ids = [int(cast(int, j.id)) for j in jobs]
                    if not ids:
                        backend_logger.debug(
                            f"[EXPORT_DEBUG] pending_check type={entity_type} jobs=[] pending=False count=0"
                        )
                        return False
                    cond = and_(cond, Entity.job_id.in_(ids))
                # Exact count for transparency
                qcnt = await s.execute(
                    select(func.count()).select_from(Entity).where(cond)
                )
                count = int(qcnt.scalar_one())
                has = count > 0
                backend_logger.debug(
                    f"[EXPORT_DEBUG] pending_check type={entity_type} jobs={[int(cast(int,j.id)) for j in jobs]} pending={has} count={count}"
                )
                return has

        # Reaction logging aggregation state (to reduce log spam)
        reaction_log_window: dict[str, int] = {}
        reaction_log_last_flush = _time.time()
        REACTION_FLUSH_INTERVAL = 5.0  # seconds
        REACTION_FLUSH_LIMIT = 200  # distinct reactions before forced flush

        def _record_reaction(slack_id: str):
            nonlocal reaction_log_last_flush
            reaction_log_window[slack_id] = reaction_log_window.get(slack_id, 0) + 1
            now = _time.time()
            if (
                now - reaction_log_last_flush >= REACTION_FLUSH_INTERVAL
                or len(reaction_log_window) >= REACTION_FLUSH_LIMIT
            ):
                total = sum(reaction_log_window.values())
                sample_items = list(reaction_log_window.items())[:8]
                backend_logger.info(
                    f"[REACTIONS] batch exported total={total} distinct={len(reaction_log_window)} sample={sample_items}"
                )
                reaction_log_window.clear()
                reaction_log_last_flush = now

        while True:
            jobs = await _fetch_exporting_jobs()
            if not jobs:
                # Try to promote earliest ready_export job (FIFO). If none promoted, then see if system idle.
                promoted = await _promote_earliest_ready_export()
                if promoted is None:
                    # Re-evaluate; maybe no running jobs left
                    async with SessionLocal() as s:
                        q2 = select(ImportJob).where(
                            ImportJob.status == JobStatus.running
                        )
                        if anchor_cutoff is not None:
                            q2 = q2.where(
                                (ImportJob.created_at < anchor_cutoff[0])
                                | (
                                    (ImportJob.created_at == anchor_cutoff[0])
                                    & (ImportJob.id <= anchor_cutoff[1])
                                )
                            )
                        q2 = q2.order_by(
                            ImportJob.created_at.asc(), ImportJob.id.asc()
                        ).limit(1)
                        row = await s.execute(q2)
                        earliest = row.scalars().first()
                    if earliest is None:
                        backend_logger.info("Очередь экспорта пуста — выходим")
                        break
                    # If earliest still ingesting, we'll sleep; if earliest is ready_export but not promoted (shouldn't happen), loop again.
                    cur_stage = cast(str, earliest.current_stage)
                    backend_logger.info(
                        f"Ожидание продвижения: job_id={earliest.id} стадия={cur_stage} (FIFO барьер)"
                    )
                    await asyncio.sleep(sleep_s)
                    continue
                else:
                    # Newly promoted; loop to pick up as exporting
                    await asyncio.sleep(0)
                    continue

            # Global per-type barrier: complete each type across all exporting jobs in FIFO order
            backend_logger.info(
                f"Запуск экспорта с глобальным барьером типов для {len(jobs)} задач"
            )
            for entity_type, exporter_cls in EXPORT_ORDER:
                # Special handling for reactions: unarchive channels before, re-archive after
                archived_channel_ids: set[str] = set()
                if entity_type == "reaction":
                    archived_channel_ids = await _get_archived_channel_ids()
                    if archived_channel_ids:
                        backend_logger.info(
                            f"Unarchiving {len(archived_channel_ids)} channels for reaction export"
                        )
                        await _unarchive_channels(archived_channel_ids)

                # Repeat the type until no exporting job has pending/skipped entities of this type
                while True:
                    jobs = await _fetch_exporting_jobs()
                    if not jobs:
                        break
                    backend_logger.debug(
                        f"[EXPORT_DEBUG] type_loop_start type={entity_type} jobs={[int(cast(int,j.id)) for j in jobs]}"
                    )
                    backend_logger.info(
                        f"[TYPE] Начинаю экспорт типа {entity_type} для {len(jobs)} задач"
                    )
                    if entity_type in ("user", "custom_emoji", "channel"):
                        # Global types: export once across all jobs
                        queue = asyncio.Queue()
                        entities = await get_entities_to_export(
                            entity_type, job_id=None
                        )
                        for entity in entities:
                            backend_logger.debug(
                                f"[EXPORT] enqueue {entity_type} {entity.slack_id}"
                            )
                            await queue.put((entity, exporter_cls))
                        workers_for_type = workers_count
                        backend_logger.debug(
                            f"[EXPORT] starting {workers_for_type} workers for global {entity_type}"
                        )
                        workers = [
                            asyncio.create_task(export_worker(queue, mm_user_id))
                            for _ in range(workers_for_type)
                        ]
                        await queue.join()
                        for _ in workers:
                            await queue.put(None)
                        await asyncio.gather(*workers)
                        # Aggregated status breakdown for transparency
                        from sqlalchemy import select as _select, func as _func

                        async with SessionLocal() as s:
                            br_rows = await s.execute(
                                _select(Entity.status, _func.count())
                                .where(Entity.entity_type == entity_type)
                                .group_by(Entity.status)
                            )
                            counts = {
                                str(getattr(st, "value", st)): int(c)
                                for st, c in br_rows.all()
                            }
                            # Which jobs still have pending of this type (should be 0 if created rows stable)
                            job_rows = await s.execute(
                                _select(Entity.job_id).where(
                                    (Entity.entity_type == entity_type)
                                    & (Entity.status == MappingStatus.pending)
                                )
                            )
                            pending_job_ids = sorted(
                                {int(r[0]) for r in job_rows.all() if r[0] is not None}
                            )
                        backend_logger.info(
                            f"Экспорт {entity_type} завершён (global) statuses={counts} pending_jobs={pending_job_ids}"
                        )
                    else:
                        # Job-scoped types: export per job
                        for j in jobs:
                            backend_logger.info(
                                f"Экспорт сущностей типа {entity_type} (job_id={j.id})"
                            )
                            if entity_type == "message":
                                t0 = asyncio.get_event_loop().time()
                                job_id_val: int = cast(int, j.id)
                                await _export_messages_per_channel(
                                    job_id=job_id_val, mm_user_id=mm_user_id
                                )
                                dt = asyncio.get_event_loop().time() - t0
                                backend_logger.info(
                                    f"Экспорт сообщений завершён за {dt:.2f}s (job_id={j.id})"
                                )
                                # After message export pass, aggregate failed categories for this job
                                await _aggregate_failed_summary(job_id_val, "message")
                            else:
                                queue = asyncio.Queue()
                                entities = await get_entities_to_export(
                                    entity_type, job_id=j.id
                                )
                                for entity in entities:
                                    if entity_type == "reaction":
                                        # Aggregate instead of verbose per-entity debug
                                        _record_reaction(
                                            str(getattr(entity, "slack_id", ""))
                                        )
                                    else:
                                        backend_logger.debug(
                                            f"[EXPORT] enqueue {entity_type} {entity.slack_id}"
                                        )
                                    await queue.put((entity, exporter_cls))
                                if entity_type == "attachment":
                                    workers_for_type = int(
                                        os.getenv("ATTACHMENT_WORKERS", workers_count)
                                    )
                                else:
                                    workers_for_type = workers_count
                                backend_logger.debug(
                                    f"[EXPORT] starting {workers_for_type} workers for {entity_type} (job_id={j.id})"
                                )
                                workers = [
                                    asyncio.create_task(
                                        export_worker(queue, mm_user_id)
                                    )
                                    for _ in range(workers_for_type)
                                ]
                                await queue.join()
                                for _ in workers:
                                    await queue.put(None)
                                await asyncio.gather(*workers)
                            backend_logger.info(
                                f"Экспорт {entity_type} завершён (job_id={j.id})"
                            )
                            # Aggregate failed categories for this job & type (only if failures exist)
                            if entity_type != "message":
                                await _aggregate_failed_summary(
                                    int(cast(int, j.id)), entity_type
                                )
                    # If still any pending/skipped of this type (including newly-exporting jobs), loop again
                    jobs = await _fetch_exporting_jobs()
                    if not await _has_pending_for_type(entity_type, jobs):
                        break

                # After completing reaction export, re-archive channels that were originally archived
                if entity_type == "reaction" and archived_channel_ids:
                    backend_logger.info(
                        f"Re-archiving {len(archived_channel_ids)} channels after reaction export"
                    )
                    await _rearchive_channels(archived_channel_ids)

            # After completing all types for these jobs, perform cleanup & mark them done
            try:
                from sqlalchemy import update
                import shutil

                async with SessionLocal() as session:
                    for j in jobs:
                        # Attempt cleanup of extract_dir if still present in meta
                        meta = j.meta or {}
                        extract_dir = meta.get("extract_dir")
                        if extract_dir and isinstance(extract_dir, str):
                            try:
                                shutil.rmtree(extract_dir, ignore_errors=True)
                                backend_logger.debug(
                                    f"[CLEANUP] remove extract_dir for job_id={j.id}: {extract_dir}"
                                )
                            except Exception:  # pragma: no cover
                                pass
                        # Remove extract_dir key from meta
                        if meta.get("extract_dir"):
                            meta.pop("extract_dir", None)
                        setattr(j, "meta", meta)
                        await session.execute(
                            update(ImportJob)
                            .where(ImportJob.id == j.id)
                            .values(current_stage="done", status=JobStatus.success)
                        )
                    await session.commit()
            except Exception as ex:  # noqa: BLE001
                backend_logger.error(f"Не удалось завершить задачи: {ex}")

            # If called for a specific job, stop after finishing it (but only after all earlier jobs)
            if (
                anchor_cutoff is not None
                and jobs
                and (jobs[-1].created_at, jobs[-1].id) >= anchor_cutoff
            ):
                break


async def _export_messages_per_channel(job_id: int, mm_user_id: str) -> None:
    """Export messages grouped by channel, processing each channel sequentially
    while allowing multiple channels to run in parallel. Preserves thread and
    chronological order within a channel by sorting roots first then ts.
    """
    from app.models.entity_relation import EntityRelation

    # Relation batching helpers keep IN() queries within safe limits when
    # millions of message ids are pending.
    def _chunked(seq, chunk_size):
        for i in range(0, len(seq), chunk_size):
            yield seq[i : i + chunk_size]

    relation_chunk_default = os.getenv(
        "EXPORT_RELATION_CHUNK", str(DEFAULT_RELATION_CHUNK)
    )
    relation_chunk = max(1000, int(relation_chunk_default))

    # Concurrency controls
    max_channels = int(
        os.getenv("EXPORT_CHANNEL_CONCURRENCY", os.getenv("EXPORT_WORKERS", 4))
    )

    # Load pending/skipped/failed messages for this job and their channel relations
    async with SessionLocal() as session:
        cond = (
            (Entity.entity_type == "message")
            & (
                Entity.status.in_(
                    [MappingStatus.pending, MappingStatus.skipped, MappingStatus.failed]
                )
            )
            & (Entity.job_id == job_id)
        )
        msg_rows = await session.execute(select(Entity).where(cond))
        messages = list(msg_rows.scalars().all())
        if not messages:
            return

        ids = [m.id for m in messages]
        # posted_in: message -> channel
        # Relation tables can hold millions of rows; batching keeps the IN() parameter
        # count well below driver limits and avoids exhausting statement caches.
        msg_to_channel = {}
        if ids:
            for chunk in _chunked(ids, relation_chunk):
                rel_rows = await session.execute(
                    select(
                        EntityRelation.from_entity_id, EntityRelation.to_entity_id
                    ).where(
                        (EntityRelation.relation_type == "posted_in")
                        & (EntityRelation.from_entity_id.in_(chunk))
                    )
                )
                for from_id, to_id in rel_rows.all():
                    msg_to_channel[from_id] = to_id

        # Identify replies for sorting
        reply_set = set()
        if ids:
            for chunk in _chunked(ids, relation_chunk):
                reply_rows = await session.execute(
                    select(EntityRelation.from_entity_id).where(
                        (EntityRelation.relation_type == "thread_reply")
                        & (EntityRelation.from_entity_id.in_(chunk))
                    )
                )
                reply_set.update(row[0] for row in reply_rows.all())

        # Group messages by channel entity id
        groups: dict[int, list[Entity]] = {}
        for m in messages:
            ch = msg_to_channel.get(m.id)
            if ch is None:
                # messages without channel relation go to a special group key -1
                ch = -1
            groups.setdefault(ch, []).append(m)

        def ts_key(ent: Entity) -> float:
            return parse_slack_ts(ent.slack_id)

        # Sort each channel: roots first then ts asc
        for ch_id, lst in groups.items():
            lst.sort(key=lambda ent: (0 if ent.id not in reply_set else 1, ts_key(ent)))

    sem = asyncio.Semaphore(max_channels)

    # shared caches across channels for this job export
    from .message_exporter import MessageCaches

    caches: MessageCaches = {
        "channel_mm_id_by_slack_id": {},
        "channel_name_by_slack_id": {},
        "user_mm_id_by_slack_id": {},
        "username_by_slack_id": {},
        "membership_seen": set(),
        "emoji_name_by_slack_name": {},
    }

    async def _run_channel(ch_id: int, ents: list[Entity]):
        """Export messages for a single channel in two phases:
        1. Root messages (those not in reply_set) first.
        2. Reply messages afterwards (ensures parent post_ids are mostly available).
        Optional timing logs if EXPORT_MESSAGE_TIMINGS=1.
        """
        async with sem:
            roots: list[Entity] = []
            replies: list[Entity] = []
            for e in ents:
                if e.id in reply_set:
                    replies.append(e)
                else:
                    roots.append(e)

            timing = os.getenv("EXPORT_MESSAGE_TIMINGS") in ("1", "true", "True")

            async def _export_list(lst: list[Entity], phase: str):
                for e in lst:
                    exporter = MessageExporter(e, caches=caches)
                    start = asyncio.get_event_loop().time() if timing else None
                    try:
                        await exporter.export_entity()
                        if timing and start is not None:
                            dt = asyncio.get_event_loop().time() - start
                            backend_logger.debug(
                                f"[MSG_TIMING] job={job_id} channel={ch_id} phase={phase} slack_ts={e.slack_id} dt={dt:.3f}s"
                            )
                    except Exception as ex:  # noqa: BLE001
                        backend_logger.error(
                            f"Ошибка экспорта сообщения {e.slack_id} в канале {ch_id}: {ex}"
                        )
                        try:
                            await exporter.set_status("failed", error=str(ex))
                        except Exception:
                            pass

            # Phase 1: roots
            await _export_list(roots, "roots")
            # Phase 2: replies
            await _export_list(replies, "replies")

    tasks = [
        asyncio.create_task(_run_channel(ch_id, ents)) for ch_id, ents in groups.items()
    ]
    if tasks:
        await asyncio.gather(*tasks)


async def _aggregate_failed_summary(job_id: int, entity_type: str) -> None:
    """Aggregate failed entities of a given type for a job into meta.failed_summary.

    Stores structure:
      meta.failed_summary[entity_type] = {
         'total_failed': N,
         'top_categories': [ {'category': cat, 'count': c}, ... up to 10 ],
         'sample_errors': [first up to 5 distinct raw error_message strings]
      }
    Safe to call repeatedly; it overwrites the section each time.
    """
    from sqlalchemy import select as _select

    async with SessionLocal() as session:
        q = await session.execute(
            _select(Entity.error_message).where(
                (Entity.job_id == job_id)
                & (Entity.entity_type == entity_type)
                & (Entity.status == MappingStatus.failed)
                & (Entity.error_message.is_not(None))
            )
        )
        rows = [r[0] for r in q.all() if r[0]]
        if not rows:
            return
        # Categorize
        cats = [_categorize_error(err) for err in rows]
        counter = Counter(cats)
        top_items = counter.most_common(10)
        sample = []
        seen = set()
        for err in rows:
            if err not in seen:
                seen.add(err)
                sample.append(err)
            if len(sample) >= 5:
                break
        # Update meta JSONB atomically (merge pattern)
        from app.services.backup.meta_utils import merge_job_meta

        await merge_job_meta(
            job_id,
            nested={
                "failed_summary": {
                    entity_type: {
                        "total_failed": len(rows),
                        "top_categories": [
                            {"category": cat, "count": cnt} for cat, cnt in top_items
                        ],
                        "sample_errors": sample,
                    }
                }
            },
        )
        backend_logger.info(
            f"[FAILED_SUMMARY] job_id={job_id} type={entity_type} total_failed={len(rows)} top={top_items[:3]}"
        )

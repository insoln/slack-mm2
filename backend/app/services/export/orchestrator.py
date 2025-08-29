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
from app.utils.filters import job_scoped_condition

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
        cond = (Entity.entity_type == entity_type) & (
            Entity.status.in_(
                [MappingStatus.pending, MappingStatus.skipped, MappingStatus.failed]
            )
        )
        cond = job_scoped_condition(cond, entity_type, job_id)

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
            await exporter.export_entity()
        except Exception as e:
            backend_logger.error(
                f"Ошибка экспорта {entity.entity_type} {entity.slack_id}: {e}"
            )
            try:
                # Ensure status is not left pending on crash
                if exporter is not None:
                    await exporter.set_status("failed", error=str(e))
                else:
                    # fallback: direct update
                    from sqlalchemy import update

                    async with SessionLocal() as session:
                        where_cond = (Entity.entity_type == entity.entity_type) & (
                            Entity.slack_id == entity.slack_id
                        )
                        # Scope by job only for job-scoped types (message/reaction/attachment)
                        where_cond = job_scoped_condition(
                            where_cond,
                            entity.entity_type,
                            getattr(entity, "job_id", None),
                        )
                        await session.execute(
                            update(Entity)
                            .where(where_cond)
                            .values(status="failed", error_message=str(e))
                        )
                        await session.commit()
            except Exception:
                pass
        queue.task_done()


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

        # Strict FIFO by upload time: always process the earliest uploaded running job first.
        # If a later job has reached 'exporting' earlier, we WAIT until earlier jobs reach 'exporting'.
        # Optional anchor: if job_id is provided, only consider jobs uploaded up to that anchor.
        anchor_cutoff: tuple | None = None
        async with SessionLocal() as session:
            if job_id is not None:
                anc = await session.get(ImportJob, job_id)
                if anc is not None:
                    anchor_cutoff = (anc.created_at, anc.id)

        sleep_s = float(os.getenv("EXPORT_QUEUE_POLL", str(EXPORT_QUEUE_POLL_DEFAULT)))
        while True:
            # Pick the earliest job in 'running' state up to anchor (if any)
            async with SessionLocal() as session:
                base = select(ImportJob).where(ImportJob.status == JobStatus.running)
                if anchor_cutoff is not None:
                    # created_at, id tuple ordering
                    base = base.where(
                        (ImportJob.created_at < anchor_cutoff[0])
                        | (
                            (ImportJob.created_at == anchor_cutoff[0])
                            & (ImportJob.id <= anchor_cutoff[1])
                        )
                    )
                base = base.order_by(
                    ImportJob.created_at.asc(), ImportJob.id.asc()
                ).limit(1)
                row = await session.execute(base)
                current: ImportJob | None = row.scalars().first()

            if not current:
                backend_logger.info("Очередь экспорта пуста — выходим")
                break

            # If earliest job hasn't reached 'exporting' yet, wait and retry
            cur_stage: str = cast(str, current.current_stage)
            if cur_stage != "exporting":
                backend_logger.info(
                    f"Ожидание: job_id={current.id} ещё не в стадии 'exporting' (текущая: {cur_stage}), "
                    "ждём, чтобы сохранить порядок загрузки"
                )
                await asyncio.sleep(sleep_s)
                # Loop back and re-evaluate (may detect failure/done or stage change)
                continue

            # Process the earliest job now that it's exporting
            j = current
            backend_logger.info(
                f"Начинаю экспорт для job_id={j.id} (загружено: {j.created_at})"
            )
            for entity_type, exporter_cls in EXPORT_ORDER:
                backend_logger.info(
                    f"Экспорт сущностей типа {entity_type} (job_id={j.id})"
                )
                # Special handling: messages exported in parallel per channel with in-channel ordering
                if entity_type == "message":
                    t0 = asyncio.get_event_loop().time()
                    # Ensure we pass a plain int job_id (avoid SQLAlchemy Column type confusion)
                    job_id_val: int = cast(int, j.id)
                    await _export_messages_per_channel(
                        job_id=job_id_val, mm_user_id=mm_user_id
                    )
                    dt = asyncio.get_event_loop().time() - t0
                    backend_logger.info(
                        f"Экспорт сообщений завершён за {dt:.2f}s (job_id={j.id})"
                    )
                else:
                    queue = asyncio.Queue()
                    entities = await get_entities_to_export(entity_type, job_id=j.id)
                    for entity in entities:
                        backend_logger.debug(
                            f"[EXPORT] enqueue {entity_type} {entity.slack_id}"
                        )
                        await queue.put((entity, exporter_cls))
                    if entity_type == "attachment":
                        # Dedicated throttle for attachments if provided
                        workers_for_type = int(
                            os.getenv("ATTACHMENT_WORKERS", workers_count)
                        )
                    else:
                        workers_for_type = workers_count
                    backend_logger.debug(
                        f"[EXPORT] starting {workers_for_type} workers for {entity_type}"
                    )
                    workers = [
                        asyncio.create_task(export_worker(queue, mm_user_id))
                        for _ in range(workers_for_type)
                    ]
                    await queue.join()
                    for _ in workers:
                        await queue.put(None)
                    await asyncio.gather(*workers)
                backend_logger.info(f"Экспорт {entity_type} завершён (job_id={j.id})")
            backend_logger.info(f"Экспорт job_id={j.id} завершён")

            # Mark job as completed to let the scheduler advance to the next one
            try:
                from sqlalchemy import update

                async with SessionLocal() as session:
                    await session.execute(
                        update(ImportJob)
                        .where(ImportJob.id == j.id)
                        .values(current_stage="done", status=JobStatus.success)
                    )
                    await session.commit()
            except Exception as ex:  # noqa: BLE001
                backend_logger.error(
                    f"Не удалось обновить статус job_id={j.id} на done: {ex}"
                )

            # If called for a specific job, stop after finishing it (but only after all earlier jobs)
            if anchor_cutoff is not None and (j.created_at, j.id) >= anchor_cutoff:
                break


async def _export_messages_per_channel(job_id: int, mm_user_id: str) -> None:
    """Export messages grouped by channel, processing each channel sequentially
    while allowing multiple channels to run in parallel. Preserves thread and
    chronological order within a channel by sorting roots first then ts.
    """
    from app.models.entity_relation import EntityRelation

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
        rel_rows = await session.execute(
            select(EntityRelation.from_entity_id, EntityRelation.to_entity_id).where(
                (EntityRelation.relation_type == "posted_in")
                & (EntityRelation.from_entity_id.in_(ids))
            )
        )
        msg_to_channel = {mid: cid for (mid, cid) in rel_rows.all()}

        # Identify replies for sorting
        reply_rows = await session.execute(
            select(EntityRelation.from_entity_id).where(
                (EntityRelation.relation_type == "thread_reply")
                & (EntityRelation.from_entity_id.in_(ids))
            )
        )
        reply_set = {row[0] for row in reply_rows.all()}

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
    caches = {
        "channel_mm_id_by_slack_id": {},
        "channel_name_by_slack_id": {},
        "user_mm_id_by_slack_id": {},
        "username_by_slack_id": {},
        "membership_seen": set(),
    }

    async def _run_channel(ch_id: int, ents: list[Entity]):
        async with sem:
            for e in ents:
                exporter = MessageExporter(e, caches=caches)
                try:
                    await exporter.export_entity()
                except Exception as ex:  # noqa: BLE001
                    backend_logger.error(
                        f"Ошибка экспорта сообщения {e.slack_id} в канале {ch_id}: {ex}"
                    )
                    try:
                        await exporter.set_status("failed", error=str(ex))
                    except Exception:
                        pass

    tasks = [
        asyncio.create_task(_run_channel(ch_id, ents)) for ch_id, ents in groups.items()
    ]
    if tasks:
        await asyncio.gather(*tasks)

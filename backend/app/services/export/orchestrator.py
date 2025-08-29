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
                jobs = [
                    r
                    for r in rows.scalars().all()
                    if cast(str, r.current_stage) == "exporting"
                ]
                return jobs

        async def _has_pending_for_type(
            entity_type: str, jobs: list[ImportJob]
        ) -> bool:
            # Check if there are any entities of this type still pending across provided jobs
            async with SessionLocal() as s:
                from sqlalchemy import select, and_

                cond = (Entity.entity_type == entity_type) & (
                    Entity.status == MappingStatus.pending
                )
                if entity_type in ("message", "reaction", "attachment"):
                    ids = [int(cast(int, j.id)) for j in jobs]
                    if not ids:
                        return False
                    cond = and_(cond, Entity.job_id.in_(ids))
                q = select(Entity.id).where(cond).limit(1)
                res = await s.execute(q)
                return res.scalar_one_or_none() is not None

        while True:
            jobs = await _fetch_exporting_jobs()
            if not jobs:
                # If there are running jobs but not yet exporting, wait for earliest to reach exporting
                async with SessionLocal() as s:
                    q2 = select(ImportJob).where(ImportJob.status == JobStatus.running)
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
                cur_stage = cast(str, earliest.current_stage)
                backend_logger.info(
                    f"Ожидание: job_id={earliest.id} ещё не в стадии 'exporting' (текущая: {cur_stage}), ждём барьер типов"
                )
                await asyncio.sleep(sleep_s)
                continue

            # Global per-type barrier: complete each type across all exporting jobs in FIFO order
            backend_logger.info(
                f"Запуск экспорта с глобальным барьером типов для {len(jobs)} задач"
            )
            for entity_type, exporter_cls in EXPORT_ORDER:
                # Repeat the type until no exporting job has pending/skipped entities of this type
                while True:
                    jobs = await _fetch_exporting_jobs()
                    if not jobs:
                        break
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
                        backend_logger.info(f"Экспорт {entity_type} завершён (global)")
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
                            else:
                                queue = asyncio.Queue()
                                entities = await get_entities_to_export(
                                    entity_type, job_id=j.id
                                )
                                for entity in entities:
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
                    # If still any pending/skipped of this type (including newly-exporting jobs), loop again
                    jobs = await _fetch_exporting_jobs()
                    if not await _has_pending_for_type(entity_type, jobs):
                        break

            # After completing all types for these jobs, mark them done
            try:
                from sqlalchemy import update

                async with SessionLocal() as session:
                    for j in jobs:
                        await session.execute(
                            update(ImportJob)
                            .where(ImportJob.id == j.id)
                            .values(current_stage="done", status=JobStatus.success)
                        )
                    await session.commit()
            except Exception as ex:  # noqa: BLE001
                backend_logger.error(
                    f"Не удалось обновить статус завершённых задач: {ex}"
                )

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
    from .message_exporter import MessageCaches

    caches: MessageCaches = {
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

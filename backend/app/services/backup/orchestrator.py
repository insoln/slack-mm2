from __future__ import annotations

"""Unified single-pass Slack import orchestrator.

Stages (sequential, deterministic, STRICT FIFO for export phase):
    extracting -> users -> channels -> messages (single-pass: messages+reactions+attachments+emojis) -> ready_export -> exporting -> done

New stage `ready_export` is an explicit barrier: job finishes ingestion and waits
its turn (by creation order) before being promoted to `exporting` by the global
export orchestrator. This prevents later (smaller) jobs from triggering export
of global (user/channel/emoji) entities that logically belong to earlier jobs.
The import orchestrator no longer sets `exporting` or `done`; those transitions
are performed inside the export orchestrator under a global lock.

Legacy standalone stages (reactions / attachments / emojis) have been removed. Counters are
maintained incrementally via atomic JSONB updates from the message importer callbacks.
"""

import os
import glob
import tempfile
import shutil
import time
import asyncio
import logging
from typing import Any, Dict, cast

from sqlalchemy import text as _text

from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.services.export.orchestrator import orchestrate_mm_export
from app.services.entities.custom_emoji import get_slack_emoji_list
from app.services.backup.zip_utils import extract_zip
from .meta_utils import merge_job_meta

from .users_import import parse_users
from .channels_import import parse_channels_and_chats, find_channel_for_folder
from .messages_import import parse_messages_and_related


async def orchestrate_slack_import(zip_path: str):  # noqa: C901 (keep readable)
    """High-level orchestration for unified single-pass import.

    Creates an ImportJob, extracts the provided zip archive, imports users, channels,
    then performs a single pass over all message JSON files (recording reactions,
    attachments and emojis inline) and finally triggers Mattermost export.
    """

    # 1. Create job record
    async with SessionLocal() as session:
        job_obj = ImportJob(
            status=JobStatus.running,
            current_stage="extracting",
            meta={"zip_path": zip_path},
        )
        # Use merge instead of add to cooperate with AsyncMock in tests (awaitable path)
        job_obj = await session.merge(job_obj)
        await session.commit()
        await session.refresh(job_obj)
        job_pk = getattr(job_obj, "id")
        try:
            job_id: int = int(job_pk)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover
            job_id = cast(int, job_pk)  # fallback

    extract_dir = tempfile.mkdtemp(prefix="slack-extract-")

    # Persist extract_dir early so /jobs can introspect (atomic)
    await merge_job_meta(job_id, set={"extract_dir": extract_dir})

    single_pass = True  # always single-pass now
    # Record stage durations when DEBUG logging is enabled
    record_durations = backend_logger.isEnabledFor(logging.DEBUG)

    # Persist mode flag and initialize durations container
    init_nested: Dict[str, Dict[str, Any]] = {}
    if record_durations:
        init_nested["durations"] = {}
    await merge_job_meta(job_id, set={"single_pass": True}, nested=init_nested)

    # --- Extract ---
    backend_logger.info(f"Распаковываю архив {zip_path} в {extract_dir}")
    _stage_start = time.time()
    await extract_zip(zip_path, extract_dir)
    if not os.path.exists(os.path.join(extract_dir, "users.json")):
        raise RuntimeError(
            "Invalid archive format: users.json not found at root (no wrapper directories allowed)"
        )
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    durs = meta.get("durations", {}) or {}
                    durs["extracting"] = _dur
                    meta["durations"] = durs
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        except Exception:  # pragma: no cover
            pass

    # Slack emoji list for later stage (single-pass usage)
    emoji_list = await get_slack_emoji_list()

    # Helper to count JSON files for progress UI; supports flat archive only (validated earlier)
    def _json_files_count(base_dir: str) -> tuple[int, dict[str, bool]]:
        top_files = [
            "users.json",
            "channels.json",
            "groups.json",
            "dms.json",
            "mpims.json",
        ]
        presence: dict[str, bool] = {}
        total = 0
        for fname in top_files:
            exists = os.path.exists(os.path.join(base_dir, fname))
            presence[fname] = exists
            if exists:
                total += 1
        # Count per-channel message JSON files inside folders
        for entry in os.listdir(base_dir):
            p = os.path.join(base_dir, entry)
            if os.path.isdir(p):
                for _ in glob.glob(os.path.join(p, "*.json")):
                    total += 1
        return total, presence

    json_total, json_presence = _json_files_count(extract_dir)
    await merge_job_meta(
        job_id,
        set={"json_files_total": int(json_total)},
        incr={"json_files_processed": 0},  # ensures key exists (no-op)
    )

    # --- Users ---
    # Update actual column for stage transition (export orchestrator relies on column, not meta)
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job:
            setattr(job, "current_stage", "users")
            await session.commit()
    # (Optional) also reflect in meta for completeness
    # current_stage now kept only in ImportJob column (meta key removed)
    backend_logger.info("Архив распакован. Начинаю парсинг пользователей…")
    _stage_start = time.time()
    # IMPORTANT: pass job_id so derived counters in /jobs (which filter by job_id) can see these entities.
    users_res = await parse_users(extract_dir, job_id=job_id)
    users_created = users_res.get("created", 0) if isinstance(users_res, dict) else 0
    users_discovered = (
        users_res.get("discovered", 0) if isinstance(users_res, dict) else 0
    )
    users_existing = users_res.get("existing", 0) if isinstance(users_res, dict) else 0
    await merge_job_meta(
        job_id,
        set={
            "users_discovered": int(users_discovered),
            "users_created": int(users_created),
            "users_existing": int(users_existing),
            # Backwards compatibility for legacy UI expecting users_processed
            "users_processed": int(users_discovered),
        },
    )
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await merge_job_meta(job_id, nested={"durations": {"users": _dur}})
    backend_logger.info(
        f"Импорт пользователей завершён. Обнаружено={users_discovered} создано={users_created} существовало={users_existing}"
    )
    if json_presence.get("users.json"):
        await merge_job_meta(job_id, incr={"json_files_processed": 1})
    # Totals will be consolidated later; no separate atomic needed here.

    # --- Channels ---
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job:
            setattr(job, "current_stage", "channels")
            await session.commit()
    # meta current_stage no longer updated
    _stage_start = time.time()
    channels_res = await parse_channels_and_chats(extract_dir, job_id=job_id)
    ch_created = channels_res.get("created", 0) if isinstance(channels_res, dict) else 0
    ch_discovered = (
        channels_res.get("discovered", 0) if isinstance(channels_res, dict) else 0
    )
    ch_existing = (
        channels_res.get("existing", 0) if isinstance(channels_res, dict) else 0
    )
    await merge_job_meta(
        job_id,
        set={
            "channels_discovered": int(ch_discovered),
            "channels_created": int(ch_created),
            "channels_existing": int(ch_existing),
            "channels_processed": int(ch_discovered),  # backward compat
        },
    )
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await merge_job_meta(job_id, nested={"durations": {"channels": _dur}})
    backend_logger.info(
        f"Импорт каналов завершён. Обнаружено={ch_discovered} создано={ch_created} существовало={ch_existing}"
    )
    top_channel_files = ["channels.json", "groups.json", "dms.json", "mpims.json"]
    add = sum(1 for f in top_channel_files if json_presence.get(f))
    if add:
        await merge_job_meta(job_id, incr={"json_files_processed": add})

    folder_channel_map = find_channel_for_folder(extract_dir, [])
    backend_logger.debug(
        f"Сопоставление папок и каналов/групп/чатов: {len(folder_channel_map)}"
    )
    # Channel mapping diagnostics: identify unmapped folders (value is None)
    try:
        unmapped = [f for f, ch in folder_channel_map.items() if not ch]
        if unmapped:
            sample = unmapped[:25]
            await merge_job_meta(
                job_id,
                nested={
                    "channel_mapping": {
                        "unmapped": {
                            "total": len(unmapped),
                            "sample": sample,
                        }
                    }
                },
            )
            backend_logger.info(
                f"[CHANNEL_MAP] unmapped_folders total={len(unmapped)} sample={sample}"
            )
        else:
            await merge_job_meta(
                job_id,
                nested={"channel_mapping": {"unmapped": {"total": 0, "sample": []}}},
            )
            backend_logger.info("[CHANNEL_MAP] all folders mapped")
    except Exception as e:  # pragma: no cover
        backend_logger.error(f"Channel mapping diagnostics failed: {e}")

    # Initialize totals container if absent
    await merge_job_meta(
        job_id,
        set={
            "stages": [
                "extracting",
                "users",
                "channels",
                "messages",
                "exporting",
                "done",
            ]
        },
        nested={"totals": {}},
    )

    # --- Messages (unified) ---
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job:
            setattr(job, "current_stage", "messages")
            await session.commit()
    # meta current_stage no longer updated

    async def _progress_messages(delta: int):
        if delta:
            await merge_job_meta(job_id, incr={"messages_processed": delta})

    async def _progress_msg_files(delta_files: int):
        if delta_files:
            await merge_job_meta(job_id, incr={"json_files_processed": delta_files})

    async def _counters(delta: dict):
        if not delta:
            return
        incr: Dict[str, int] = {}
        max_keys: Dict[str, int] = {}
        if delta.get("reactions"):
            incr["reactions_processed"] = int(delta["reactions"])  # type: ignore[arg-type]
        if delta.get("attachments"):
            incr["attachments_processed"] = int(delta["attachments"])  # type: ignore[arg-type]
        if delta.get("emojis"):
            max_keys["emojis_processed"] = int(delta["emojis"])  # type: ignore[arg-type]
        if incr or max_keys:
            await merge_job_meta(job_id, incr=incr, max_keys=max_keys)

    # Run the messages import once (was incorrectly nested inside _counters)
    _stage_start = time.time()
    # Always use sequential processing (concurrency=1) for predictable behavior
    # and to avoid database race conditions and deadlocks
    concurrency = 1

    if concurrency <= 1:
        await parse_messages_and_related(
            extract_dir,
            folder_channel_map,
            emoji_list=emoji_list,
            batch_log_every=1,  # ensure per-message progress for small datasets
            progress_messages=_progress_messages,
            file_progress=_progress_msg_files,
            job_id=job_id,
            single_pass=single_pass,
            counters_callback=_counters,
        )
    else:
        backend_logger.info(
            f"Messages import concurrency enabled (concurrency={concurrency}) for {len(folder_channel_map)} folders"
        )
        sem = asyncio.Semaphore(concurrency)

        async def _channel_task(folder: str, ch: dict):
            async with sem:
                try:
                    await parse_messages_and_related(
                        extract_dir,
                        {folder: ch},
                        emoji_list=emoji_list,
                        batch_log_every=1,
                        progress_messages=_progress_messages,
                        file_progress=_progress_msg_files,
                        job_id=job_id,
                        single_pass=single_pass,
                        counters_callback=_counters,
                    )
                except Exception as e:  # pragma: no cover
                    backend_logger.error(
                        f"Channel import failed for folder={folder}: {e}"
                    )

        tasks = [
            asyncio.create_task(_channel_task(folder, ch))
            for folder, ch in folder_channel_map.items()
            if ch
        ]
        await asyncio.gather(*tasks)

    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await merge_job_meta(job_id, nested={"durations": {"messages": _dur}})

    # Recalculate and persist final CREATED totals plus discovered/existing snapshots.
    # Rationale:
    #  * meta users_discovered/channels_discovered reflect how many Slack objects we saw in the archive
    #  * DB counts (Entity rows for this job_id) reflect how many NEW rows were actually created (global uniqueness may reduce this)
    #  * existing = discovered - created (clamped >= 0)
    #  * totals.* now stores CREATED counts (stable denominator for later export-stage progress)
    #  * meta.discovered.* captures discovered counts for UI that wants to show both
    from sqlalchemy import select, func  # local import to keep top import list minimal
    from app.models.entity import Entity

    try:  # pragma: no cover - defensive block
        async with SessionLocal() as s:
            q = await s.execute(
                select(Entity.entity_type, func.count())
                .where(Entity.job_id == job_id)
                .group_by(Entity.entity_type)
            )
            created_map = {et: int(cnt) for et, cnt in q.all()}
    except Exception:
        created_map = {}

    # Pull discovered counters from meta we have already written
    # (If any are missing, treat as zero; messages_discovered mirrors messages_processed counter.)
    try:
        async with SessionLocal() as s:
            job = await s.get(ImportJob, job_id)
            meta_now: Dict[str, Any] = (
                cast(Dict[str, Any], (job.meta or {})) if job else {}
            )
    except Exception:
        meta_now = {}

    def _g(name: str) -> int:
        v = meta_now.get(name)
        return int(v) if isinstance(v, (int, str)) and str(v).isdigit() else 0

    discovered_users = _g("users_discovered") or _g("users_processed")
    discovered_channels = _g("channels_discovered") or _g("channels_processed")
    discovered_messages = _g("messages_processed")
    discovered_reactions = _g("reactions_processed")
    discovered_attachments = _g("attachments_processed")
    discovered_emojis = _g("emojis_processed")

    created_users = created_map.get("user", 0)
    created_channels = created_map.get("channel", 0)
    created_messages = created_map.get("message", 0)
    created_reactions = created_map.get("reaction", 0)
    created_attachments = created_map.get("attachment", 0)
    created_emojis = created_map.get("custom_emoji", 0)

    existing_users = max(discovered_users - created_users, 0)
    existing_channels = max(discovered_channels - created_channels, 0)
    # For messages/reactions/attachments we usually expect no reuse; still compute for consistency
    existing_messages = max(discovered_messages - created_messages, 0)
    existing_reactions = max(discovered_reactions - created_reactions, 0)
    existing_attachments = max(discovered_attachments - created_attachments, 0)
    existing_emojis = max(discovered_emojis - created_emojis, 0)

    await merge_job_meta(
        job_id,
        nested={
            "totals": {  # CREATED counts (stable denominators)
                "users": created_users,
                "channels": created_channels,
                "messages": created_messages,
                "reactions": created_reactions,
                "attachments": created_attachments,
                "emojis": created_emojis,
            },
            "discovered": {
                "users": discovered_users,
                "channels": discovered_channels,
                "messages": discovered_messages,
                "reactions": discovered_reactions,
                "attachments": discovered_attachments,
                "emojis": discovered_emojis,
            },
            "existing": {
                "users": existing_users,
                "channels": existing_channels,
                "messages": existing_messages,
                "reactions": existing_reactions,
                "attachments": existing_attachments,
                "emojis": existing_emojis,
            },
        },
        set={"totals_frozen": True},
    )

    # --- Ready for Export (FIFO barrier) ---
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job:
            setattr(job, "current_stage", "ready_export")
            await session.commit()
    # meta current_stage no longer updated
    backend_logger.info(
        f"Задача job_id={job_id} завершила импорт и перешла в стадию 'ready_export' (ожидание очереди экспорта)"
    )

    # Trigger export orchestrator (it will promote this and possibly other waiting
    # jobs strictly in creation order). Duration of waiting + export will be
    # measured inside export orchestrator in future enhancement (placeholder here).
    try:
        await orchestrate_mm_export(job_id=job_id)
    except Exception as e:  # pragma: no cover
        backend_logger.error(f"Ошибка запуска экспортёра для job_id={job_id}: {e}")

    # Preserve DEBUG-level gating check visibility for tests ensuring durations logic.
    backend_logger.isEnabledFor(logging.DEBUG)

    # Cleanup will now be handled by export orchestrator upon marking job done.
    # (If needed, a fallback cleanup on process restart can later scan done jobs.)

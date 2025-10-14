from __future__ import annotations

"""Unified single-pass Slack import orchestrator.

Stages (sequential, deterministic):
  extracting -> users -> channels -> messages (single-pass: messages+reactions+attachments+emojis) -> exporting -> done

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
    await merge_job_meta(job_id, set={"current_stage": "users"})
    backend_logger.info("Архив распакован. Начинаю парсинг пользователей…")
    _stage_start = time.time()
    # IMPORTANT: pass job_id so derived counters in /jobs (which filter by job_id) can see these entities.
    users = await parse_users(extract_dir, job_id=job_id)
    # Persist users_processed counter atomically
    await merge_job_meta(
        job_id,
        set={"users_processed": int(len(users) if users else 0)},
    )
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await merge_job_meta(job_id, nested={"durations": {"users": _dur}})
    backend_logger.info(
        f"Импорт пользователей завершён. Всего обработано: {len(users) if users else 0}"
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
    await merge_job_meta(job_id, set={"current_stage": "channels"})
    _stage_start = time.time()
    channels = await parse_channels_and_chats(extract_dir, job_id=job_id)
    await merge_job_meta(
        job_id,
        set={"channels_processed": int(len(channels) if channels else 0)},
    )
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await merge_job_meta(job_id, nested={"durations": {"channels": _dur}})
    backend_logger.info(
        f"Импорт каналов завершён. Всего обработано: {len(channels) if channels else 0}"
    )
    top_channel_files = ["channels.json", "groups.json", "dms.json", "mpims.json"]
    add = sum(1 for f in top_channel_files if json_presence.get(f))
    if add:
        await merge_job_meta(job_id, incr={"json_files_processed": add})

    folder_channel_map = find_channel_for_folder(extract_dir, [])
    backend_logger.debug(
        f"Сопоставление папок и каналов/групп/чатов: {len(folder_channel_map)}"
    )

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
    await merge_job_meta(job_id, set={"current_stage": "messages"})

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

    # Persist final totals based on processed counters (including users & channels)
    totals_sql = _text(
        """
        UPDATE import_jobs
        SET meta = (
          SELECT meta || jsonb_build_object(
            'totals', jsonb_build_object(
              'users', COALESCE((meta->>'users_processed')::int,0),
              'channels', COALESCE((meta->>'channels_processed')::int,0),
              'messages', COALESCE((meta->>'messages_processed')::int,0),
              'reactions', COALESCE((meta->>'reactions_processed')::int,0),
              'attachments', COALESCE((meta->>'attachments_processed')::int,0),
              'emojis', COALESCE((meta->>'emojis_processed')::int,0)
            )
          ) FROM import_jobs WHERE id=:jid
        ) WHERE id=:jid
        """
    )
    try:
        async with SessionLocal() as s:
            await s.execute(totals_sql, {"jid": job_id})
            await s.commit()
    except Exception:  # pragma: no cover
        pass

    # --- Export ---
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job:
            setattr(job, "current_stage", "exporting")
            await session.commit()
    await merge_job_meta(job_id, set={"current_stage": "exporting"})
    _stage_start = time.time()
    await orchestrate_mm_export(job_id=job_id)
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await merge_job_meta(job_id, nested={"durations": {"exporting": _dur}})

    # --- Done ---
    async with SessionLocal() as session:
        from sqlalchemy import update

        await session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(current_stage="done", status=JobStatus.success)
        )
        await session.commit()

    # Cleanup temp dir (on success path) AFTER all stages
    try:
        shutil.rmtree(extract_dir)
        backend_logger.debug(f"Временная директория {extract_dir} удалена")
    except Exception:  # pragma: no cover
        pass
    await merge_job_meta(job_id, remove=["extract_dir"])

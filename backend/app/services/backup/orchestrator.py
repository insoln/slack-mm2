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
from typing import Any, Dict, cast

from sqlalchemy import text as _text

from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.services.export.orchestrator import orchestrate_mm_export
from app.services.entities.custom_emoji import get_slack_emoji_list
from app.services.backup.zip_utils import extract_zip

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
        session.add(job_obj)
        await session.commit()
        await session.refresh(job_obj)
        # SQLAlchemy may present instrumented attribute; obtain plain int
        job_pk = getattr(job_obj, "id")
        try:
            job_id: int = int(job_pk)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover
            job_id = cast(int, job_pk)  # fallback

    extract_dir = tempfile.mkdtemp(prefix="slack-extract-")

    record_durations = os.environ.get("IMPORT_RECORD_STAGE_DURATIONS", "1") in ("1", "true", "TRUE")
    single_pass = True  # architecture enforced

    # Persist early meta (extract_dir + mode + durations container)
    try:
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], job.meta or {})
                meta["extract_dir"] = extract_dir
                meta["single_pass"] = True
                if record_durations and "durations" not in meta:
                    meta["durations"] = {}
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:  # pragma: no cover
        pass

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

    # Slack emoji list for later stage
    emoji_list = await get_slack_emoji_list()

    # Helper placed here (needed before users stage completion logic)
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
        for entry in os.listdir(base_dir):
            p = os.path.join(base_dir, entry)
            if os.path.isdir(p):
                for _ in glob.glob(os.path.join(p, "*.json")):
                    total += 1
        return total, presence

    json_total, json_presence = _json_files_count(extract_dir)
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job:
            meta = cast(Dict[str, Any], (job.meta or {}))
            meta["json_files_total"] = int(json_total)
            meta.setdefault("json_files_processed", 0)
            setattr(job, "meta", meta)  # type: ignore[attr-defined]
            await session.commit()

    # --- Users ---
    await _set_stage(job_id, "users")
    backend_logger.info("Архив распакован. Начинаю парсинг пользователей…")
    _stage_start = time.time()
    users = await parse_users(extract_dir, job_id=None)
    try:
        async with SessionLocal() as session:
            job2 = await session.get(ImportJob, job_id)
            if job2:
                meta2 = cast(Dict[str, Any], (job2.meta or {}))
                meta2["users_processed"] = int(len(users) if users else 0)
                setattr(job2, "meta", meta2)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:  # pragma: no cover
        pass
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await _record_stage_duration(job_id, "users", _dur)
    if json_presence.get("users.json"):
        await _inc_json_files_processed(job_id, 1)
    backend_logger.info(
        f"Импорт пользователей завершён. Всего обработано: {len(users) if users else 0}"
    )

    # --- Channels ---
    await _set_stage(job_id, "channels")
    _stage_start = time.time()
    channels = await parse_channels_and_chats(extract_dir, job_id=None)
    try:
        async with SessionLocal() as session:
            job2 = await session.get(ImportJob, job_id)
            if job2:
                meta2 = cast(Dict[str, Any], (job2.meta or {}))
                meta2["channels_processed"] = int(len(channels) if channels else 0)
                setattr(job2, "meta", meta2)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:  # pragma: no cover
        pass
    _dur = int((time.time() - _stage_start) * 1000)
    if record_durations:
        await _record_stage_duration(job_id, "channels", _dur)
    top_channel_files = ["channels.json", "groups.json", "dms.json", "mpims.json"]
    add = sum(1 for f in top_channel_files if json_presence.get(f))
    if add:
        await _inc_json_files_processed(job_id, add)
    backend_logger.info(
        f"Импорт каналов завершён. Всего обработано: {len(channels) if channels else 0}"
    )
    # Initialize totals container if absent (once users/channels known)
    try:
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], (job.meta or {}))
                meta.setdefault("totals", {"users": 0, "channels": 0, "messages": 0, "reactions": 0, "attachments": 0, "emojis": 0})
                meta.setdefault("stages", ["extracting", "users", "channels", "messages", "exporting", "done"])
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:  # pragma: no cover
        pass

    # Build folder -> channel mapping for message iteration
    folder_channel_map = {}
    for ch in channels or []:
        try:
            if not isinstance(ch, dict):
                continue
            name = ch.get("name")
            cid = ch.get("id")
            if name:
                folder_channel_map[name] = ch
            if cid and cid not in folder_channel_map:
                folder_channel_map[cid] = ch
        except Exception:
            continue
    backend_logger.debug(f"Картирование папок -> каналов: {len(folder_channel_map)} (эвристика)")

    # Stage: messages
    await _set_stage(job_id, "messages")
    start = time.time()

    async def _progress_messages(delta: int):
        if not delta:
            return
        async with SessionLocal() as s:
            await s.execute(
                _text(
                    """
                    UPDATE import_jobs
                    SET meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                        'messages_processed', COALESCE((meta->>'messages_processed')::int,0) + :d
                    )
                    WHERE id=:jid
                    """
                ),
                {"d": int(delta), "jid": job_id},
            )
            await s.commit()

    async def _file_progress(delta_files: int):
        if delta_files:
            await _inc_json_files_processed(job_id, delta_files)

    async def _counters(delta: dict):
        if not delta:
            return
        reactions_inc = int(delta.get("reactions", 0) or 0)
        attachments_inc = int(delta.get("attachments", 0) or 0)
        emojis_candidate = int(delta.get("emojis", 0) or 0)
        if not (reactions_inc or attachments_inc or emojis_candidate):
            return
        async with SessionLocal() as s:
            await s.execute(
                _text(
                    """
                    UPDATE import_jobs
                    SET meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                        'reactions_processed', COALESCE((meta->>'reactions_processed')::int,0) + :r,
                        'attachments_processed', COALESCE((meta->>'attachments_processed')::int,0) + :a,
                        'emojis_processed', GREATEST(COALESCE((meta->>'emojis_processed')::int,0), :e)
                    )
                    WHERE id=:jid
                    """
                ),
                {"r": reactions_inc, "a": attachments_inc, "e": emojis_candidate, "jid": job_id},
            )
            await s.commit()

    for folder, ch in folder_channel_map.items():
        if not ch:
            continue
        await parse_messages_and_related(
            extract_dir,
            {folder: ch},
            emoji_list=emoji_list,
            batch_log_every=1,
            progress_messages=_progress_messages,
            file_progress=_file_progress,
            job_id=job_id,
            single_pass=True,
            counters_callback=_counters,
        )
    if record_durations:
        await _record_stage_duration(job_id, "messages", int((time.time() - start) * 1000))

    # Final totals snapshot
    try:
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], (job.meta or {}))
                totals = meta.get("totals") or {}
                for key in ("users", "channels", "messages", "reactions", "attachments", "emojis"):
                    val = int(meta.get(f"{key}_processed", 0) or 0)
                    if val and val > int(totals.get(key, 0) or 0):
                        totals[key] = val
                meta["totals"] = totals
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:  # pragma: no cover
        pass

    # Stage: exporting
    await _set_stage(job_id, "exporting")
    start = time.time()
    await orchestrate_mm_export(job_id=job_id)
    if record_durations:
        await _record_stage_duration(job_id, "exporting", int((time.time() - start) * 1000))

    # Done
    await _mark_done(job_id)

    # Cleanup
    try:
        shutil.rmtree(extract_dir)
        backend_logger.debug(f"Временная директория {extract_dir} удалена")
    except Exception as e:  # noqa: BLE001
        backend_logger.error(f"Ошибка при удалении временной директории {extract_dir}: {e}")
    try:
        async with SessionLocal() as session:
            job_cleanup = await session.get(ImportJob, job_id)
            if job_cleanup is not None and getattr(job_cleanup, "meta", None) is not None:
                meta = cast(Dict[str, Any], getattr(job_cleanup, "meta"))
                if "extract_dir" in meta:
                    meta.pop("extract_dir", None)
                    setattr(job_cleanup, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Helper internal routines
# ---------------------------------------------------------------------------
async def _set_stage(job_id: int, stage: str):
    async with SessionLocal() as session:
        from sqlalchemy import update

        await session.execute(
            update(ImportJob).where(ImportJob.id == job_id).values(current_stage=stage)
        )
        await session.commit()


async def _record_stage_duration(job_id: int, stage: str, duration_ms: int):
    async with SessionLocal() as session:
        await session.execute(
            _text(
                """
                UPDATE import_jobs
                SET meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                    'durations', (
                        COALESCE(meta->'durations','{}'::jsonb) || jsonb_build_object(:stage, :dur)
                    )
                )
                WHERE id = :jid
                """
            ),
            {"stage": stage, "dur": int(duration_ms), "jid": job_id},
        )
        await session.commit()


async def _inc_json_files_processed(job_id: int, delta: int):
    if not delta:
        return
    async with SessionLocal() as session:
        await session.execute(
            _text(
                """
                UPDATE import_jobs
                SET meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                    'json_files_processed', COALESCE((meta->>'json_files_processed')::int,0) + :d
                )
                WHERE id = :jid
                """
            ),
            {"d": int(delta), "jid": job_id},
        )
        await session.commit()


async def _snapshot_totals(job_id: int):
    async with SessionLocal() as session:
        await session.execute(
            _text(
                """
                UPDATE import_jobs
                SET meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                    'totals', (
                        COALESCE(meta->'totals','{}'::jsonb)
                        || jsonb_build_object('messages', COALESCE((meta->>'messages_processed')::int,0))
                        || jsonb_build_object('reactions', COALESCE((meta->>'reactions_processed')::int,0))
                        || jsonb_build_object('attachments', COALESCE((meta->>'attachments_processed')::int,0))
                        || jsonb_build_object('emojis', COALESCE((meta->>'emojis_processed')::int,0))
                    )
                )
                WHERE id = :jid
                """
            ),
            {"jid": job_id},
        )
        await session.commit()


async def _mark_done(job_id: int):
    async with SessionLocal() as session:
        from sqlalchemy import update

        await session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(current_stage="done", status=JobStatus.success)
        )
        await session.commit()


async def _mark_failed(job_id: int, error_message: str):
    async with SessionLocal() as session:
        from sqlalchemy import update

        await session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(status=JobStatus.failed, error_message=error_message)
        )
        await session.commit()



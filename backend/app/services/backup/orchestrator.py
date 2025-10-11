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

from .users_import import parse_users
from .channels_import import parse_channels_and_chats
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
                meta.setdefault("durations", {} if record_durations else None)
                meta["stages"] = [
                    "extracting",
                    "users",
                    "channels",
                    "messages",
                    "exporting",
                    "done",
                ]
                meta.setdefault(
                    "totals",
                    {"messages": 0, "reactions": 0, "attachments": 0, "emojis": 0},
                )
                meta.setdefault("json_files_processed", 0)
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:  # pragma: no cover
        pass

    try:
        # Stage: extracting
        from app.services.backup.zip_utils import extract_zip

        start = time.time()
        backend_logger.info(f"Распаковка архива {zip_path} -> {extract_dir}")
        await extract_zip(zip_path, extract_dir)
        duration_ms = int((time.time() - start) * 1000)
        if record_durations:
            await _record_stage_duration(job_id, "extracting", duration_ms)

        # Detect nested root (single top-level folder containing expected files)
        try:
            entries = [e for e in os.listdir(extract_dir) if not e.startswith('.')]
            if len(entries) == 1:
                candidate = os.path.join(extract_dir, entries[0])
                if os.path.isdir(candidate):
                    expected_files = {"users.json", "channels.json", "groups.json", "dms.json"}
                    if expected_files.intersection(set(os.listdir(candidate))):
                        backend_logger.info(
                            f"Обнаружена вложенная директория '{entries[0]}', переключаюсь на неё"
                        )
                        extract_dir = candidate
        except Exception:  # pragma: no cover
            pass

        # Pre-count total JSON files (top-level metadata + per-channel message files) for UI progress.
        json_total, json_presence = _json_files_count(extract_dir)
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], job.meta or {})
                    meta["json_files_total"] = int(json_total)
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        except Exception:  # pragma: no cover
            pass

        # Stage: users
        await _set_stage(job_id, "users")
        start = time.time()
        backend_logger.info("Импорт пользователей…")
        users = await parse_users(extract_dir, job_id=None)
        backend_logger.info(f"Импорт пользователей завершён ({len(users) if users else 0})")
        if json_presence.get("users.json"):
            await _inc_json_files_processed(job_id, 1)
        if record_durations:
            await _record_stage_duration(job_id, "users", int((time.time() - start) * 1000))

        # Stage: channels
        await _set_stage(job_id, "channels")
        start = time.time()
        backend_logger.info("Импорт каналов…")
        channels = await parse_channels_and_chats(extract_dir, job_id=None)
        backend_logger.info(f"Импорт каналов завершён ({len(channels) if channels else 0})")
        top_channel_files = ["channels.json", "groups.json", "dms.json", "mpims.json"]
        incr = sum(1 for f in top_channel_files if json_presence.get(f))
        if incr:
            await _inc_json_files_processed(job_id, incr)
        if record_durations:
            await _record_stage_duration(job_id, "channels", int((time.time() - start) * 1000))

        # Build folder -> channel mapping (simple heuristic: folder name == channel name or id)
        folder_channel_map = {}
        try:
            async with SessionLocal() as session:
                # channels already inserted by parse_channels_and_chats into entities; we rely on returned list
                for ch in channels or []:
                    try:
                        name = ch.get("name") if isinstance(ch, dict) else None
                        cid = ch.get("id") if isinstance(ch, dict) else None
                        if name:
                            folder_channel_map[name] = ch
                        if cid and cid not in folder_channel_map:
                            folder_channel_map[cid] = ch
                    except Exception:
                        continue
        except Exception:  # pragma: no cover
            pass
        backend_logger.debug(f"Картирование папок -> каналов: {len(folder_channel_map)} (эвристика)")

        # Prepare emoji list once for detection (map name -> url)
        emoji_list = await get_slack_emoji_list()

        # Stage: messages (single-pass ingestion)
        await _set_stage(job_id, "messages")
        start = time.time()

        # Progress callbacks (atomic JSONB updates) -------------------------
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
            if not delta_files:
                return
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

        # Sequential deterministic iteration over folders
        for folder, ch in folder_channel_map.items():
            if not ch:
                continue
            await parse_messages_and_related(
                extract_dir,
                {folder: ch},
                emoji_list=emoji_list,
                progress_messages=_progress_messages,
                file_progress=_file_progress,
                job_id=job_id,
                single_pass=True,
                counters_callback=_counters,
            )

        if record_durations:
            await _record_stage_duration(job_id, "messages", int((time.time() - start) * 1000))

        # Persist final totals snapshot (max of processed counters so far)
        await _snapshot_totals(job_id)

        # Stage: exporting
        await _set_stage(job_id, "exporting")
        start = time.time()
        await orchestrate_mm_export(job_id=job_id)
        if record_durations:
            await _record_stage_duration(job_id, "exporting", int((time.time() - start) * 1000))

        # Finalize
        await _mark_done(job_id)
    except Exception as e:  # pragma: no cover - error path
        backend_logger.error(f"Оркестратор импорта завершился с ошибкой: {e}")
        await _mark_failed(job_id, str(e))
        raise
    finally:
        # Best-effort cleanup of extraction dir and removal from meta
        try:
            shutil.rmtree(extract_dir)
            backend_logger.debug(f"Удалена временная директория {extract_dir}")
        except Exception as ce:  # noqa: BLE001
            backend_logger.error(f"Не удалось удалить {extract_dir}: {ce}")
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


def _json_files_count(base_dir: str):  # (total_files, presence_map)
    top_files = [
        "users.json",
        "channels.json",
        "groups.json",
        "dms.json",
        "mpims.json",
    ]
    presence: Dict[str, bool] = {}
    total = 0
    for fname in top_files:
        exists = os.path.exists(os.path.join(base_dir, fname))
        presence[fname] = exists
        if exists:
            total += 1
    # Count per-channel message json files
    for entry in os.listdir(base_dir):
        p = os.path.join(base_dir, entry)
        if os.path.isdir(p):
            for _ in glob.glob(os.path.join(p, "*.json")):
                total += 1
    return total, presence


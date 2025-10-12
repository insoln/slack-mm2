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
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "users")
                await session.commit()
        backend_logger.info("Архив распакован. Начинаю парсинг пользователей…")
        _stage_start = time.time()
        # IMPORTANT: pass job_id so derived counters in /jobs (which filter by job_id) can see these entities.
        users = await parse_users(extract_dir, job_id=job_id)
        # Persist users_processed counter (previous logic omitted incremental tracking)
        try:
            async with SessionLocal() as session:
                job2 = await session.get(ImportJob, job_id)
                if job2:
                    meta2 = cast(Dict[str, Any], (job2.meta or {}))
                    meta2["users_processed"] = int(len(users) if users else 0)
                    setattr(job2, "meta", meta2)  # type: ignore[attr-defined]
                    await session.commit()
                    backend_logger.debug(
                        f"[DIAG] After users stage (python commit) users_processed={meta2.get('users_processed')} meta_keys={list(meta2.keys())}"
                    )
        except Exception:  # pragma: no cover
            pass
        # Direct SQL snapshot right after python meta save
        try:
            async with SessionLocal() as s:
                row = await s.execute(
                    _text("SELECT meta::text FROM import_jobs WHERE id=:jid"),
                    {"jid": job_id},
                )
                txt = row.scalar_one_or_none()
                backend_logger.debug(f"[DIAG] DB meta snapshot post-users: {txt}")
        except Exception:  # pragma: no cover
            pass
        _dur = int((time.time() - _stage_start) * 1000)
        if record_durations:
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        durs = meta.get("durations", {}) or {}
                        durs["users"] = _dur
                        meta["durations"] = durs
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
            except Exception:  # pragma: no cover
                pass
        backend_logger.info(
            f"Импорт пользователей завершён. Всего обработано: {len(users) if users else 0}"
        )
        if json_presence.get("users.json"):
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    meta["json_files_processed"] = (
                        int(meta.get("json_files_processed", 0)) + 1
                    )
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        # Atomic SQL update for users_processed + totals.users
        try:
            async with SessionLocal() as s:
                await s.execute(
                    _text(
                        """
                        UPDATE import_jobs
                        SET meta = COALESCE(meta,'{}'::jsonb)
                          || jsonb_build_object(
                               'users_processed', :u_cnt,
                               'totals', (
                                   COALESCE(meta->'totals','{}'::jsonb)
                                   || jsonb_build_object('users', :u_cnt)
                               )
                          )
                        WHERE id = :job_id
                        """
                    ),
                    {"u_cnt": int(len(users) if users else 0), "job_id": job_id},
                )
                await s.commit()
        except Exception:  # pragma: no cover
            pass

        # --- Channels ---
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "channels")
                await session.commit()
        _stage_start = time.time()
        # IMPORTANT: pass job_id so channel entities are associated with this import job.
        channels = await parse_channels_and_chats(extract_dir, job_id=job_id)
        # Persist channels_processed counter
        try:
            async with SessionLocal() as session:
                job2 = await session.get(ImportJob, job_id)
                if job2:
                    meta2 = cast(Dict[str, Any], (job2.meta or {}))
                    meta2["channels_processed"] = int(len(channels) if channels else 0)
                    setattr(job2, "meta", meta2)  # type: ignore[attr-defined]
                    await session.commit()
                    backend_logger.debug(
                        f"[DIAG] After channels stage (python commit) channels_processed={meta2.get('channels_processed')} meta_keys={list(meta2.keys())}"
                    )
        except Exception:  # pragma: no cover
            pass
        # Direct SQL snapshot after channels stage
        try:
            async with SessionLocal() as s:
                row = await s.execute(
                    _text("SELECT meta::text FROM import_jobs WHERE id=:jid"),
                    {"jid": job_id},
                )
                txt = row.scalar_one_or_none()
                backend_logger.debug(f"[DIAG] DB meta snapshot post-channels: {txt}")
        except Exception:  # pragma: no cover
            pass
        _dur = int((time.time() - _stage_start) * 1000)
        if record_durations:
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        durs = meta.get("durations", {}) or {}
                        durs["channels"] = _dur
                        meta["durations"] = durs
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
            except Exception:  # pragma: no cover
                pass
        backend_logger.info(
            f"Импорт каналов завершён. Всего обработано: {len(channels) if channels else 0}"
        )
        top_channel_files = ["channels.json", "groups.json", "dms.json", "mpims.json"]
        add = sum(1 for f in top_channel_files if json_presence.get(f))
        if add:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    meta["json_files_processed"] = (
                        int(meta.get("json_files_processed", 0)) + add
                    )
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        # Atomic SQL update for channels_processed + totals.channels
        try:
            async with SessionLocal() as s:
                await s.execute(
                    _text(
                        """
                        UPDATE import_jobs
                        SET meta = COALESCE(meta,'{}'::jsonb)
                          || jsonb_build_object(
                               'channels_processed', :c_cnt,
                               'totals', (
                                   COALESCE(meta->'totals','{}'::jsonb)
                                   || jsonb_build_object('channels', :c_cnt)
                               )
                          )
                        WHERE id = :job_id
                        """
                    ),
                    {"c_cnt": int(len(channels) if channels else 0), "job_id": job_id},
                )
                await s.commit()
        except Exception:  # pragma: no cover
            pass

        folder_channel_map = find_channel_for_folder(extract_dir, [])
        backend_logger.debug(
            f"Сопоставление папок и каналов/групп/чатов: {len(folder_channel_map)}"
        )

        # Initialize totals container if absent
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], (job.meta or {}))
                meta.setdefault(
                    "totals",
                    {
                        "users": 0,
                        "channels": 0,
                        "messages": 0,
                        "reactions": 0,
                        "attachments": 0,
                        "emojis": 0,
                    },
                )
                meta["stages"] = [
                    "extracting",
                    "users",
                    "channels",
                    "messages",
                    "exporting",
                    "done",
                ]
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()

        # --- Messages (unified) ---
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "messages")
                await session.commit()

        async def _progress_messages(delta: int):
            if not delta:
                return
            async with SessionLocal() as s:
                await s.execute(
                    _text(
                        """
                        UPDATE import_jobs
                        SET meta = COALESCE(meta, '{}'::jsonb)
                            || jsonb_build_object(
                                'messages_processed',
                                COALESCE((meta->>'messages_processed')::int, 0) + :delta
                            )
                        WHERE id = :job_id
                        """
                    ),
                    {"delta": int(delta or 0), "job_id": job_id},
                )
                await s.commit()
                # Diagnostic snapshot every few messages (small dataset so always log)
                if delta:
                    snap = await s.execute(
                        _text(
                            "SELECT (meta->>'messages_processed')::int FROM import_jobs WHERE id=:jid"
                        ),
                        {"jid": job_id},
                    )
                    backend_logger.debug(
                        f"[DIAG] messages_processed updated to {snap.scalar_one_or_none()}"
                    )

        async def _progress_msg_files(delta_files: int):
            if not delta_files:
                return
            async with SessionLocal() as s:
                await s.execute(
                    _text(
                        """
                        UPDATE import_jobs
                        SET meta = COALESCE(meta, '{}'::jsonb)
                            || jsonb_build_object(
                                'json_files_processed',
                                COALESCE((meta->>'json_files_processed')::int, 0) + :delta
                            )
                        WHERE id = :job_id
                        """
                    ),
                    {"delta": int(delta_files or 0), "job_id": job_id},
                )
                await s.commit()

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
                        SET meta = COALESCE(meta, '{}'::jsonb) || jsonb_build_object(
                            'reactions_processed', COALESCE((meta->>'reactions_processed')::int,0) + :r_inc,
                            'attachments_processed', COALESCE((meta->>'attachments_processed')::int,0) + :a_inc,
                            'emojis_processed', GREATEST(COALESCE((meta->>'emojis_processed')::int,0), :emoji_candidate)
                        )
                        WHERE id = :job_id
                        """
                    ),
                    {
                        "r_inc": reactions_inc,
                        "a_inc": attachments_inc,
                        "emoji_candidate": emojis_candidate,
                        "job_id": job_id,
                    },
                )
                await s.commit()

        _stage_start = time.time()
        concurrency = 1
        try:
            concurrency = int(os.environ.get("IMPORT_CHANNEL_CONCURRENCY", "1") or 1)
        except Exception:  # pragma: no cover
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
                f"Messages import concurrency enabled (IMPORT_CHANNEL_CONCURRENCY={concurrency}) for {len(folder_channel_map)} folders"
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
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        durs = meta.get("durations", {}) or {}
                        durs["messages"] = _dur
                        meta["durations"] = durs
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
            except Exception:  # pragma: no cover
                pass

        # Persist final totals based on processed counters (including users & channels) without force fallback
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    totals = meta.get("totals") or {}
                    for key in (
                        "users",
                        "channels",
                        "messages",
                        "reactions",
                        "attachments",
                        "emojis",
                    ):
                        val = int(meta.get(f"{key}_processed", 0) or 0)
                        if val and val > int(totals.get(key, 0) or 0):
                            totals[key] = val
                    meta["totals"] = totals
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        except Exception:  # pragma: no cover
            pass

        # --- Export --- (always executed; skip-export flag removed as per request)
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "exporting")
                await session.commit()
        _stage_start = time.time()
        await orchestrate_mm_export(job_id=job_id)
        _dur = int((time.time() - _stage_start) * 1000)
        if record_durations:
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        durs = meta.get("durations", {}) or {}
                        durs["exporting"] = _dur
                        meta["durations"] = durs
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
            except Exception:  # pragma: no cover
                pass

        # --- Done ---
        async with SessionLocal() as session:
            from sqlalchemy import update

            await session.execute(
                update(ImportJob)
                .where(ImportJob.id == job_id)
                .values(current_stage="done", status=JobStatus.success)
            )
            await session.commit()



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



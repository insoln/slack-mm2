import json
import tempfile
import shutil
from app.logging_config import backend_logger
from typing import Any, Dict, cast
from .users_import import parse_users
from .channels_import import parse_channels_and_chats, find_channel_for_folder
from .messages_import import parse_channel_messages
from .attachments_import import parse_attachments_from_export
from app.services.export.orchestrator import orchestrate_mm_export
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.services.entities.custom_emoji import get_slack_emoji_list
import os
import glob
import ijson
import re
from .custom_emojis_import import parse_custom_emojis_from_export
import time


async def orchestrate_slack_import(zip_path):
    # Create job entry
    job_id = None
    async with SessionLocal() as session:
        job = ImportJob(
            status=JobStatus.running,
            current_stage="extracting",
            meta={"zip_path": zip_path},
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    extract_dir = tempfile.mkdtemp(prefix="slack-extract-")
    # Persist extract_dir for compatibility (e.g., /jobs can derive file totals while import runs)
    try:
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], (job.meta or {}))
                meta["extract_dir"] = extract_dir
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()
    except Exception:
        pass
    try:
        import os as _os
        single_pass = _os.environ.get("IMPORT_SINGLE_PASS", "0") in ("1", "true", "TRUE")
        record_durations = _os.environ.get("IMPORT_RECORD_STAGE_DURATIONS", "1") in ("1", "true", "TRUE")
        # Persist chosen single_pass mode early for frontend logic
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    meta["single_pass"] = bool(single_pass)
                    if record_durations and "durations" not in meta:
                        meta["durations"] = {}
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        except Exception:
            pass
        backend_logger.info(f"Распаковываю архив {zip_path} в {extract_dir}")
        from app.services.backup.zip_utils import extract_zip

        _stage_start = time.time()
        await extract_zip(zip_path, extract_dir)
        _extract_dur = int((time.time() - _stage_start) * 1000)
        if record_durations:
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        durs = meta.get("durations", {}) or {}
                        durs["extracting"] = _extract_dur
                        meta["durations"] = durs
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
            except Exception:
                pass

        # Получаем список эмодзи из Slack API один раз
        emoji_list = await get_slack_emoji_list()

        # Подсчитать общее количество JSON-файлов в бэкапе (для прогресса импорта по файлам)
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
            # Перебрать подпапки (каналы/чаты) и посчитать *.json в каждой
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
                meta["json_files_processed"] = int(
                    meta.get("json_files_processed", 0) or 0
                )
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()

        # users
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "users")
                await session.commit()
        backend_logger.info("Архив распакован. Начинаю парсинг пользователей…")
        _stage_start = time.time()
        users = await parse_users(extract_dir, job_id=None)
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
            except Exception:
                pass
        backend_logger.info(
            f"Импорт пользователей завершён. Всего обработано: {len(users)}"
        )
        # Отметить users.json как обработанный, если он присутствует
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

        # channels
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "channels")
                await session.commit()
        _stage_start = time.time()
        channels = await parse_channels_and_chats(extract_dir, job_id=None)
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
            except Exception:
                pass
        backend_logger.info(
            f"Импорт каналов завершён. Всего обработано: {len(channels)}"
        )
        # Отметить верхнеуровневые файлы каналов как обработанные
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

        folder_channel_map = find_channel_for_folder(extract_dir, [])
        backend_logger.debug(
            f"Сопоставление папок и каналов/групп/чатов: {len(folder_channel_map)}"
        )

        # Initialize empty totals instead of expensive deep pre-scan (will be filled incrementally by stages)
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], (job.meta or {}))
                if "totals" not in meta:
                    meta["totals"] = {
                        "messages": 0,
                        "reactions": 0,
                        "attachments": 0,
                        "emojis": 0,
                    }
                meta["stages"] = [
                    "extracting",
                    "users",
                    "channels",
                    "messages",
                    "emojis",
                    "reactions",
                    "attachments",
                    "exporting",
                    "done",
                ]
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()

        # messages
        from sqlalchemy import text as _text

        async def _progress_messages(delta: int):
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

        async def _progress_msg_files(delta_files: int):
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

        from sqlalchemy import text as _sql_text

        async def _counters(delta: dict):
            # delta contains per-batch increments for reactions / attachments / messages (messages handled separately) and
            # emojis is a current unique total (we keep max).
            if not delta:
                return
            reactions_inc = int(delta.get("reactions", 0) or 0)
            attachments_inc = int(delta.get("attachments", 0) or 0)
            emojis_candidate = int(delta.get("emojis", 0) or 0)
            # Atomic JSONB update: increment counters, keep max for emojis.
            async with SessionLocal() as s:
                await s.execute(
                    _sql_text(
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

        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "messages")
                await session.commit()
        jid = cast(int | None, job_id)
        _stage_start = time.time()
        # Optional channel-level concurrency for messages stage
        concurrency = 1
        try:
            concurrency = int(os.environ.get("IMPORT_CHANNEL_CONCURRENCY", "1") or 1)
        except Exception:
            concurrency = 1
        if concurrency <= 1:
            _ = await parse_channel_messages(
                extract_dir,
                folder_channel_map,
                batch_size=200,
                progress=_progress_messages,
                file_progress=_progress_msg_files,
                job_id=jid,
                single_pass=single_pass,
                counters_callback=_counters if single_pass else None,
                emoji_list=emoji_list if single_pass else None,
            )
        else:
            import asyncio as _asyncio
            backend_logger.info(f"Messages import: concurrency enabled (IMPORT_CHANNEL_CONCURRENCY={concurrency}) for {len(folder_channel_map)} folders")
            sem = _asyncio.Semaphore(concurrency)

            # Wrap callbacks to ensure thread-safety (async DB writes already isolated per call)
            async def _channel_wrapper(folder, ch):
                async with sem:
                    try:
                        # Build a single-item map for this channel
                        single_map = {folder: ch}
                        await parse_channel_messages(
                            extract_dir,
                            single_map,
                            batch_size=200,
                            progress=_progress_messages,
                            file_progress=_progress_msg_files,
                            job_id=jid,
                            single_pass=single_pass,
                            counters_callback=_counters if single_pass else None,
                            emoji_list=emoji_list if single_pass else None,
                        )
                    except Exception as e:
                        backend_logger.error(f"Channel import failed for folder={folder}: {e}")

            tasks = []
            for folder, ch in folder_channel_map.items():
                if not ch:
                    continue
                tasks.append(_asyncio.create_task(_channel_wrapper(folder, ch)))
            # Wait for all tasks
            await _asyncio.gather(*tasks)
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
            except Exception:
                pass

        # Persist final messages total (and single-pass other counters)
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    totals = meta.get("totals") or {}
                    msgs = int(meta.get("messages_processed", 0) or 0)
                    if msgs and msgs > int(totals.get("messages", 0) or 0):
                        totals["messages"] = msgs
                    if single_pass:
                        for key in ("reactions", "attachments", "emojis"):
                            val = int(meta.get(f"{key}_processed", 0) or 0)
                            if val and val > int(totals.get(key, 0) or 0):
                                totals[key] = val
                    meta["totals"] = totals
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()
        except Exception:
            pass

        # emojis (skip if single_pass handled them)
        if not single_pass:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    setattr(job, "current_stage", "emojis")
                    await session.commit()

            async def _progress_emojis(delta: int):
                async with SessionLocal() as s:
                    job = await s.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        meta["emojis_processed"] = int(
                            meta.get("emojis_processed", 0)
                        ) + int(delta or 0)
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await s.commit()

            _stage_start = time.time()
            await parse_custom_emojis_from_export(
                extract_dir,
                folder_channel_map,
                emoji_list,
                progress=_progress_emojis,
            )
            _dur = int((time.time() - _stage_start) * 1000)
            if record_durations:
                try:
                    async with SessionLocal() as session:
                        job = await session.get(ImportJob, job_id)
                        if job:
                            meta = cast(Dict[str, Any], (job.meta or {}))
                            durs = meta.get("durations", {}) or {}
                            durs["emojis"] = _dur
                            meta["durations"] = durs
                            setattr(job, "meta", meta)  # type: ignore[attr-defined]
                            await session.commit()
                except Exception:
                    pass

        if not single_pass:
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        emojis = int(meta.get("emojis_processed", 0) or 0)
                        totals = meta.get("totals") or {}
                        if emojis and (emojis > int(totals.get("emojis", 0) or 0)):
                            totals["emojis"] = emojis
                            meta["totals"] = totals
                            setattr(job, "meta", meta)  # type: ignore[attr-defined]
                            await session.commit()
            except Exception:
                pass


        # attachments (skip if single_pass)
        if not single_pass:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    setattr(job, "current_stage", "attachments")
                    await session.commit()

            async def _progress_attachments(delta: int):
                async with SessionLocal() as s:
                    job = await s.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        meta["attachments_processed"] = int(
                            meta.get("attachments_processed", 0)
                        ) + int(delta or 0)
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await s.commit()

            _stage_start = time.time()
            await parse_attachments_from_export(
                extract_dir,
                folder_channel_map,
                progress=_progress_attachments,
                job_id=jid,
            )
            _dur = int((time.time() - _stage_start) * 1000)
            if record_durations:
                try:
                    async with SessionLocal() as session:
                        job = await session.get(ImportJob, job_id)
                        if job:
                            meta = cast(Dict[str, Any], (job.meta or {}))
                            durs = meta.get("durations", {}) or {}
                            durs["attachments"] = _dur
                            meta["durations"] = durs
                            setattr(job, "meta", meta)  # type: ignore[attr-defined]
                            await session.commit()
                except Exception:
                    pass

        if not single_pass:
            try:
                async with SessionLocal() as session:
                    job = await session.get(ImportJob, job_id)
                    if job:
                        meta = cast(Dict[str, Any], (job.meta or {}))
                        attachments = int(meta.get("attachments_processed", 0) or 0)
                        totals = meta.get("totals") or {}
                        if attachments and (attachments > int(totals.get("attachments", 0) or 0)):
                            totals["attachments"] = attachments
                            meta["totals"] = totals
                            setattr(job, "meta", meta)  # type: ignore[attr-defined]
                            await session.commit()
            except Exception:
                pass

        # export
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
            except Exception:
                pass

        # done
        async with SessionLocal() as session:
            from sqlalchemy import update

            await session.execute(
                update(ImportJob)
                .where(ImportJob.id == job_id)
                .values(current_stage="done", status=JobStatus.success)
            )
            await session.commit()
    except Exception as e:
        backend_logger.error(f"Оркестратор импорта завершился с ошибкой: {e}")
        async with SessionLocal() as session:
            from sqlalchemy import update

            await session.execute(
                update(ImportJob)
                .where(ImportJob.id == job_id)
                .values(status=JobStatus.failed, error_message=str(e))
            )
            await session.commit()
        raise
    finally:
        try:
            shutil.rmtree(extract_dir)
            backend_logger.debug(f"Временная директория {extract_dir} удалена")
        except Exception as e:
            backend_logger.error(
                f"Ошибка при удалении временной директории {extract_dir}: {e}"
            )
        # Cleanup extract_dir from job.meta to avoid leaking temp paths
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    if "extract_dir" in meta:
                        try:
                            del meta["extract_dir"]
                        except Exception:
                            meta["extract_dir"] = None
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
        except Exception:
            pass

import json
import tempfile
import shutil
from typing import Any, Dict, cast, Optional
import os
import glob
import re
import ijson

from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.services.entities.custom_emoji import get_slack_emoji_list
from app.services.export.orchestrator import orchestrate_mm_export

from .users_import import parse_users
from .channels_import import parse_channels_and_chats, find_channel_for_folder
from .messages_import import parse_messages_and_related
from .custom_emojis_import import parse_custom_emojis_from_export


async def orchestrate_slack_import(zip_path: str):
    """Main orchestration for Slack import.

    Steps:
      1. Create ImportJob row
      2. Extract archive
      3. Parse users, channels
      4. Pre-count totals (messages, reactions, attachments, emojis) for progress UI
      5. Import messages, emojis, reactions, attachments
      6. Export to Mattermost
      7. Mark job done / failed
    """

    job_id: Optional[int] = None
    # 1. Create job row
    async with SessionLocal() as session:
        job = ImportJob(
            status=JobStatus.running,
            current_stage="extracting",
            meta={"zip_path": zip_path},
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = cast(int, job.id)

    extract_dir = tempfile.mkdtemp(prefix="slack-extract-")

    # Persist extract_dir early so /jobs can introspect while running
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
        # 2. Extract
        backend_logger.info(f"Распаковываю архив {zip_path} в {extract_dir}")
        from app.services.backup.zip_utils import extract_zip

        await extract_zip(zip_path, extract_dir)

        # Slack emoji list once
        emoji_list = await get_slack_emoji_list()

        # Helper to count json files (for file-level progress bar)
        def _json_files_count(base_dir: str) -> tuple[int, dict[str, bool]]:
            top_files = ["users.json", "channels.json", "groups.json", "dms.json", "mpims.json"]
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
                meta["json_files_processed"] = int(meta.get("json_files_processed", 0) or 0)
                setattr(job, "meta", meta)  # type: ignore[attr-defined]
                await session.commit()

        # 3. Users
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "users")
                await session.commit()
        backend_logger.info("Архив распакован. Начинаю парсинг пользователей…")
        users = await parse_users(extract_dir, job_id=job_id)
        backend_logger.info(f"Импорт пользователей завершён. Всего обработано: {len(users)}")
        if json_presence.get("users.json"):
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    meta["json_files_processed"] = int(meta.get("json_files_processed", 0)) + 1
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()

        # 4. Channels
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "channels")
                await session.commit()
        channels = await parse_channels_and_chats(extract_dir, job_id=job_id)
        backend_logger.info(f"Импорт каналов завершён. Всего обработано: {len(channels)}")
        top_channel_files = ["channels.json", "groups.json", "dms.json", "mpims.json"]
        add = sum(1 for f in top_channel_files if json_presence.get(f))
        if add:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    meta["json_files_processed"] = int(meta.get("json_files_processed", 0)) + add
                    setattr(job, "meta", meta)  # type: ignore[attr-defined]
                    await session.commit()

        folder_channel_map = find_channel_for_folder(extract_dir, [])
        backend_logger.debug(f"Сопоставление папок и каналов/групп/чатов: {len(folder_channel_map)}")

        # 5. Pre-count totals for progress UI
        EMOJI_PATTERN = re.compile(r":([a-z0-9_+\-]+):")
        counts = {"users": len(users), "channels": len(channels), "messages": 0, "reactions": 0, "attachments": 0, "emojis": 0}
        seen_emoji: set[str] = set()
        for folder, _ in folder_channel_map.items():
            folder_path = os.path.join(extract_dir, folder)
            if not os.path.isdir(folder_path):
                continue
            for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
                try:
                    with open(msg_file, "r", encoding="utf-8") as f:
                        for msg in ijson.items(f, "item"):
                            raw = msg or {}
                            counts["messages"] += 1
                            for reaction in raw.get("reactions") or []:
                                users_list = reaction.get("users") or []
                                counts["reactions"] += len(users_list)
                            for file_obj in raw.get("files") or []:
                                url_private = file_obj.get("url_private")
                                if url_private and str(url_private).startswith("https://files.slack.com"):
                                    counts["attachments"] += 1
                            text = raw.get("text") or ""
                            for name in EMOJI_PATTERN.findall(text):
                                seen_emoji.add(name)
                            for a in raw.get("attachments") or []:
                                for key in ("pretext", "title", "text", "fallback"):
                                    val = a.get(key)
                                    if isinstance(val, str):
                                        for name in EMOJI_PATTERN.findall(val):
                                            seen_emoji.add(name)
                            for b in raw.get("blocks") or []:
                                if isinstance(b, dict):
                                    if b.get("type") == "rich_text":
                                        for el in b.get("elements", []) or []:
                                            if isinstance(el, dict):
                                                if el.get("type") in ("text", "mrkdwn", "plain_text"):
                                                    t = el.get("text") or ""
                                                    for name in EMOJI_PATTERN.findall(t):
                                                        seen_emoji.add(name)
                                    else:
                                        t = ((b.get("text") or {}).get("text") if isinstance(b.get("text"), dict) else None)
                                        if t:
                                            for name in EMOJI_PATTERN.findall(t):
                                                seen_emoji.add(name)
                except Exception as e:
                    backend_logger.error(f"Ошибка предподсчёта {msg_file}: {e}")
                    continue

        valid_emoji = 0
        for n in seen_emoji:
            if emoji_list and emoji_list.get(n):
                valid_emoji += 1
        counts["emojis"] = valid_emoji

        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                meta = cast(Dict[str, Any], (job.meta or {}))
                meta["totals"] = counts
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

        # 6. Unified import (messages + reactions + attachments + custom emojis)
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "messages")
                await session.commit()
            # progress callbacks only for legacy file + message increments
            async def _progress_msg_files(delta_files: int):
                from sqlalchemy import text
                async with SessionLocal() as s:
                    await s.execute(
                        text(
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

            await parse_messages_and_related(
                extract_dir,
                folder_channel_map,
                emoji_list=emoji_list,
                file_progress=_progress_msg_files,
                job_id=job_id,
            )

        # 7. Export
        async with SessionLocal() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                setattr(job, "current_stage", "exporting")
                await session.commit()
        await orchestrate_mm_export(job_id=job_id)

        # 8. Done
        async with SessionLocal() as session:
            from sqlalchemy import update
            await session.execute(
                update(ImportJob)
                .where(ImportJob.id == job_id)
                .values(current_stage="done", status=JobStatus.success)
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
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
        # Cleanup temp dir
        try:
            shutil.rmtree(extract_dir)
            backend_logger.debug(f"Временная директория {extract_dir} удалена")
        except Exception as e:  # noqa: BLE001
            backend_logger.error(f"Ошибка при удалении временной директории {extract_dir}: {e}")
        # Remove extract_dir from meta
        try:
            async with SessionLocal() as session:
                job = await session.get(ImportJob, job_id)
                if job:
                    meta = cast(Dict[str, Any], (job.meta or {}))
                    if "extract_dir" in meta:
                        meta.pop("extract_dir", None)
                        setattr(job, "meta", meta)  # type: ignore[attr-defined]
                        await session.commit()
        except Exception:
            pass

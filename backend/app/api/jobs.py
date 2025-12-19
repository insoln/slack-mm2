from fastapi import APIRouter, HTTPException, BackgroundTasks
from sqlalchemy import select, func, update
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
from app.logging_config import backend_logger
import os
import glob
import zipfile

ENTITY_LABEL_TO_TYPE = {
    "users": "user",
    "channels": "channel",
    "messages": "message",
    "reactions": "reaction",
    "attachments": "attachment",
    "emojis": "custom_emoji",
}

router = APIRouter()


def _serialize_job(row: ImportJob) -> dict:
    created_at = getattr(row, "created_at", None)
    updated_at = getattr(row, "updated_at", None)
    return {
        "id": row.id,
        "status": getattr(row.status, "value", row.status),
        "current_stage": row.current_stage,
        "meta": row.meta or {},
        "error_message": row.error_message,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


@router.get("/jobs")
async def list_jobs(limit: int = 50):
    async with SessionLocal() as session:
        res = await session.execute(
            select(ImportJob).order_by(ImportJob.id.desc()).limit(limit)
        )
        rows = res.scalars().all()

        # Pre-compute export status breakdown for all jobs in a single query to avoid N+1
        job_ids = [r.id for r in rows if r.id is not None]
        breakdown: dict[int, dict[str, dict[str, int]]] = {}
        if job_ids:
            br_res = await session.execute(
                select(
                    Entity.job_id,
                    Entity.entity_type,
                    Entity.status,
                    func.count(),
                )
                .where(Entity.job_id.in_(job_ids))  # type: ignore[arg-type]
                .group_by(Entity.job_id, Entity.entity_type, Entity.status)
            )
            for jid, etype, status, cnt in br_res.all():
                jid_int = int(jid)  # defensive
                job_map = breakdown.setdefault(jid_int, {})
                type_map = job_map.setdefault(str(etype), {})
                type_map[str(getattr(status, "value", status))] = int(cnt)

        # Derive per-job totals for job-scoped types if meta.totals absent/empty
        jobs_out = []
        for row in rows:
            data = _serialize_job(row)
            meta = data.get("meta") or {}
            # Backfill file-based import progress if available and missing
            if (
                (
                    not meta.get("json_files_total")
                    or not isinstance(meta.get("json_files_total"), int)
                )
                and bool(meta.get("extract_dir"))
                and data.get("current_stage")
                in {
                    "extracting",
                    "users",
                    "channels",
                    "messages",
                    "emojis",
                    "reactions",
                    "attachments",
                }
            ):
                base_dir = str(meta.get("extract_dir") or "")
                try:
                    total = 0
                    if not base_dir or not os.path.isdir(base_dir):
                        raise RuntimeError("extract_dir not available")
                    # top-level files
                    for fname in (
                        "users.json",
                        "channels.json",
                        "groups.json",
                        "dms.json",
                        "mpims.json",
                    ):
                        if os.path.exists(os.path.join(base_dir, fname)):
                            total += 1
                    # daily message JSONs per channel
                    # Exclude FC: directories (file comments) as they don't contain message data
                    for entry in os.listdir(base_dir):
                        p = os.path.join(base_dir, entry)
                        if os.path.isdir(p) and not entry.startswith("FC:"):
                            total += len(glob.glob(os.path.join(p, "*.json")))
                    meta["json_files_total"] = int(total)
                    # processed remains whatever orchestrator has set; don't derive here to avoid expensive/fragile scans
                    data["meta"] = meta
                except Exception:
                    pass
            # If still no total, try to derive it directly from the uploaded zip archive
            if (
                (
                    not meta.get("json_files_total")
                    or not isinstance(meta.get("json_files_total"), int)
                )
                and bool(meta.get("zip_path"))
                and data.get("current_stage")
                in {
                    "extracting",
                    "users",
                    "channels",
                    "messages",
                    "emojis",
                    "reactions",
                    "attachments",
                }
            ):
                zpath = str(meta.get("zip_path") or "")
                try:
                    if zpath and os.path.exists(zpath):
                        total = 0
                        top_present = {
                            "users.json": False,
                            "channels.json": False,
                            "groups.json": False,
                            "dms.json": False,
                            "mpims.json": False,
                        }
                        with zipfile.ZipFile(zpath, "r") as zf:
                            names = zf.namelist()
                            # Normalize separators and filter directories
                            top_allowed = {
                                "users.json",
                                "channels.json",
                                "groups.json",
                                "dms.json",
                                "mpims.json",
                            }
                            for name in names:
                                if name.endswith("/"):
                                    continue
                                # Remove any leading prefix folders (Slack zips often wrap everything in one folder)
                                parts = [p for p in name.split("/") if p]
                                if not parts:
                                    continue
                                fname = parts[-1]
                                if len(parts) == 1:
                                    # top-level file
                                    if fname in top_allowed:
                                        top_present[fname] = True
                                        total += 1
                                else:
                                    # per-channel daily JSON: any *.json placed under some folder (channel/chat)
                                    # Exclude FC: directories (file comments) as they don't contain message data
                                    top_dir = parts[0] if len(parts) >= 1 else ""
                                    if fname.lower().endswith(
                                        ".json"
                                    ) and not top_dir.startswith("FC:"):
                                        total += 1
                        if total > 0:
                            meta["json_files_total"] = int(total)
                            # Do not synthesize processed here; let the orchestrator update the DB value in real time
                            data["meta"] = meta
                except Exception:
                    # Non-fatal: silently ignore if zip cannot be read
                    pass
            # Only derive (and freeze) totals AFTER import stages complete to avoid
            # denominators that later get exceeded (progress > 100%). We treat
            # totals as a consolidation artifact of entering exporting/done.
            totals = meta.get("totals") or {}
            in_import_stage = data.get("current_stage") in {
                "extracting",
                "users",
                "channels",
                "messages",
                "emojis",
                "reactions",
                "attachments",
            }
            needs_totals = not totals or all(
                (totals.get(k, 0) == 0)
                for k in (
                    "users",
                    "channels",
                    "messages",
                    "reactions",
                    "attachments",
                )
            )
            if (not in_import_stage) and needs_totals and row.id is not None:
                q = await session.execute(
                    select(Entity.entity_type, func.count())
                    .where(Entity.job_id == row.id)
                    .group_by(Entity.entity_type)
                )
                derived = {et: cnt for et, cnt in q.all()}
                frozen_totals = {
                    label: int(derived.get(et_key, 0))
                    for label, et_key in ENTITY_LABEL_TO_TYPE.items()
                    if label != "emojis"
                }
                emoji_source = (
                    totals.get("emojis", 0)
                    if isinstance(totals, dict)
                    else derived.get(ENTITY_LABEL_TO_TYPE["emojis"], 0)
                )
                frozen_totals["emojis"] = int(emoji_source or 0)
                meta["totals"] = frozen_totals
                meta["totals_frozen"] = True
                data["meta"] = meta
            # Derive processed (ingested) vs exported (non-pending) counters.
            # processed_* remain monotonic counts of entities we attempted/created during import (ingestion scope)
            # exported_* reflect how many have transitioned out of pending (success/failed/skipped) suitable for export progress UI.
            if row.id is not None:
                q2 = await session.execute(
                    select(Entity.entity_type, func.count())
                    .where(
                        (Entity.job_id == row.id)
                        & (Entity.status != MappingStatus.pending)
                    )
                    .group_by(Entity.entity_type)
                )
                nonpend = {et: int(cnt) for et, cnt in q2.all()}
                # reuse in_import_stage calculated above
                if in_import_stage:
                    meta["users_processed"] = max(
                        int(meta.get("users_processed") or 0),
                        nonpend.get("user", 0),
                    )
                    meta["channels_processed"] = max(
                        int(meta.get("channels_processed") or 0),
                        nonpend.get("channel", 0),
                    )
                    meta["messages_processed"] = max(
                        int(meta.get("messages_processed") or 0),
                        nonpend.get("message", 0),
                    )
                    meta["reactions_processed"] = max(
                        int(meta.get("reactions_processed") or 0),
                        nonpend.get("reaction", 0),
                    )
                    meta["attachments_processed"] = max(
                        int(meta.get("attachments_processed") or 0),
                        nonpend.get("attachment", 0),
                    )
                else:
                    # Exporting / done: avoid regressions to zero if exporter statuses
                    # have not yet flipped from pending. Use max of nonpending, existing
                    # meta counters, and totals (if present) to maintain monotonicity.
                    totals_local = meta.get("totals") or {}
                    for label, et_key in ENTITY_LABEL_TO_TYPE.items():
                        if label == "emojis":
                            continue
                        processed_key = f"{label}_processed"
                        meta[processed_key] = max(
                            int(nonpend.get(et_key, 0)),
                            int(meta.get(processed_key) or 0),
                            int(totals_local.get(label, 0) or 0),
                        )
                # Exported counters always reflect current non-pending counts (even during import)
                for label, et_key in ENTITY_LABEL_TO_TYPE.items():
                    if label == "emojis":
                        continue
                    meta[f"{label}_exported"] = int(nonpend.get(et_key, 0))
                # Attach full status breakdown per type (success/failed/skipped/pending) for richer UI.
                # Provide all four statuses even if zero to simplify client-side rendering.
                # row.id is already an int (SQLAlchemy scalar instance). Avoid casting which upsets type checker.
                br_key = row.id
                br_map = breakdown.get(br_key, {}) if br_key is not None else {}
                export_status: dict[str, dict[str, int]] = {}
                all_status_values = [
                    m.value for m in MappingStatus
                ]  # ['pending','skipped','failed','success']
                for et in (
                    "user",
                    "channel",
                    "message",
                    "reaction",
                    "attachment",
                    "custom_emoji",
                ):
                    et_counts_raw = br_map.get(et, {})
                    et_counts: dict[str, int] = {
                        s: int(et_counts_raw.get(s, 0)) for s in all_status_values
                    }
                    export_status[et] = et_counts
                meta["export_status"] = export_status
                data["meta"] = meta
            jobs_out.append(data)
    return {"jobs": jobs_out}


@router.get("/jobs/{job_id}/audit")
async def audit_job(job_id: int):
    """Comprehensive reconciliation for a single job.

    Provides discovered vs created vs existing counts, live status breakdown,
    failed summaries, unmapped channel folders, and basic integrity flags.
    """
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        meta = job.meta or {}
        # Live created counts per type (entity rows for this job)
        q_created = await session.execute(
            select(Entity.entity_type, func.count())
            .where(Entity.job_id == job_id)
            .group_by(Entity.entity_type)
        )
        created_map = {et: int(cnt) for et, cnt in q_created.all()}
        # Live status breakdown for this job
        q_status = await session.execute(
            select(Entity.entity_type, Entity.status, func.count())
            .where(Entity.job_id == job_id)
            .group_by(Entity.entity_type, Entity.status)
        )
        status_map: dict[str, dict[str, int]] = {}
        for et, st, cnt in q_status.all():
            sm = status_map.setdefault(str(et), {})
            sm[str(getattr(st, "value", st))] = int(cnt)

        def _meta_int(key: str) -> int:
            v = meta.get(key)
            try:
                return int(v) if v is not None else 0
            except Exception:
                return 0

        discovered = {
            "users": _meta_int("users_discovered") or _meta_int("users_processed"),
            "channels": _meta_int("channels_discovered")
            or _meta_int("channels_processed"),
            "messages": _meta_int("messages_processed"),
            "reactions": _meta_int("reactions_processed"),
            "attachments": _meta_int("attachments_processed"),
            "emojis": _meta_int("emojis_processed"),
        }
        created = {
            label: created_map.get(et_key, 0)
            for label, et_key in ENTITY_LABEL_TO_TYPE.items()
        }
        existing = {
            k: max(discovered.get(k, 0) - created.get(k, 0), 0) for k in discovered
        }
        # Export status (already produced in list_jobs meta); recompute for isolation
        all_status_values = [m.value for m in MappingStatus]
        export_status: dict[str, dict[str, int]] = {}
        for et in set(ENTITY_LABEL_TO_TYPE.values()):
            raw = status_map.get(et, {})
            export_status[et] = {s: int(raw.get(s, 0)) for s in all_status_values}

        failed_summary = meta.get("failed_summary") or {}
        channel_mapping = meta.get("channel_mapping") or {}

        # Integrity flags
        integrity = {}
        integrity["has_failures"] = any(
            export_status.get(et, {}).get("failed", 0) > 0 for et in export_status
        )
        integrity["unmapped_folders"] = bool(
            channel_mapping.get("unmapped", {}).get("total", 0) > 0
        )
        # Check mismatch: created should not exceed discovered
        integrity["created_exceeds_discovered"] = any(
            created.get(k, 0) > discovered.get(k, 0) for k in discovered
        )
        # Sum of statuses per type should equal created (or <= created if some still pending globally)
        mismatch_types = []
        for label, c_val in created.items():
            et_key = ENTITY_LABEL_TO_TYPE[label]
            status_total = sum(export_status.get(et_key, {}).values())
            if status_total != c_val:
                mismatch_types.append(et_key)
        integrity["status_sum_mismatch_types"] = mismatch_types
        integrity["status_sum_mismatch"] = bool(mismatch_types)

        notes = []
        if integrity["has_failures"]:
            notes.append("One or more entity types have failures")
        if integrity["unmapped_folders"]:
            notes.append("Some archive folders have no channel mapping")
        if integrity["created_exceeds_discovered"]:
            notes.append("Created count exceeds discovered — unexpected")
        if integrity["status_sum_mismatch"]:
            notes.append("Status counts do not sum to created for some types")

        payload = {
            "job_id": job_id,
            "status": getattr(job.status, "value", job.status),
            "current_stage": job.current_stage,
            "in_progress": job.current_stage not in {"done"},
            "discovered": discovered,
            "created": created,
            "existing": existing,
            "export_status": export_status,
            "failed_summary": failed_summary,
            "channel_mapping": channel_mapping,
            "integrity": integrity,
            "notes": notes,
            "timestamps": {
                "created_at": getattr(job.created_at, "isoformat", lambda: None)(),
                "updated_at": getattr(job.updated_at, "isoformat", lambda: None)(),
            },
        }
    return payload


@router.post("/jobs/{job_id}/restart")
async def restart_job(job_id: int, background_tasks: BackgroundTasks):
    """Restart a job by resetting failed/skipped entities to pending and re-triggering export.

    This endpoint allows retrying failed/skipped mappings after a job has completed.
    It resets the job to 'exporting' stage and triggers the export orchestrator.
    """
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        # Only allow restart for completed jobs
        if job.status not in {JobStatus.success, JobStatus.failed}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot restart job in status '{job.status}'. Only completed (success/failed) jobs can be restarted.",
            )

        # Check if there are any failed or skipped entities worth retrying
        q_failed_skipped = await session.execute(
            select(func.count())
            .select_from(Entity)
            .where(
                (Entity.job_id == job_id)
                & (Entity.status.in_([MappingStatus.failed, MappingStatus.skipped]))
            )
        )
        count = int(q_failed_skipped.scalar_one())
        if count == 0:
            raise HTTPException(
                status_code=400,
                detail="No failed or skipped entities to retry. Job has no retryable items.",
            )

        # Reset failed/skipped entities to pending
        await session.execute(
            update(Entity)
            .where(
                (Entity.job_id == job_id)
                & (Entity.status.in_([MappingStatus.failed, MappingStatus.skipped]))
            )
            .values(status=MappingStatus.pending, error_message=None)
        )

        # Reset job to running/exporting state
        await session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(
                status=JobStatus.running, current_stage="exporting", error_message=None
            )
        )

        await session.commit()

        backend_logger.info(
            f"Restarting job_id={job_id}: reset {count} failed/skipped entities to pending"
        )

    # Trigger export orchestrator in background for this job
    from app.services.export.orchestrator import orchestrate_mm_export

    background_tasks.add_task(orchestrate_mm_export, job_id=job_id)

    return {
        "status": "restart_initiated",
        "message": f"Job {job_id} restarted: {count} entities reset to pending and export triggered",
        "reset_count": count,
    }

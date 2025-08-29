from fastapi import APIRouter
from sqlalchemy import select, func
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
import os
import glob
import zipfile

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

        # Derive per-job totals for job-scoped types if meta.totals absent/empty
        jobs_out = []
        for row in rows:
            data = _serialize_job(row)
            meta = data.get("meta") or {}
            # Backfill file-based import progress if available and missing
            if (
                (not meta.get("json_files_total") or not isinstance(meta.get("json_files_total"), int))
                and bool(meta.get("extract_dir"))
                and data.get("current_stage") in {"extracting","users","channels","messages","emojis","reactions","attachments"}
            ):
                base_dir = str(meta.get("extract_dir") or "")
                try:
                    total = 0
                    if not base_dir or not os.path.isdir(base_dir):
                        raise RuntimeError("extract_dir not available")
                    # top-level files
                    for fname in ("users.json","channels.json","groups.json","dms.json","mpims.json"):
                        if os.path.exists(os.path.join(base_dir, fname)):
                            total += 1
                    # daily message JSONs per channel
                    for entry in os.listdir(base_dir):
                        p = os.path.join(base_dir, entry)
                        if os.path.isdir(p):
                            total += len(glob.glob(os.path.join(p, "*.json")))
                    meta["json_files_total"] = int(total)
                    # processed remains whatever orchestrator has set; don't derive here to avoid expensive/fragile scans
                    data["meta"] = meta
                except Exception:
                    pass
            # If still no total, try to derive it directly from the uploaded zip archive
            if (
                (not meta.get("json_files_total") or not isinstance(meta.get("json_files_total"), int))
                and bool(meta.get("zip_path"))
                and data.get("current_stage") in {"extracting","users","channels","messages","emojis","reactions","attachments"}
            ):
                zpath = str(meta.get("zip_path") or "")
                try:
                    if zpath and os.path.exists(zpath):
                        total = 0
                        top_present = {"users.json": False, "channels.json": False, "groups.json": False, "dms.json": False, "mpims.json": False}
                        with zipfile.ZipFile(zpath, 'r') as zf:
                            names = zf.namelist()
                            # Normalize separators and filter directories
                            top_allowed = {"users.json", "channels.json", "groups.json", "dms.json", "mpims.json"}
                            for name in names:
                                if name.endswith('/'):
                                    continue
                                # Remove any leading prefix folders (Slack zips often wrap everything in one folder)
                                parts = [p for p in name.split('/') if p]
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
                                    if fname.lower().endswith('.json'):
                                        total += 1
                        if total > 0:
                            meta["json_files_total"] = int(total)
                            # Do not synthesize processed here; let the orchestrator update the DB value in real time
                            data["meta"] = meta
                except Exception:
                    # Non-fatal: silently ignore if zip cannot be read
                    pass
            totals = meta.get("totals") or {}
            needs_totals = not totals or all((totals.get(k, 0) == 0) for k in ("messages", "reactions", "attachments"))
            if needs_totals and row.id is not None:
                q = await session.execute(
                    select(Entity.entity_type, func.count())
                    .where(Entity.job_id == row.id)
                    .group_by(Entity.entity_type)
                )
                derived = {et: cnt for et, cnt in q.all()}
                totals = {
                    "messages": int(derived.get("message", 0)),
                    "reactions": int(derived.get("reaction", 0)),
                    "attachments": int(derived.get("attachment", 0)),
                    # emojis left as-is (global)
                    **({"emojis": totals.get("emojis", 0)} if isinstance(totals, dict) else {}),
                }
                meta["totals"] = totals
                data["meta"] = meta
            # Derive processed counters:
            #  - During import stages: keep max(meta vs derived) so UI doesn't regress.
            #  - During exporting/done: use derived (non-pending) only, so progress resets to 0 at export start.
            if row.id is not None:
                q2 = await session.execute(
                    select(Entity.entity_type, func.count())
                    .where((Entity.job_id == row.id) & (Entity.status != MappingStatus.pending))
                    .group_by(Entity.entity_type)
                )
                nonpend = {et: int(cnt) for et, cnt in q2.all()}
                in_import_stage = data.get("current_stage") in {"extracting","users","channels","messages","emojis","reactions","attachments"}
                if in_import_stage:
                    meta["messages_processed"] = max(int(meta.get("messages_processed") or 0), nonpend.get("message", 0))
                    meta["reactions_processed"] = max(int(meta.get("reactions_processed") or 0), nonpend.get("reaction", 0))
                    meta["attachments_processed"] = max(int(meta.get("attachments_processed") or 0), nonpend.get("attachment", 0))
                else:
                    # Export/done: reflect actual exported items only
                    meta["messages_processed"] = int(nonpend.get("message", 0))
                    meta["reactions_processed"] = int(nonpend.get("reaction", 0))
                    meta["attachments_processed"] = int(nonpend.get("attachment", 0))
                data["meta"] = meta
            jobs_out.append(data)
    return {"jobs": jobs_out}

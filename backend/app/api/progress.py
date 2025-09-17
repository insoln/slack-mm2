import asyncio
import json
import time
from collections import deque, defaultdict
from typing import Deque, Dict, Any
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.api.stats import get_mapping_stats
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
from sqlalchemy import select, func

router = APIRouter()


@router.get("/progress/stream")
async def progress_stream(interval: float = 2.0, window_sec: float = 8.0):
    """Server-Sent Events stream of global stats plus per-job speed & ETA.
    Per-job speed/eta computed over a rolling window (default 8s) for job-scoped types
    (message, reaction, attachment). We treat processed = success+skipped+failed.
    ETA = pending / speed if speed>0 else null.
    """

    # Rolling window per job: job_id -> deque[(ts, {type: {pending:int, processed:int}})]
    job_windows: Dict[int, Deque[tuple[float, Dict[str, Dict[str, int]]]]] = (
        defaultdict(lambda: deque(maxlen=50))
    )

    job_scoped_types = ("message", "reaction", "attachment")

    async def _snapshot_jobs():
        """Collect per-job counts for job-scoped types grouped by status."""
        async with SessionLocal() as session:
            from app.models.job_status_enum import JobStatus as _JobStatus

            # Active jobs: queued or running
            jobs_rows = await session.execute(
                select(ImportJob).where(
                    ImportJob.status.in_(
                        [
                            _JobStatus.queued,
                            _JobStatus.running,
                        ]
                    )
                )
            )
            jobs = list(jobs_rows.scalars().all())
            if not jobs:
                return []
            job_ids = [int(getattr(j, "id")) for j in jobs]
            # Query counts: job_id, entity_type, status, count
            rows = await session.execute(
                select(
                    Entity.job_id,
                    Entity.entity_type,
                    Entity.status,
                    func.count().label("cnt"),
                )
                .where(
                    (Entity.job_id.in_(job_ids))
                    & (Entity.entity_type.in_(job_scoped_types))
                )
                .group_by(Entity.job_id, Entity.entity_type, Entity.status)
            )
            out: Dict[int, Dict[str, Dict[str, int]]] = {
                int(getattr(j, "id")): {} for j in jobs
            }
            for job_id, etype, status, cnt in rows.all():
                jdict = out.setdefault(int(job_id), {})
                sdict = jdict.setdefault(
                    etype,
                    {
                        st: 0
                        for st in (
                            MappingStatus.pending,
                            MappingStatus.skipped,
                            MappingStatus.failed,
                            MappingStatus.success,
                        )
                    },
                )
                sdict[status] = cnt
            # Ensure missing types appear with zeroes
            for jid in out:
                for t in job_scoped_types:
                    out[jid].setdefault(
                        t,
                        {
                            st: 0
                            for st in (
                                MappingStatus.pending,
                                MappingStatus.skipped,
                                MappingStatus.failed,
                                MappingStatus.success,
                            )
                        },
                    )
            # Attach job metadata
            job_meta = {int(getattr(j, "id")): j for j in jobs}
            return [
                {
                    "id": int(jid),
                    "status": getattr(
                        job_meta[jid].status, "value", job_meta[jid].status
                    ),
                    "current_stage": job_meta[jid].current_stage,
                    "counts": counts,
                }
                for jid, counts in out.items()
            ]

    def _compute_speed_eta(job_entry: Dict[str, Any]):
        jid = job_entry["id"]
        counts = job_entry["counts"]  # type -> {MappingStatus: count}
        # Normalize to simple ints
        simplified: Dict[str, Dict[str, int]] = {}
        for etype, statmap in counts.items():
            simplified[etype] = {
                "pending": statmap[MappingStatus.pending],
                "processed": (
                    statmap[MappingStatus.success]
                    + statmap[MappingStatus.skipped]
                    + statmap[MappingStatus.failed]
                ),
            }
        now = time.time()
        job_windows[jid].append((now, simplified))
        # Prune older than window_sec
        dq = job_windows[jid]
        while dq and (now - dq[0][0]) > window_sec:
            dq.popleft()
        if len(dq) < 2:
            # Not enough data yet
            per_type = {
                t: {
                    "eps": None,
                    "eta_sec": None,
                    "pending": simplified[t]["pending"],
                    "processed": simplified[t]["processed"],
                }
                for t in simplified
            }
            aggregate = {
                "eps": None,
                "eta_sec": None,
                "pending": sum(v["pending"] for v in simplified.values()),
                "processed": sum(v["processed"] for v in simplified.values()),
            }
            return per_type, aggregate
        oldest_ts, oldest_map = dq[0]
        elapsed = max(0.001, now - oldest_ts)
        per_type = {}
        total_pending = 0
        total_processed = 0
        total_speed_num = 0.0
        for t, cur in simplified.items():
            old = oldest_map.get(t, {"processed": 0, "pending": cur["pending"]})
            delta_processed = cur["processed"] - old.get("processed", 0)
            speed = delta_processed / elapsed if delta_processed > 0 else 0.0
            pending = cur["pending"]
            eta = (pending / speed) if speed > 0 else None
            per_type[t] = {
                "eps": speed if speed > 0 else 0.0,
                "eta_sec": eta,
                "pending": pending,
                "processed": cur["processed"],
            }
            total_pending += pending
            total_processed += cur["processed"]
            total_speed_num += speed
        agg_speed = total_speed_num  # sum of per-type speeds (not weighted); alternative: recompute via aggregated delta
        agg_eta = (total_pending / agg_speed) if agg_speed > 0 else None
        aggregate = {
            "eps": agg_speed if agg_speed > 0 else 0.0,
            "eta_sec": agg_eta,
            "pending": total_pending,
            "processed": total_processed,
        }
        return per_type, aggregate

    async def event_generator():
        # Initial lines to help proxies start streaming immediately
        yield ": init\n\n"
        yield "retry: 2000\n\n"
        while True:
            try:
                stats = await get_mapping_stats()
                # Collect per-job counts and compute speed/eta
                jobs_snapshot = await _snapshot_jobs()
                enriched_jobs = []
                for jb in jobs_snapshot:
                    per_type, aggregate = _compute_speed_eta(jb)
                    enriched_jobs.append(
                        {
                            "id": jb["id"],
                            "status": jb["status"],
                            "current_stage": jb["current_stage"],
                            "speed": {
                                "window_sec": window_sec,
                                "per_type": per_type,
                                "aggregate": aggregate,
                            },
                        }
                    )
                payload = json.dumps(
                    {**stats, "jobs": enriched_jobs}, ensure_ascii=False
                )
                yield f"event: stats\ndata: {payload}\n\n"
            except Exception as e:
                # Emit an error event but keep the stream alive
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(max(0.25, float(interval)))

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=headers
    )

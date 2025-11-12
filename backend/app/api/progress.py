import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.api.stats import get_mapping_stats
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from sqlalchemy import select

router = APIRouter()


@router.get("/progress/stream")
async def progress_stream(interval: float = 2.0, jobs_limit: int = 25):
    async def event_generator():
        # Initial lines to help proxies start streaming immediately
        yield ": init\n\n"
        yield "retry: 2000\n\n"
        while True:
            try:
                stats = await get_mapping_stats()
                # Fetch multiple recent jobs (reuse minimal subset of list_jobs logic inline to avoid import cycle)
                async with SessionLocal() as session:
                    res = await session.execute(
                        select(ImportJob)
                        .order_by(ImportJob.id.desc())
                        .limit(jobs_limit)
                    )
                    rows = res.scalars().all()
                jobs_payload = []
                latest_job = None
                for idx, row in enumerate(rows):
                    job_obj = {
                        "id": row.id,
                        "status": getattr(row.status, "value", row.status),
                        "current_stage": row.current_stage,
                        # meta no longer mirrors current_stage key
                        "meta": row.meta or {},
                        "error_message": row.error_message,
                    }
                    if idx == 0:
                        latest_job = job_obj
                    jobs_payload.append(job_obj)
                # Backward compatible field 'job' plus new 'jobs' and 'latest_job'
                payload_dict = {
                    **stats,
                    "job": latest_job,
                    "latest_job": latest_job,
                    "jobs": jobs_payload,
                }
                payload = json.dumps(payload_dict, ensure_ascii=False)
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

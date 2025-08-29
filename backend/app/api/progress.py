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
async def progress_stream(interval: float = 2.0):
    async def event_generator():
        # Initial lines to help proxies start streaming immediately
        yield ": init\n\n"
        yield "retry: 2000\n\n"
        while True:
            try:
                stats = await get_mapping_stats()
                # Add latest job info
                job_info = None
                async with SessionLocal() as session:
                    res = await session.execute(
                        select(ImportJob).order_by(ImportJob.id.desc()).limit(1)
                    )
                    row = res.scalar_one_or_none()
                if row:
                    job_info = {
                        "id": row.id,
                        "status": getattr(row.status, "value", row.status),
                        "current_stage": row.current_stage,
                        "meta": row.meta or {},
                    }
                payload = json.dumps({**stats, "job": job_info}, ensure_ascii=False)
                yield f"event: stats\ndata: {payload}\n\n"
            except Exception as e:
                # Emit an error event but keep the stream alive
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(max(0.25, float(interval)))
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

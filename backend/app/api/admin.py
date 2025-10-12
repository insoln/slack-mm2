from fastapi import APIRouter, BackgroundTasks
from app.logging_config import backend_logger
from app.services.export.orchestrator import orchestrate_mm_export

router = APIRouter()


@router.post("/admin/export/retrigger")
async def retrigger_export(
    background_tasks: BackgroundTasks, job_id: int | None = None
):
    """Force (re-)run of export orchestrator.

    If job_id provided, orchestrator will anchor to that job (processing pending
    entities for earlier jobs first, then the anchor). Without job_id it will
    scan all running jobs in exporting stage.
    """
    backend_logger.info(
        f"Admin export retrigger requested (job_id={job_id if job_id is not None else 'ALL'})"
    )
    background_tasks.add_task(orchestrate_mm_export, job_id=job_id)
    return {"status": "scheduled", "job_id": job_id}

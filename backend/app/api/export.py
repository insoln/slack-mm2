from fastapi import APIRouter, BackgroundTasks, Query
from app.services.export.orchestrator import orchestrate_mm_export
from app.logging_config import backend_logger

router = APIRouter()


@router.post("/export")
async def start_export(
    background_tasks: BackgroundTasks,
    job_id: int | None = Query(
        default=None,
        description="Anchor job id: ограничить FIFO продвижение экспортом только для джоб, созданных не позже указанной",
    ),
):
    if job_id is not None:
        backend_logger.info(f"Запуск экспорта с якорем job_id={job_id}")
        background_tasks.add_task(orchestrate_mm_export, job_id=job_id)
        return {
            "status": "export_started",
            "message": f"Экспорт (anchor job_id={job_id}) запущен в фоне",
        }
    backend_logger.info("Запуск экспорта всех доступных задач (без anchor)")
    background_tasks.add_task(orchestrate_mm_export)
    return {
        "status": "export_started",
        "message": "Экспорт (global) запущен в фоновом режиме",
    }

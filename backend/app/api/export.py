from fastapi import APIRouter, BackgroundTasks
from app.services.export.orchestrator import orchestrate_mm_export
from app.logging_config import backend_logger

router = APIRouter()


@router.post("/export")
async def start_export(background_tasks: BackgroundTasks):
    backend_logger.info("Запуск экспорта в Mattermost")
    background_tasks.add_task(orchestrate_mm_export)
    return {"status": "export_started", "message": "Экспорт запущен в фоновом режиме"}

from fastapi import APIRouter, UploadFile, File, BackgroundTasks
from app.services.backup.file_storage import save_temp_file
from app.services.backup.orchestrator import orchestrate_slack_import
import os
from app.logging_config import backend_logger
import tempfile

router = APIRouter()


@router.post("/upload")
async def upload_backup(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    backend_logger.info(f"UPLOAD: {file.filename}, content_type={file.content_type}")
    try:
        backend_logger.debug("Вызов save_temp_file...")
        tmp_path = await save_temp_file(file)
        size = os.path.getsize(tmp_path)
        # Если это zip, запускаем оркестратор импорта в фоне
        if tmp_path.endswith(".zip"):
            backend_logger.debug(f"Фоновый импорт Slack-экспорта из архива: {tmp_path}")
            background_tasks.add_task(orchestrate_slack_import, tmp_path)
            return {"filename": file.filename, "size": size, "status": "processing"}
        else:
            backend_logger.info(
                f"UPLOAD: файл {tmp_path} не является zip-архивом, удаляю файл"
            )
            os.remove(tmp_path)
            backend_logger.error(
                f"Загружен неподдерживаемый тип файла: {file.filename}, content_type={file.content_type}"
            )
            return {"error": "Можно загружать только zip-архивы экспорта Slack"}
    except Exception as e:
        backend_logger.error(f"Ошибка при загрузке файла: {e}")
        return {"error": str(e)}

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, HTTPException
from app.services.backup.file_storage import save_temp_file
from app.services.backup.orchestrator import orchestrate_slack_import
import os
from app.logging_config import backend_logger
import tempfile
from starlette.responses import JSONResponse

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB soft cap (adjust if needed)

router = APIRouter()


@router.post("/upload")
async def upload_backup(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    backend_logger.info(f"UPLOAD: {file.filename}, content_type={file.content_type}")
    try:
        # Fast preflight: reject clearly non-zip by extension early (still store only if zip)
        name_lower = (file.filename or "").lower()
        if not name_lower.endswith('.zip'):
            return JSONResponse(status_code=400, content={"error": "Можно загружать только zip-архивы экспорта Slack"})

        backend_logger.debug("Вызов save_temp_file...")
        tmp_path = await save_temp_file(file)
        size = os.path.getsize(tmp_path)

        if size > MAX_UPLOAD_BYTES:
            backend_logger.error(f"UPLOAD: size {size} > MAX_UPLOAD_BYTES {MAX_UPLOAD_BYTES}, deleting {tmp_path}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return JSONResponse(status_code=413, content={"error": "Файл слишком большой", "max_bytes": MAX_UPLOAD_BYTES, "size": size})

        if not tmp_path.endswith('.zip'):
            # Safety double check (suffix preserved from original filename)
            backend_logger.info(f"UPLOAD: {tmp_path} не zip, удаляю")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return JSONResponse(status_code=400, content={"error": "Можно загружать только zip-архивы экспорта Slack"})

        backend_logger.debug(f"Фоновый импорт Slack-экспорта: {tmp_path} ({size} bytes)")
        background_tasks.add_task(orchestrate_slack_import, tmp_path)
        return {"filename": file.filename, "size": size, "status": "processing"}
    except Exception as e:
        backend_logger.exception("Ошибка при загрузке файла")
        return JSONResponse(status_code=500, content={"error": "Upload failed", "detail": str(e)})

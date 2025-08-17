# file_storage.py
# Временное и постоянное хранение файлов backup

import aiofiles
import tempfile
import os
from fastapi import UploadFile
from typing import Optional
from app.logging_config import backend_logger


async def save_temp_file(upload_file: UploadFile) -> str:
    filename = upload_file.filename or "upload.tmp"
    suffix = os.path.splitext(str(filename))[-1] or ".tmp"
    backend_logger.debug(f"Начинаю сохранение файла: {filename}, suffix={suffix}")
    try:
        async with aiofiles.tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="slack-upload-", dir="/tmp"
        ) as tmp:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                await tmp.write(chunk)
            tmp_path = str(tmp.name)
        backend_logger.debug(f"UPLOAD: файл успешно сохранён: {tmp_path}")
        return tmp_path
    except Exception as e:
        backend_logger.error(f"Ошибка при сохранении файла {filename}: {e}")
        raise

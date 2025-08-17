# zip_utils.py
# Вспомогательные функции для работы с zip-архивами Slack backup

import asyncio
import subprocess
import os
from app.logging_config import backend_logger


async def extract_zip(path_to_zip, extract_to):
    loop = asyncio.get_running_loop()

    def _extract():
        try:
            backend_logger.debug(
                f"Начинаю распаковку архива через unzip: {path_to_zip} -> {extract_to} (без указания кодировки)"
            )
            os.makedirs(extract_to, exist_ok=True)
            result = subprocess.run([
                "unzip", path_to_zip, "-d", extract_to
            ], capture_output=True, text=True)
            if result.returncode != 0:
                backend_logger.error(f"Ошибка при распаковке {path_to_zip}: {result.stderr}")
                raise RuntimeError(f"unzip failed: {result.stderr}")
            backend_logger.debug(f"PARSE: архив успешно распакован: {extract_to}")
        except Exception as e:
            backend_logger.error(f"Ошибка при распаковке {path_to_zip}: {e}")
            raise

    await loop.run_in_executor(None, _extract)

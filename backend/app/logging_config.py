import logging
import sys
import os

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(filename)s:%(lineno)d %(funcName)s: %(message)s"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
UVICORN_LOG_LEVEL = os.getenv("UVICORN_LOG_LEVEL", "INFO").upper()
HTTPX_LOG_LEVEL = os.getenv("HTTPX_LOG_LEVEL", "WARNING").upper()

# Преобразуем строки в уровни логирования
LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL, logging.INFO)
UVICORN_LOG_LEVEL_NUM = getattr(logging, UVICORN_LOG_LEVEL, logging.INFO)
HTTPX_LOG_LEVEL_NUM = getattr(logging, HTTPX_LOG_LEVEL, logging.WARNING)

# Настройка root-логгера
logging.basicConfig(level=LOG_LEVEL_NUM, format=LOG_FORMAT)

# Явно настраиваем логгер backend
backend_logger = logging.getLogger("backend")
backend_logger.setLevel(LOG_LEVEL_NUM)

# Добавляем собственный stdout-хендлер, чтобы uvicorn не глушил наши логи
backend_stream_handler = logging.StreamHandler(sys.stdout)
backend_stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
backend_logger.handlers = [backend_stream_handler]
backend_logger.propagate = False

# Кастомный хендлер для uvicorn
uvicorn_handler = logging.StreamHandler(sys.stdout)
uvicorn_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Настройка логгеров uvicorn
for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    uvicorn_logger = logging.getLogger(uvicorn_logger_name)
    uvicorn_logger.handlers = []  # Убираем дефолтные хендлеры Uvicorn
    uvicorn_logger.addHandler(uvicorn_handler)
    uvicorn_logger.propagate = False
    uvicorn_logger.setLevel(UVICORN_LOG_LEVEL_NUM)

# Настройка логгера httpx
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(HTTPX_LOG_LEVEL_NUM)

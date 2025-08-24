import subprocess
from pathlib import Path
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import UploadFile, File
import shutil
import tempfile
from app.logging_config import backend_logger
from app.api.upload import router as upload_router
from app.api.export import router as export_router
from app.api.plugin import router as plugin_router
from app.api.stats import router as stats_router
from app.api import plugin as plugin_api

BACKEND_HOST = os.getenv("BACKEND_HOST", "localhost")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        subprocess.run(["alembic", "-c", "/alembic.ini", "upgrade", "head"], check=True)
    backend_logger.info(f"Backend available at: http://{BACKEND_HOST}:{BACKEND_PORT}")
    # Auto-ensure Mattermost importer plugin on startup (best-effort)
    try:
        status = await plugin_api._compute_status()
        if (not status.get("installed")) or status.get("needs_update") or (not status.get("enabled")):
            backend_logger.info("Ensuring Mattermost importer plugin at startup…")
            # Try deploy if missing/outdated
            if (not status.get("installed")) or status.get("needs_update"):
                bundle_path = status.get("bundle_path")
                if not status.get("bundle_exists") or not bundle_path:
                    # This will attempt a build if bundle missing
                    await plugin_api.plugin_deploy()
                else:
                    ok, err = await plugin_api._upload_bundle(Path(bundle_path))
                    if not ok:
                        backend_logger.error(f"Plugin upload failed: {err}")
            # Enable if needed
            final = await plugin_api._compute_status()
            if not final.get("enabled"):
                await plugin_api.plugin_enable()
    except Exception as e:
        backend_logger.error(f"Auto-ensure plugin failed: {e}")
    yield


app = FastAPI(title="Slack-MM2 Sync Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Для разработки можно *, для продакшена лучше явно
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router)
app.include_router(export_router)
app.include_router(plugin_router)
app.include_router(stats_router)


@app.get("/healthcheck")
async def healthcheck():
    backend_logger.info("HEALTHCHECK")
    return JSONResponse(content={"status": "ok"})


# Временное access-логирование для /upload (dev-трассировка)
@app.middleware("http")
async def log_upload_requests(request: Request, call_next):
    if request.url.path == "/upload":
        backend_logger.info(f"HTTP {request.method} {request.url.path}")
        response = await call_next(request)
        backend_logger.info(
            f"HTTP {request.method} {request.url.path} -> {response.status_code}"
        )
        return response
    return await call_next(request)

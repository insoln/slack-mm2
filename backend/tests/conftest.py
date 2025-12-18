import os
import sys
import threading
from pathlib import Path
import pytest
import pytest_asyncio

# Ensure backend root is on sys.path and test-mode flag is set BEFORE importing app
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTEST_RUN", "1")

from fastapi.testclient import TestClient
from app.main import app
from app.models.base import engine


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_async_engine():
    yield
    await engine.dispose()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    remaining = [t for t in threading.enumerate() if t is not threading.main_thread()]
    if remaining:
        names = ", ".join(f"{t.name}(daemon={t.daemon})" for t in remaining)
        print(f"[pytest-thread-dump] Alive threads: {names}")

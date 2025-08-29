import os
import sys
from pathlib import Path
import pytest

# Ensure backend root is on sys.path and test-mode flag is set BEFORE importing app
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTEST_RUN", "1")

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)

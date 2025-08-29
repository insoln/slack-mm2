import os
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    # Signal app to skip migrations/plugin/autoresume during tests
    os.environ.setdefault("PYTEST_RUN", "1")
    return TestClient(app)

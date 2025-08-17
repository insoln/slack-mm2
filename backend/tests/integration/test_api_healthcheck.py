import pytest
from fastapi.testclient import TestClient
from app.main import app
import os
import requests

client = TestClient(app)


def test_healthcheck():
    response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.skipif(
    not os.getenv("MATTERMOST_API_TOKEN") or not os.getenv("MATTERMOST_API_URL"),
    reason="MATTERMOST_API_TOKEN and MATTERMOST_API_URL must be set in environment",
)
def test_mattermost_api_token():
    token = os.getenv("MATTERMOST_API_TOKEN")
    url = os.getenv("MATTERMOST_API_URL")
    assert url is not None, "MATTERMOST_API_URL must be set"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "admin"
    assert data["email"] == "is@careerum.com"

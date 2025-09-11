import os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_plugin_status_shape_no_mm_env(monkeypatch):
    # Ensure MM env vars absent to trigger error path
    monkeypatch.delenv("MM_URL", raising=False)
    monkeypatch.delenv("MM_TOKEN", raising=False)
    r = client.get("/plugin/status")
    assert r.status_code == 200
    data = r.json()
    # Basic expected keys
    for key in [
        "plugin_id","expected_version","installed","enabled","installed_version",
        "needs_update","bundle_exists","bundle_path","bundle_sha256","bundle_mtime"
    ]:
        assert key in data
    assert data["installed"] is False
    assert data["enabled"] is False
    # When MM env is missing we expect error field
    assert data.get("error") is not None


def test_plugin_status_with_fake_mm_env(monkeypatch):
    # Provide fake env so compute_status tries to call remote; we monkeypatch network layer if needed.
    monkeypatch.setenv("MM_URL", "http://localhost:65500")  # unlikely port to avoid real server
    monkeypatch.setenv("MM_TOKEN", "dummy")
    r = client.get("/plugin/status")
    # Even if Mattermost unreachable, endpoint should not crash
    assert r.status_code == 200
    data = r.json()
    assert "bundle_exists" in data
    # Hash fields may be None if no bundle
    assert "bundle_sha256" in data
    assert "bundle_mtime" in data

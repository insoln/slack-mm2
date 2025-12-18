import json
from pathlib import Path


def test_bundle_info_404_when_absent(monkeypatch, client):
    monkeypatch.setenv("MM_URL", "http://localhost:65500")
    monkeypatch.setenv("MM_TOKEN", "dummy")
    # Force plugin repo path to temp area without bundle
    tmp = Path("/tmp/test_plugin_repo_no_bundle")
    (tmp / "dist").mkdir(parents=True, exist_ok=True)
    with open(tmp / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"id": "mm-importer", "version": "0.0.1"}, f)
    monkeypatch.setenv("PLUGIN_REPO_PATH", str(tmp))
    r = client.get("/plugin/bundle/info")
    assert r.status_code == 404
    assert r.json()["error"] == "Bundle not found"


def test_bundle_info_present(monkeypatch, client):
    monkeypatch.setenv("MM_URL", "http://localhost:65500")
    monkeypatch.setenv("MM_TOKEN", "dummy")
    tmp = Path("/tmp/test_plugin_repo_with_bundle")
    dist = tmp / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    with open(tmp / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"id": "mm-importer", "version": "0.1.0"}, f)
    # Create fake bundle file
    bundle = dist / "mm-importer-0.1.0.tar.gz"
    with open(bundle, "wb") as f:
        f.write(b"fake bundle content")
    monkeypatch.setenv("PLUGIN_REPO_PATH", str(tmp))
    r = client.get("/plugin/bundle/info")
    assert r.status_code == 200
    data = r.json()
    assert data["bundle_path"].endswith("mm-importer-0.1.0.tar.gz")
    assert data["bundle_sha256"]
    assert data["bundle_size"] == bundle.stat().st_size
    assert isinstance(data.get("bundle_mtime"), int)

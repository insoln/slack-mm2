import os
import json
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import httpx
from app.logging_config import backend_logger
import subprocess

router = APIRouter()

MM_URL = os.environ.get("MM_URL")
MM_TOKEN = os.environ.get("MM_TOKEN")

PLUGIN_DEFAULT_ID = "mm-importer"


def get_plugin_repo_root() -> Path:
    # Allow override
    env_path = os.environ.get("PLUGIN_REPO_PATH")
    if env_path:
        return Path(env_path)
    # Try common locations both on host and inside container
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "infra" / "plugin",  # /app + infra/plugin (container layout)
        here.parents[3] / "infra" / "plugin",  # repo root / infra/plugin (host layout)
        Path("/app/infra/plugin"),
    ]
    for p in candidates:
        if (p / "plugin.json").exists():
            return p
    # Fallback to /app/infra/plugin even if not present; callers will handle errors
    return Path("/app/infra/plugin")


def read_plugin_manifest() -> dict:
    plugin_root = get_plugin_repo_root()
    manifest_path = plugin_root / "plugin.json"
    try:
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        backend_logger.error(f"Failed to read plugin manifest: {e}")
        return {"id": PLUGIN_DEFAULT_ID, "version": None}


def get_local_bundle_path(plugin_id: str, version: str | None) -> Path | None:
    if not version:
        return None
    plugin_root = get_plugin_repo_root()
    bundle = plugin_root / "dist" / f"{plugin_id}-{version}.tar.gz"
    return bundle


async def mm_get(path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MM_URL}{path}",
            headers={"Authorization": f"Bearer {MM_TOKEN}"},
            timeout=15,
        )
        return resp


async def mm_post(path: str, json_body: dict | None = None):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MM_URL}{path}",
            headers={"Authorization": f"Bearer {MM_TOKEN}"},
            json=json_body,
            timeout=60,
        )
        return resp


async def mm_delete(path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{MM_URL}{path}",
            headers={"Authorization": f"Bearer {MM_TOKEN}"},
            timeout=60,
        )
        return resp


async def _disable_plugin(plugin_id: str) -> tuple[bool, str | None]:
    resp = await mm_post(f"/api/v4/plugins/{plugin_id}/disable")
    if resp.status_code == 200:
        return True, None
    return False, resp.text


async def _uninstall_plugin(plugin_id: str) -> tuple[bool, str | None]:
    # Best-effort disable then delete
    await _disable_plugin(plugin_id)
    resp = await mm_delete(f"/api/v4/plugins/{plugin_id}")
    if resp.status_code in (200, 204):
        return True, None
    return False, resp.text


async def _wait_until_uninstalled(plugin_id: str, timeout_sec: int = 20) -> bool:
    import asyncio

    for _ in range(timeout_sec * 2):  # check every 0.5s
        st = await _compute_status()
        if not st.get("installed"):
            return True
        await asyncio.sleep(0.5)
    return False


async def _compute_status() -> dict:
    manifest = read_plugin_manifest()
    expected_id = manifest.get("id", PLUGIN_DEFAULT_ID)
    expected_version = manifest.get("version")

    installed = False
    enabled = False
    installed_version = None

    if not MM_URL or not MM_TOKEN:
        return {
            "plugin_id": expected_id,
            "expected_version": expected_version,
            "installed": False,
            "enabled": False,
            "installed_version": None,
            "needs_update": None,
            "error": "MM_URL or MM_TOKEN not set",
            "bundle_exists": False,
            "bundle_path": None,
        }

    resp = await mm_get("/api/v4/plugins")
    if resp.status_code == 200:
        data = resp.json()
        active = data.get("active", [])
        inactive = data.get("inactive", [])
        for pl in active + inactive:
            if pl.get("id") == expected_id:
                installed = True
                installed_version = pl.get("version")
                enabled = pl in active
                break
    else:
        backend_logger.error(f"Failed to fetch plugins: {resp.status_code} {resp.text}")

    needs_update = False
    if expected_version and installed_version and expected_version != installed_version:
        needs_update = True

    bundle_path = get_local_bundle_path(expected_id, expected_version)
    bundle_exists = bool(bundle_path and bundle_path.exists())

    return {
        "plugin_id": expected_id,
        "expected_version": expected_version,
        "installed": installed,
        "enabled": enabled,
        "installed_version": installed_version,
        "needs_update": needs_update,
        "bundle_exists": bundle_exists,
        "bundle_path": str(bundle_path) if bundle_path else None,
    }


async def _upload_bundle(bundle_path: Path) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            with open(bundle_path, "rb") as f:
                files = {"plugin": (bundle_path.name, f, "application/gzip")}
                resp = await client.post(
                    f"{MM_URL}/api/v4/plugins?force=true",
                    headers={"Authorization": f"Bearer {MM_TOKEN}"},
                    files=files,
                )
        if resp.status_code in (200, 201):
            return True, None
        return False, resp.text
    except Exception as e:
        return False, str(e)


async def _enable_plugin(plugin_id: str) -> tuple[bool, str | None]:
    resp = await mm_post(f"/api/v4/plugins/{plugin_id}/enable")
    if resp.status_code == 200:
        return True, None
    return False, resp.text


@router.get("/plugin/status")
async def plugin_status():
    status = await _compute_status()
    return JSONResponse(content=status)


@router.post("/plugin/deploy")
async def plugin_deploy(path: str | None = None):
    manifest = read_plugin_manifest()
    plugin_id = manifest.get("id", PLUGIN_DEFAULT_ID)
    version = manifest.get("version")
    bundle_path = Path(path) if path else get_local_bundle_path(plugin_id, version)

    if not MM_URL or not MM_TOKEN:
        return JSONResponse(
            status_code=400, content={"error": "MM_URL or MM_TOKEN not set"}
        )
    if not bundle_path or not bundle_path.exists():
        # Try to build the plugin bundle if missing
        plugin_root = get_plugin_repo_root()
        try:
            backend_logger.info(
                "Bundle not found. Attempting to build plugin via make dist…"
            )
            subprocess.run(["make", "-C", str(plugin_root), "dist"], check=True)
        except Exception as e:
            return JSONResponse(
                status_code=404,
                content={"error": f"Bundle not found and build failed: {e}"},
            )
        # Recompute path after build
        bundle_path = get_local_bundle_path(plugin_id, version)
        if not bundle_path or not bundle_path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": f"Bundle still not found after build: {bundle_path}"},
            )

    # If already installed, try disable first (allows replacement), then fallback to uninstall
    st0 = await _compute_status()
    if st0.get("installed"):
        backend_logger.info("plugin_deploy: disabling existing plugin before upload…")
        await _disable_plugin(plugin_id)
    ok, err = await _upload_bundle(bundle_path)
    if not ok:
        return JSONResponse(status_code=502, content={"error": err})
    # Refresh status after upload
    final = await _compute_status()
    return JSONResponse(
        content={
            "status": "uploaded",
            "version": version,
            "plugin_id": plugin_id,
            **final,
        }
    )


@router.post("/plugin/enable")
async def plugin_enable():
    manifest = read_plugin_manifest()
    plugin_id = manifest.get("id", PLUGIN_DEFAULT_ID)
    ok, err = await _enable_plugin(plugin_id)
    if not ok:
        return JSONResponse(status_code=502, content={"error": err})
    return JSONResponse(content={"status": "enabled", "plugin_id": plugin_id})


@router.post("/plugin/ensure")
async def plugin_ensure():
    """Ensure plugin is installed (at expected version) and enabled.

    - If not installed or needs update and bundle exists -> upload
    - Ensure enabled afterwards
    - Return final status
    """
    status = await _compute_status()
    plugin_id = status.get("plugin_id") or PLUGIN_DEFAULT_ID

    if not MM_URL or not MM_TOKEN:
        return JSONResponse(
            status_code=400, content={"error": "MM_URL or MM_TOKEN not set", **status}
        )

    # Deploy if missing or outdated
    if not status.get("installed") or status.get("needs_update"):
        bundle_path = status.get("bundle_path")
        if not status.get("bundle_exists") or not bundle_path:
            # Attempt to build
            manifest = read_plugin_manifest()
            plugin_id = manifest.get("id", PLUGIN_DEFAULT_ID)
            version = manifest.get("version")
            try:
                backend_logger.info(
                    "Ensuring plugin: bundle missing, building via make dist…"
                )
                subprocess.run(
                    ["make", "-C", str(get_plugin_repo_root()), "dist"], check=True
                )
            except Exception as e:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": f"Bundle not found and build failed: {e}",
                        **status,
                    },
                )
            bundle_path = str(get_local_bundle_path(plugin_id, version))
        # Always uninstall first when updating
        if status.get("installed"):
            backend_logger.info("Ensure: uninstalling existing plugin before upload…")
            uok, uerr = await _uninstall_plugin(plugin_id)
            if not uok:
                return JSONResponse(
                    status_code=502,
                    content={"error": f"Uninstall failed: {uerr}", **status},
                )
            if not await _wait_until_uninstalled(plugin_id):
                return JSONResponse(
                    status_code=504,
                    content={
                        "error": "Timeout waiting for plugin to uninstall",
                        **status,
                    },
                )
        # Always disable before upload to allow replacement; uninstall if still needed
        if status.get("installed"):
            backend_logger.info("Ensure: disabling existing plugin before upload…")
            await _disable_plugin(plugin_id)
        ok, err = await _upload_bundle(Path(bundle_path))
        if not ok:
            # As a last resort, uninstall and retry once
            backend_logger.info(
                "Ensure: uninstalling existing plugin as upload failed…"
            )
            uok, uerr = await _uninstall_plugin(plugin_id)
            if not uok:
                return JSONResponse(
                    status_code=502,
                    content={"error": f"Uninstall failed: {uerr}", **status},
                )
            if not await _wait_until_uninstalled(plugin_id):
                return JSONResponse(
                    status_code=504,
                    content={
                        "error": "Timeout waiting for plugin to uninstall",
                        **status,
                    },
                )
            ok2, err2 = await _upload_bundle(Path(bundle_path))
            if not ok2:
                return JSONResponse(status_code=502, content={"error": err2, **status})

    # Enable if disabled
    if not status.get("enabled"):
        ok, err = await _enable_plugin(plugin_id)
        if not ok:
            return JSONResponse(status_code=502, content={"error": err})

    final_status = await _compute_status()
    return JSONResponse(content={"status": "ensured", **final_status})


@router.post("/plugin/reinstall")
async def plugin_reinstall():
    """Hard reinstall flow: disable -> uninstall -> build (if needed) -> upload -> enable -> status."""
    status = await _compute_status()
    plugin_id = status.get("plugin_id") or PLUGIN_DEFAULT_ID

    if not MM_URL or not MM_TOKEN:
        return JSONResponse(
            status_code=400, content={"error": "MM_URL or MM_TOKEN not set", **status}
        )

    # Disable and uninstall if present
    if status.get("installed"):
        await _disable_plugin(plugin_id)
        uok, uerr = await _uninstall_plugin(plugin_id)
        if not uok:
            return JSONResponse(
                status_code=502,
                content={"error": f"Uninstall failed: {uerr}", **status},
            )
        if not await _wait_until_uninstalled(plugin_id):
            return JSONResponse(
                status_code=504,
                content={"error": "Timeout waiting for plugin to uninstall", **status},
            )

    # Ensure bundle exists (build if missing)
    manifest = read_plugin_manifest()
    version = manifest.get("version")
    bundle_path = get_local_bundle_path(plugin_id, version)
    if not bundle_path or not bundle_path.exists():
        try:
            backend_logger.info("Reinstall: bundle missing, building via make dist…")
            subprocess.run(
                ["make", "-C", str(get_plugin_repo_root()), "dist"], check=True
            )
        except Exception as e:
            return JSONResponse(
                status_code=409,
                content={"error": f"Bundle not found and build failed: {e}", **status},
            )
        bundle_path = get_local_bundle_path(plugin_id, version)
        if not bundle_path or not bundle_path.exists():
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"Bundle still not found after build: {bundle_path}",
                    **status,
                },
            )

    # Upload and enable
    ok, err = await _upload_bundle(bundle_path)
    if not ok:
        return JSONResponse(status_code=502, content={"error": err, **status})
    ok2, err2 = await _enable_plugin(plugin_id)
    if not ok2:
        return JSONResponse(status_code=502, content={"error": err2, **status})

    final_status = await _compute_status()
    return JSONResponse(content={"status": "reinstalled", **final_status})

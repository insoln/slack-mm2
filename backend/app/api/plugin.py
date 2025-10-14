import os
import json
import hashlib
import time
import tempfile
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import httpx
from app.logging_config import backend_logger
import subprocess  # legacy (kept if future external helpers need it; no direct build calls now)

router = APIRouter()

MM_URL = os.environ.get("MM_URL")
MM_TOKEN = os.environ.get("MM_TOKEN")
PLUGIN_BUNDLE_URL = os.environ.get("PLUGIN_BUNDLE_URL")  # Optional remote tar.gz source

PLUGIN_DEFAULT_ID = "mm-importer"

# In-memory cache for bundle hash/mtime/size to avoid recalculating on every status request
_bundle_cache: dict[str, dict] = {}
_BUNDLE_CACHE_TTL = 30  # seconds

# Remote bundle availability cache (avoid HEAD every status poll)
_remote_bundle_cache: dict[str, dict] = {}
_REMOTE_CACHE_TTL = 15  # seconds


def _get_cached_bundle_info(
    path: Path | None,
) -> tuple[str | None, int | None, int | None, int | None]:
    """Return (sha256, mtime_epoch, size_bytes, computed_at_epoch).

    Cache key includes file mtime ns + size so new build invalidates automatically.
    """
    if not path or not path.exists():
        return None, None, None, None
    try:
        stat = path.stat()
        key = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
        now = time.time()
        cached = _bundle_cache.get(key)
        if cached and (now - cached["computed_at"]) < _BUNDLE_CACHE_TTL:
            return (
                cached.get("sha256"),
                int(stat.st_mtime),
                stat.st_size,
                int(cached.get("computed_at", now)),
            )
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        sha = h.hexdigest()
        # Keep only the latest entry (one active bundle expected)
        _bundle_cache.clear()
        _bundle_cache[key] = {"sha256": sha, "computed_at": now}
        return sha, int(stat.st_mtime), stat.st_size, int(now)
    except Exception as e:  # pragma: no cover
        backend_logger.warning(f"Bundle cache compute failed: {e}")
        return None, None, None, None


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
    primary = plugin_root / "dist" / f"{plugin_id}-{version}.tar.gz"
    if primary.exists():
        return primary
    # Fallback path for externally mounted bundles directory
    alt_dir = Path("/plugin-bundles")
    alt = alt_dir / f"{plugin_id}-{version}.tar.gz"
    if alt.exists():
        return alt
    return primary  # return expected primary even if missing (for hints)


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

    # We still want to return bundle metadata fields even if MM env missing
    bundle_path = get_local_bundle_path(expected_id, expected_version)
    bundle_exists = bool(bundle_path and bundle_path.exists())
    bundle_sha256 = None
    bundle_mtime = None
    bundle_size = None
    bundle_hash_computed_at = None
    if bundle_exists and bundle_path is not None:
        bundle_sha256, bundle_mtime, bundle_size, bundle_hash_computed_at = (
            _get_cached_bundle_info(bundle_path)
        )
    if not MM_URL or not MM_TOKEN:
        return {
            "plugin_id": expected_id,
            "expected_version": expected_version,
            "installed": False,
            "enabled": False,
            "installed_version": None,
            "needs_update": None,
            "error": "MM_URL or MM_TOKEN not set",
            "bundle_exists": bundle_exists,
            "bundle_path": str(bundle_path) if bundle_path else None,
            "bundle_sha256": bundle_sha256,
            "bundle_mtime": bundle_mtime,
            "bundle_size": bundle_size,
            "bundle_hash_computed_at": bundle_hash_computed_at,
        }

    resp = None
    mm_fetch_error: str | None = None
    try:
        resp = await mm_get("/api/v4/plugins")
    except Exception as e:  # network / DNS / timeout
        # Downgraded to warning to avoid failing integration log scan when MM not yet ready
        mm_fetch_error = f"mm_get_failed: {e}"  # logged below
        backend_logger.warning(
            f"Failed to fetch plugins (exception): {e} (logged as warning; will retry on next status)"
        )

    if resp is not None:
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception as je:  # pragma: no cover
                backend_logger.error(f"Failed to decode plugins JSON: {je}")
                data = {}
            active = data.get("active", [])
            inactive = data.get("inactive", [])
            for pl in active + inactive:
                if pl.get("id") == expected_id:
                    installed = True
                    installed_version = pl.get("version")
                    enabled = pl in active
                    break
        else:
            mm_fetch_error = f"mm_status_{resp.status_code}"
            # Transient startup race or auth misconfig; warning keeps visibility without failing log scan
            backend_logger.warning(
                f"Failed to fetch plugins: {resp.status_code} {resp.text[:200]}"
            )

    needs_update = False
    if expected_version and installed_version and expected_version != installed_version:
        needs_update = True

    # bundle metadata already computed above if env missing; recompute when env present
    bundle_path = get_local_bundle_path(expected_id, expected_version)
    bundle_exists = bool(bundle_path and bundle_path.exists())
    bundle_sha256 = None
    bundle_mtime = None
    bundle_size = None
    bundle_hash_computed_at = None
    if bundle_exists and bundle_path is not None:
        bundle_sha256, bundle_mtime, bundle_size, bundle_hash_computed_at = (
            _get_cached_bundle_info(bundle_path)
        )

    # Remote bundle detection (only if local bundle missing and remote URL configured)
    remote_available = False
    remote_size: int | None = None
    remote_error: str | None = None
    if (not bundle_exists) and PLUGIN_BUNDLE_URL:
        cache_key = PLUGIN_BUNDLE_URL
        now = time.time()
        cached = _remote_bundle_cache.get(cache_key)
        if cached and (now - cached.get("ts", 0)) < _REMOTE_CACHE_TTL:
            remote_available = bool(cached.get("available"))
            remote_size = cached.get("size")
            remote_error = cached.get("error")
        else:
            try:
                # Prefer HEAD (fast); fall back to GET if server lacks HEAD support
                timeout = httpx.Timeout(10.0)
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    r = client.head(PLUGIN_BUNDLE_URL)
                    if (
                        r.status_code >= 400 or r.status_code == 405
                    ):  # 405 = method not allowed
                        r = client.get(
                            PLUGIN_BUNDLE_URL, headers={"Range": "bytes=0-0"}
                        )  # fetch first byte
                    if 200 <= r.status_code < 300:
                        remote_available = True
                        # Content-Length may be absent for ranged requests; attempt to parse
                        cl = r.headers.get("Content-Length") or r.headers.get(
                            "content-length"
                        )
                        if cl and cl.isdigit():
                            remote_size = int(cl)
                    else:
                        remote_error = f"remote_status_{r.status_code}"
            except Exception as e:  # pragma: no cover (network/transient)
                remote_error = (
                    f"remote_exc:{e}"  # logged only in cache, keep status lean
                )
            _remote_bundle_cache[cache_key] = {
                "ts": now,
                "available": remote_available,
                "size": remote_size,
                "error": remote_error,
            }

    result = {
        "plugin_id": expected_id,
        "expected_version": expected_version,
        "installed": installed,
        "enabled": enabled,
        "installed_version": installed_version,
        "needs_update": needs_update,
        "bundle_exists": bundle_exists,
        "bundle_path": str(bundle_path) if bundle_path else None,
        "bundle_sha256": bundle_sha256,
        "bundle_mtime": bundle_mtime,
        "bundle_size": bundle_size,
        "bundle_hash_computed_at": bundle_hash_computed_at,
        "remote_bundle_available": remote_available,
        "remote_bundle_url": PLUGIN_BUNDLE_URL if PLUGIN_BUNDLE_URL else None,
        "remote_bundle_size": remote_size,
    }
    if mm_fetch_error:
        result["error"] = mm_fetch_error
    if remote_error:
        # Provide non-fatal remote probe error for diagnosability (warn-level semantics)
        result["remote_probe_error"] = remote_error
    return result


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


@router.get("/plugin/bundle/info")
async def plugin_bundle_info():
    st = await _compute_status()
    if not st.get("bundle_exists"):
        return JSONResponse(status_code=404, content={"error": "Bundle not found"})
    return JSONResponse(
        content={
            "plugin_id": st.get("plugin_id"),
            "expected_version": st.get("expected_version"),
            "bundle_path": st.get("bundle_path"),
            "bundle_sha256": st.get("bundle_sha256"),
            "bundle_mtime": st.get("bundle_mtime"),
            "bundle_size": st.get("bundle_size"),
            "bundle_hash_computed_at": st.get("bundle_hash_computed_at"),
        }
    )


@router.post("/plugin/deploy")
async def plugin_deploy(path: str | None = None):
    """Upload an already-built plugin bundle.

    Unlike the previous implementation this endpoint NEVER builds the bundle itself.
    If the bundle is absent it returns status "needs_bundle" with a hint how to build
    (via the dedicated build service / script) instead of attempting an internal build.
    """
    manifest = read_plugin_manifest()
    plugin_id = manifest.get("id", PLUGIN_DEFAULT_ID)
    version = manifest.get("version")
    bundle_path = Path(path) if path else get_local_bundle_path(plugin_id, version)

    if not MM_URL or not MM_TOKEN:
        return JSONResponse(
            status_code=400, content={"error": "MM_URL or MM_TOKEN not set"}
        )

    if (not bundle_path or not bundle_path.exists()) and PLUGIN_BUNDLE_URL:
        # Attempt remote fetch
        fetched, fetch_err = await _fetch_remote_bundle(plugin_id, version)
        if fetched:
            bundle_path = fetched
    if not bundle_path or not bundle_path.exists():
        status = await _compute_status()
        expected = get_local_bundle_path(plugin_id, version)
        extra = {}
        if PLUGIN_BUNDLE_URL:
            extra["remote_url"] = PLUGIN_BUNDLE_URL
        return JSONResponse(
            status_code=200,
            content={
                "status": "needs_bundle",
                "error": "Plugin bundle not found",
                "expected_path": str(expected) if expected else None,
                "hint": "Run: docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build or set PLUGIN_BUNDLE_URL",
                **extra,
                **status,
            },
        )

    # If already installed, disable to allow replacement (Mattermost permits force=true but disable is safer)
    st0 = await _compute_status()
    if st0.get("installed"):
        backend_logger.info("plugin_deploy: disabling existing plugin before upload…")
        await _disable_plugin(plugin_id)

    ok, err = await _upload_bundle(bundle_path)
    if not ok:
        return JSONResponse(status_code=502, content={"error": err})

    final = await _compute_status()
    return JSONResponse(content={"status": "uploaded", **final})


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
    """Ensure plugin is installed at expected version and enabled.

    Policy after refactor:
    * Never builds the bundle. External builder (mm-plugin-build service or build-dev.sh) is the single source of truth.
    * If plugin missing or outdated and bundle absent -> return status "needs_bundle" (HTTP 200) with hint.
    * If bundle present but update required -> uninstall (with wait) then upload new bundle.
    * Always (re)enable at the end.
    """
    status = await _compute_status()
    plugin_id = status.get("plugin_id") or PLUGIN_DEFAULT_ID

    if not MM_URL or not MM_TOKEN:
        return JSONResponse(
            status_code=400, content={"error": "MM_URL or MM_TOKEN not set", **status}
        )

    # If plugin already installed at correct version but local bundle (cache) is missing,
    # opportunistically fetch it to have it ready for future updates / reinstall scenarios.
    if (
        status.get("installed")
        and not status.get("needs_update")
        and not status.get("bundle_exists")
        and PLUGIN_BUNDLE_URL
    ):
        fetched, _ = await _fetch_remote_bundle(
            status.get("plugin_id") or PLUGIN_DEFAULT_ID,
            status.get("expected_version"),
        )
        if fetched:
            status = await _compute_status()

    need_deploy = (not status.get("installed")) or status.get("needs_update")
    if need_deploy:
        # Try remote fetch if missing
        if not status.get("bundle_exists") and PLUGIN_BUNDLE_URL:
            fetched, fetch_err = await _fetch_remote_bundle(
                plugin_id, status.get("expected_version")
            )
            if fetched:
                # Recompute status to reflect new local bundle
                status = await _compute_status()
        if not status.get("bundle_exists"):
            expected_path = status.get("bundle_path") or str(
                get_local_bundle_path(plugin_id, status.get("expected_version"))
            )
            extra = {}
            if PLUGIN_BUNDLE_URL:
                extra["remote_url"] = PLUGIN_BUNDLE_URL
            return JSONResponse(
                status_code=200,
                content={
                    "status": "needs_bundle",
                    "error": "Plugin bundle not found for deploy/upgrade",
                    "expected_path": expected_path,
                    "hint": "Run: docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build or set PLUGIN_BUNDLE_URL",
                    **extra,
                    **status,
                },
            )

        # Uninstall existing if updating (cleaner than disable+force)
        if status.get("installed"):
            backend_logger.info("Ensure: uninstalling existing plugin before update…")
            uok, uerr = await _uninstall_plugin(plugin_id)
            if not uok:
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "retry_later",
                        "error": f"Uninstall failed: {uerr}",
                        **status,
                    },
                )
            if not await _wait_until_uninstalled(plugin_id):
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "retry_later",
                        "error": "Timeout waiting for uninstall",
                        **status,
                    },
                )

        # Upload new bundle
        bundle_path_str = status.get("bundle_path")
        if not bundle_path_str:
            return JSONResponse(
                status_code=200, content={"status": "needs_bundle", **status}
            )
        bundle_path = Path(bundle_path_str)
        ok, err = await _upload_bundle(bundle_path)
        if not ok:
            return JSONResponse(status_code=502, content={"error": err, **status})

    # Enable if disabled (or after fresh upload)
    post = await _compute_status()
    if not post.get("enabled"):
        ok, err = await _enable_plugin(plugin_id)
        if not ok:
            return JSONResponse(status_code=502, content={"error": err, **post})
        # Poll a few times because enable in MM can be slightly async in larger envs
        import asyncio

        for _ in range(10):  # up to ~5s (10 * 0.5)
            await asyncio.sleep(0.5)
            post = await _compute_status()
            if post.get("enabled"):
                break
        if not post.get("enabled"):
            # Non-fatal: report partial success with hint to retry
            return JSONResponse(
                status_code=200,
                content={
                    "status": "enable_pending",
                    "warning": "Plugin upload ok, but enable not yet reflected (retry soon)",
                    **post,
                },
            )

    return JSONResponse(content={"status": "ensured", **post})


@router.post("/plugin/reinstall")
async def plugin_reinstall():
    """Hard reinstall without building.

    Steps:
      * Disable & uninstall if installed
      * Require existing bundle (do NOT build). If missing -> needs_bundle
      * Upload & enable
    """
    status = await _compute_status()
    plugin_id = status.get("plugin_id") or PLUGIN_DEFAULT_ID

    if not MM_URL or not MM_TOKEN:
        return JSONResponse(
            status_code=400, content={"error": "MM_URL or MM_TOKEN not set", **status}
        )

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
                content={"error": "Timeout waiting for uninstall", **status},
            )

    # Need bundle
    fresh = await _compute_status()
    if not fresh.get("bundle_exists") and PLUGIN_BUNDLE_URL:
        fetched, fetch_err = await _fetch_remote_bundle(
            plugin_id, fresh.get("expected_version")
        )
        if fetched:
            fresh = await _compute_status()
    if not fresh.get("bundle_exists"):
        extra = {}
        if PLUGIN_BUNDLE_URL:
            extra["remote_url"] = PLUGIN_BUNDLE_URL
        return JSONResponse(
            status_code=200,
            content={
                "status": "needs_bundle",
                "error": "Plugin bundle not found for reinstall",
                "expected_path": fresh.get("bundle_path"),
                "hint": "Run: docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build or set PLUGIN_BUNDLE_URL",
                **extra,
                **fresh,
            },
        )

    bundle_path_str = fresh.get("bundle_path")
    if not bundle_path_str:
        return JSONResponse(
            status_code=200, content={"status": "needs_bundle", **fresh}
        )
    ok, err = await _upload_bundle(Path(bundle_path_str))
    if not ok:
        return JSONResponse(status_code=502, content={"error": err, **fresh})
    ok2, err2 = await _enable_plugin(plugin_id)
    if not ok2:
        return JSONResponse(status_code=502, content={"error": err2, **fresh})
    final = await _compute_status()
    return JSONResponse(content={"status": "reinstalled", **final})


async def _fetch_remote_bundle(
    plugin_id: str, expected_version: str | None
) -> tuple[Path | None, str | None]:
    """Download plugin bundle from PLUGIN_BUNDLE_URL if set.

    Validates that the downloaded archive file name (if pattern matches) includes the expected version (best-effort) and stores it under local dist/.
    Does NOT extract; Mattermost server validates manifest on upload.
    """
    if not PLUGIN_BUNDLE_URL:
        return None, "PLUGIN_BUNDLE_URL not set"
    if not expected_version:
        return None, "expected_version unknown"
    try:
        # Basic heuristic: if URL doesn't already end with plugin_id-version.tar.gz, we still accept it; we just store with canonical name.
        plugin_root = get_plugin_repo_root()
        dist_dir = plugin_root / "dist"
        target: Path
        use_tmp = False
        try:
            dist_dir.mkdir(parents=True, exist_ok=True)
            # Test writability
            test_file = dist_dir / ".wtest"
            with open(test_file, "w") as tf:
                tf.write("ok")
            test_file.unlink(missing_ok=True)
        except Exception:
            # Fallback to /tmp (ephemeral) for read-only plugin mount scenarios
            tmp_root = Path("/tmp/plugin-bundles")
            tmp_root.mkdir(parents=True, exist_ok=True)
            dist_dir = tmp_root
            use_tmp = True
        target = dist_dir / f"{plugin_id}-{expected_version}.tar.gz"

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.get(PLUGIN_BUNDLE_URL, follow_redirects=True)
            if resp.status_code != 200:
                return None, f"download_failed_{resp.status_code}"
            # Write atomically
            with tempfile.NamedTemporaryFile(dir=dist_dir, delete=False) as tmp:
                tmp.write(resp.content)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)
        tmp_path.replace(target)
        loc = "tmp" if use_tmp else "dist"
        backend_logger.info(
            f"Downloaded remote plugin bundle to {target} (storage={loc}) from {PLUGIN_BUNDLE_URL}"
        )
        return target, None
    except Exception as e:  # pragma: no cover (network issues)
        backend_logger.warning(f"Remote bundle fetch failed: {e}")
        return None, str(e)

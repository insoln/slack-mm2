from __future__ import annotations
import os
import httpx
from app.logging_config import backend_logger

# Shared async clients with connection pooling
_mm_client: httpx.AsyncClient | None = None
_generic_client: httpx.AsyncClient | None = None

def _get_limits() -> httpx.Limits:
    try:
        max_keepalive = int(os.getenv("MM_MAX_KEEPALIVE", "20"))
        max_conns = int(os.getenv("MM_MAX_CONNECTIONS", "100"))
    except Exception:
        max_keepalive, max_conns = 20, 100
    return httpx.Limits(max_keepalive_connections=max_keepalive, max_connections=max_conns)

def _get_mm_client() -> httpx.AsyncClient:
    global _mm_client
    if _mm_client is None:
        http2 = str(os.getenv("MM_HTTP2", "0")).lower() not in ("0", "false")
        _mm_client = httpx.AsyncClient(
            base_url=os.environ.get("MM_URL", ""),
            headers={"Authorization": f"Bearer {os.environ.get('MM_TOKEN','')}"},
            timeout=30,
            limits=_get_limits(),
            http2=http2,
        )
    return _mm_client

def _get_generic_client() -> httpx.AsyncClient:
    global _generic_client
    if _generic_client is None:
        _generic_client = httpx.AsyncClient(timeout=60, limits=_get_limits(), follow_redirects=True)
    return _generic_client

async def close_clients():
    global _mm_client, _generic_client
    try:
        if _mm_client is not None:
            await _mm_client.aclose()
    finally:
        _mm_client = None
    try:
        if _generic_client is not None:
            await _generic_client.aclose()
    finally:
        _generic_client = None


class MMApiMixin:
    def _redact_payload(self, payload):
        """Return a payload copy safe for logging: mask large/sensitive fields."""
        try:
            if isinstance(payload, dict):
                safe = dict(payload)
                if 'content_base64' in safe and isinstance(safe['content_base64'], str):
                    safe['content_base64'] = f"[redacted base64, {len(payload['content_base64'])} chars]"
                return safe
        except Exception:
            # Best-effort redaction; on any issue just return a placeholder
            return '[unloggable payload]'
        return payload

    async def mm_api_get(self, path: str):
        client = _get_mm_client()
        backend_logger.debug(f"MM API GET {client.base_url}{path}")
        resp = await client.get(path)
        backend_logger.debug(f"MM API GET {client.base_url}{path} status={resp.status_code} resp={resp.text}")
        return resp

    async def mm_api_post(self, path: str, payload: dict):
        client = _get_mm_client()
        backend_logger.debug(f"MM API POST {client.base_url}{path} payload={self._redact_payload(payload)}")
        resp = await client.post(path, json=payload)
        if resp.status_code >= 400:
            backend_logger.error(f"MM API POST {client.base_url}{path} status={resp.status_code} body={resp.text[:200]}")
        else:
            backend_logger.debug(f"MM API POST {client.base_url}{path} status={resp.status_code}")
        return resp

    async def mm_api_post_files(self, path: str, data_fields: dict, files: dict):
        """POST multipart/form-data with files using httpx 'files' API. Avoids logging file content."""
        client = _get_mm_client()
        # Log only field names and file names/sizes if available
        safe_files = {}
        try:
            for key, val in (files or {}).items():
                if isinstance(val, (tuple, list)) and len(val) >= 1:
                    fname = val[0]
                    fsize = None
                    if len(val) >= 2 and isinstance(val[1], (bytes, bytearray)):
                        fsize = len(val[1])
                    safe_files[key] = {"filename": fname, "bytes": fsize}
                else:
                    safe_files[key] = "(stream)"
        except Exception:
            safe_files = {k: "(unknown)" for k in (files or {}).keys()}
        backend_logger.debug(f"MM API POST(files) {client.base_url}{path} fields={list((data_fields or {}).keys())} files={safe_files}")
        resp = await client.post(path, data=data_fields or {}, files=files or {}, timeout=None)
        if resp.status_code >= 400:
            backend_logger.error(f"MM API POST(files) {client.base_url}{path} status={resp.status_code} body={resp.text[:200]}")
        else:
            backend_logger.debug(f"MM API POST(files) {client.base_url}{path} status={resp.status_code}")
        return resp

    async def mm_api_post_multipart(self, path: str, data, headers: dict):
        client = _get_mm_client()
        backend_logger.debug(f"MM API POST multipart {client.base_url}{path}")
        if hasattr(data, 'content_type') and hasattr(data, 'to_string'):
            headers['Content-Type'] = data.content_type
            resp = await client.post(path, content=data.to_string(), headers=headers, timeout=10)
        else:
            resp = await client.post(path, data=data, headers=headers, timeout=10)
        backend_logger.debug(f"MM API POST multipart {client.base_url}{path} status={resp.status_code} resp={resp.text}")
        return resp

    async def download_file(self, url: str, headers: dict | None = None):
        """Скачать файл по URL. Опционально с заголовками (например, Slack Bearer Token)."""
        backend_logger.debug(f"Downloading file from {url}")
        client = _get_generic_client()
        resp = await client.get(url, headers=headers or {}, timeout=30)
        backend_logger.debug(f"Download {url} status={resp.status_code}")
        return resp
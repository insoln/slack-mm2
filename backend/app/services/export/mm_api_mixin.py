import os
import httpx
from app.logging_config import backend_logger

class MMApiMixin:
    async def mm_api_get(self, path):
        url = f"{os.environ['MM_URL']}{path}"
        backend_logger.debug(f"MM API GET {url}")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {os.environ['MM_TOKEN']}"},
                timeout=10,
            )
            backend_logger.debug(f"MM API GET {url} status={resp.status_code} resp={resp.text}")
            return resp

    async def mm_api_post(self, path, payload):
        url = f"{os.environ['MM_URL']}{path}"
        backend_logger.debug(f"MM API POST {url} payload={payload}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {os.environ['MM_TOKEN']}"},
                json=payload,
                timeout=10,
            )
            backend_logger.debug(f"MM API POST {url} status={resp.status_code} resp={resp.text}")
            return resp

    async def mm_api_post_multipart(self, path, data, headers):
        url = f"{os.environ['MM_URL']}{path}"
        backend_logger.debug(f"MM API POST multipart {url}")
        async with httpx.AsyncClient() as client:
            # Если data это MultipartEncoder, используем его content_type и data
            if hasattr(data, 'content_type') and hasattr(data, 'to_string'):
                # Для MultipartEncoder из requests_toolbelt
                headers['Content-Type'] = data.content_type
                resp = await client.post(
                    url,
                    content=data.to_string(),
                    headers=headers,
                    timeout=10,
                )
            else:
                # Для обычных данных
                resp = await client.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=10,
                )
            backend_logger.debug(f"MM API POST multipart {url} status={resp.status_code} resp={resp.text}")
            return resp

    async def download_file(self, url):
        """Скачать файл по URL"""
        backend_logger.debug(f"Downloading file from {url}")
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=30)
            backend_logger.debug(f"Download {url} status={resp.status_code}")
            return resp 
from __future__ import annotations

import base64
import os
from typing import Optional
import asyncio

from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin
from app.logging_config import backend_logger
from sqlalchemy import select
from app.models.base import SessionLocal
from app.models.entity import Entity


class AttachmentExporter(ExporterBase, LoggingMixin, MMApiMixin):
    """
    Uploads a Slack attachment to Mattermost via plugin and stores returned file_id.
    - Reads Slack file info from entity.raw_data (expects id, url_private, name/filename)
    - Resolves target channel from the message relation (posted_in) or raw_data['channel_id']
    - Downloads content from Slack using SLACK_BOT_TOKEN and POSTs to /plugins/mm-importer/api/v1/attachment
    - On success sets entity.mattermost_id and marks status=success
    """

    async def export_entity(self) -> None:
        self.log_export(f"Экспорт аттачмента {self.entity.slack_id}")

        raw = self.entity.raw_data or {}
        filename = (
            raw.get("name") or raw.get("title") or raw.get("filename") or "file.bin"
        )

        # Enforce size cap (skip huge files safely)
        try:
            max_mb_env = os.environ.get("ATTACHMENT_MAX_MB")
            if max_mb_env is not None:
                max_mb = float(max_mb_env)
                size_bytes = raw.get("size")
                if isinstance(size_bytes, int) and size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb > max_mb:
                        await self.set_status(
                            "skipped",
                            error=f"Attachment {filename} {size_mb:.1f}MB exceeds cap {max_mb:.1f}MB",
                        )
                        backend_logger.warning(
                            f"Skip oversized attachment {self.entity.slack_id}: {size_mb:.1f}MB > {max_mb:.1f}MB"
                        )
                        return
        except Exception:
            # Best-effort; don't block export on config errors
            pass

        # Determine channel_id where to upload: prefer message relation, fallback to raw_data.channel_id
        channel_id = await self._resolve_mm_channel_id_for_attachment()
        if not channel_id:
            await self.set_status("failed", error="No target channel for attachment")
            return

        # Prefer streaming multipart upload to avoid base64 overhead
        prefer_multipart = os.environ.get("ATTACHMENT_MULTIPART", "1") not in (
            "0",
            "false",
            "False",
        )

        # Obtain content as base64, with retry/backoff for robustness
        async def _retry_download(url, headers, attempts=3, base_delay=1.0):
            last_exc = None
            for i in range(attempts):
                try:
                    resp = await self.download_file(url, headers=headers)
                    if getattr(resp, "status_code", 0) == 200:
                        return resp
                    else:
                        last_exc = Exception(f"HTTP {getattr(resp, 'status_code', 0)}")
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                await asyncio.sleep(base_delay * (2**i))
            raise last_exc or Exception("download failed")

        content_b64: Optional[str] = raw.get("content_base64")
        if not content_b64:
            url = raw.get("url_private") or raw.get("url_private_download")
            if not url:
                await self.set_status(
                    "failed",
                    error="No content source: neither content_base64 nor url_private",
                )
                return
            slack_token = os.environ.get("SLACK_BOT_TOKEN") or os.environ.get(
                "SLACK_TOKEN"
            )
            headers = {"Authorization": f"Bearer {slack_token}"} if slack_token else {}
            try:
                resp = await _retry_download(url, headers=headers)
            except Exception as e:  # noqa: BLE001
                await self.set_status(
                    "failed", error=f"Failed to download from Slack: {e}"
                )
                return
            if prefer_multipart:
                # Write to a temp file and stream as multipart file (auto-cleanup)
                import tempfile

                fields = {"channel_id": channel_id, "filename": filename}
                files = None
                with tempfile.NamedTemporaryFile(
                    prefix="att-", suffix=".bin", delete=True
                ) as tf:
                    try:
                        tf.write(resp.content)
                        tf.flush()
                    except Exception as e:  # noqa: BLE001
                        await self.set_status(
                            "failed", error=f"Temp file write failed: {e}"
                        )
                        return
                    try:
                        files = {
                            "file": (
                                filename,
                                open(tf.name, "rb"),
                                "application/octet-stream",
                            )
                        }
                        resp2 = await self.mm_api_post_files(
                            "/plugins/mm-importer/api/v1/attachment_multipart",
                            fields,
                            files,
                        )
                    finally:
                        try:
                            if files is not None:
                                fh = files.get("file")
                                if (
                                    isinstance(fh, (tuple, list))
                                    and len(fh) >= 2
                                    and hasattr(fh[1], "close")
                                ):
                                    fh[1].close()
                        except Exception:
                            pass
                if resp2.status_code not in (200, 201):
                    try:
                        data = resp2.json()
                        err = data.get("error") or data
                    except Exception:
                        err = resp2.text
                    await self.set_status(
                        "failed",
                        error=f"Plugin upload failed: {resp2.status_code} {err}",
                    )
                    return
                data = resp2.json()
                file_id = data.get("file_id")
                if not file_id:
                    await self.set_status(
                        "failed", error=f"No file_id in plugin response: {data}"
                    )
                    return
                self.entity.mattermost_id = file_id
                await self.set_status("success")
                backend_logger.debug(f"Attachment uploaded, file_id={file_id}")
                return
            else:
                content = resp.content  # httpx.Response
                content_b64 = base64.b64encode(content).decode("ascii")

        payload = {
            "channel_id": channel_id,
            "filename": filename,
            "content_base64": content_b64,
        }

        async def _retry_plugin_post(attempts=3, base_delay=1.0):
            last_err = None
            for i in range(attempts):
                try:
                    resp = await self.mm_api_post(
                        "/plugins/mm-importer/api/v1/attachment", payload
                    )
                    # retry on 5xx/429; accept 2xx
                    if 200 <= resp.status_code < 300:
                        return resp
                    if resp.status_code in (429,) or resp.status_code >= 500:
                        last_err = f"HTTP {resp.status_code}"
                    else:
                        # don't retry 4xx client errors
                        return resp
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
                await asyncio.sleep(base_delay * (2**i))
            raise Exception(last_err or "plugin post failed")

        try:
            resp = await _retry_plugin_post()
            if resp.status_code not in (200, 201):
                # Try to parse error
                try:
                    data = resp.json()
                    err = data.get("error") or data
                except Exception:
                    err = resp.text
                await self.set_status(
                    "failed", error=f"Plugin upload failed: {resp.status_code} {err}"
                )
                return
            data = resp.json()
            file_id = data.get("file_id")
            if not file_id:
                await self.set_status(
                    "failed", error=f"No file_id in plugin response: {data}"
                )
                return
            self.entity.mattermost_id = file_id
            await self.set_status("success")
            backend_logger.debug(f"Attachment uploaded, file_id={file_id}")
        except Exception as e:  # noqa: BLE001
            await self.set_status("failed", error=str(e))

    async def _resolve_mm_channel_id_for_attachment(self) -> Optional[str]:
        """Find the MM channel id where this attachment should be uploaded.
        Strategy:
        - If raw_data has channel_id and we can map it to entity.channel mapping, use that
        - Else: traverse relation attached_to -> message -> posted_in -> channel and get its mattermost_id
        """
        # 1) Try raw_data.channel_id path
        raw = self.entity.raw_data or {}
        ch_slack_id = raw.get("channel_id")
        async with SessionLocal() as session:
            if ch_slack_id:
                q = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "channel")
                        & (Entity.slack_id == ch_slack_id)
                    )
                )
                ch_entity = q.scalar_one_or_none()
                if ch_entity is not None:
                    mmid = getattr(ch_entity, "mattermost_id", None)
                    if isinstance(mmid, str) and mmid:
                        return mmid

            # 2) Walk relations: this attachment is from_entity in entity_relations to message, then message posted_in channel
            # Find message entity via attached_to relation
            from app.models.entity_relation import (
                EntityRelation,
            )  # local import to avoid cycles

            q_att = await session.execute(
                select(EntityRelation, Entity)
                .join(Entity, Entity.id == EntityRelation.to_entity_id)
                .where(
                    (EntityRelation.from_entity_id == self.entity.id)
                    & (EntityRelation.relation_type == "attached_to")
                )
            )
            row = q_att.first()
            if row:
                _, msg_entity = row
                # Now find channel via posted_in relation
                q_ch = await session.execute(
                    select(EntityRelation, Entity)
                    .join(Entity, Entity.id == EntityRelation.to_entity_id)
                    .where(
                        (EntityRelation.from_entity_id == msg_entity.id)
                        & (EntityRelation.relation_type == "posted_in")
                    )
                )
                ch_row = q_ch.first()
                if ch_row:
                    _, ch_entity2 = ch_row
                    if ch_entity2 is not None:
                        mmid2 = getattr(ch_entity2, "mattermost_id", None)
                        if isinstance(mmid2, str) and mmid2:
                            return mmid2

        return None

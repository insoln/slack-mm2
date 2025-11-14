from __future__ import annotations

import os
from typing import Optional
import asyncio
from urllib.parse import urlparse, urlunparse

from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin
from app.logging_config import backend_logger
from sqlalchemy import select
from app.models.base import SessionLocal
from app.models.entity import Entity


def normalize_slack_file_url(url: str) -> str:
    """
    Normalize Slack file URL by removing query parameters.

    Slack's url_private URLs contain temporary ?t= tokens that expire over time.
    These expired tokens cause 404 errors even with valid Bearer auth headers.
    Stripping the query parameters allows the Slack API to authenticate using
    the Bearer token alone, resolving download failures.

    Args:
        url: The Slack file URL, potentially with query parameters

    Returns:
        The normalized URL without query parameters
    """
    if not url:
        return url

    parsed = urlparse(url)
    # Reconstruct URL without query parameters, preserving fragment if present
    normalized = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", "", parsed.fragment)
    )
    return normalized


class AttachmentExporter(ExporterBase, LoggingMixin, MMApiMixin):
    """
    Exports a Slack attachment to Mattermost via plugin direct download and stores returned file_id.
    - Reads Slack file info from entity.raw_data (expects id, url_private, name/filename)
    - Resolves target channel from the message relation (posted_in) or raw_data['channel_id']
    - Sends file URL and auth token to plugin via /plugins/mm-importer/api/v1/attachment_from_url
    - Plugin downloads content from Slack directly and uploads to Mattermost
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

        # Get file URL and auth header
        url = raw.get("url_private") or raw.get("url_private_download")
        if not url:
            await self.set_status(
                "failed",
                error="No content source: neither url_private nor url_private_download",
            )
            return

        # Normalize URL: strip query parameters (expired ?t= tokens cause 404s)
        url = normalize_slack_file_url(url)

        slack_token = os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_TOKEN")
        if not slack_token:
            await self.set_status("failed", error="No Slack token available")
            return

        auth_header = f"Bearer {slack_token}"

        # Try to resolve the actual user who uploaded the attachment
        user_id = await self._resolve_mm_user_id_for_attachment()

        # Send URL to plugin for direct download and upload
        async def _retry_plugin_post(attempts=3, base_delay=1.0):
            last_err = None
            for i in range(attempts):
                try:
                    resp = await self.mm_api_post_attachment_from_url(
                        channel_id, filename, url, auth_header, user_id
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
            backend_logger.debug(f"Attachment uploaded via URL, file_id={file_id}")
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

    async def _resolve_mm_user_id_for_attachment(self) -> Optional[str]:
        """Find the Mattermost user id who uploaded this attachment.
        Strategy: traverse attached_to -> message and get the user from that message.
        """
        async with SessionLocal() as session:
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
                msg_raw = msg_entity.raw_data or {}
                slack_uid = msg_raw.get("user") or msg_raw.get("bot_id")

                if slack_uid:
                    # Look up user entity by slack_id to get mattermost_id
                    q_user = await session.execute(
                        select(Entity).where(
                            (Entity.entity_type == "user")
                            & (Entity.slack_id == slack_uid)
                        )
                    )
                    user_entity = q_user.scalar_one_or_none()
                    if user_entity is not None:
                        mmid = getattr(user_entity, "mattermost_id", None)
                        if isinstance(mmid, str) and mmid:
                            return mmid
        return None

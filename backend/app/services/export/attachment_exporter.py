from __future__ import annotations

import base64
import os
from typing import Optional

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

    async def export_entity(self):
        self.log_export(f"Экспорт аттачмента {self.entity.slack_id}")

        raw = self.entity.raw_data or {}
        filename = raw.get("name") or raw.get("title") or raw.get("filename") or "file.bin"

        # Determine channel_id where to upload: prefer message relation, fallback to raw_data.channel_id
        channel_id = await self._resolve_mm_channel_id_for_attachment()
        if not channel_id:
            await self.set_status("failed", error="No target channel for attachment")
            return

        # Obtain content as base64
        content_b64: Optional[str] = raw.get("content_base64")
        if not content_b64:
            url = raw.get("url_private") or raw.get("url_private_download")
            if not url:
                await self.set_status("failed", error="No content source: neither content_base64 nor url_private")
                return
            slack_token = os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_TOKEN")
            headers = {"Authorization": f"Bearer {slack_token}"} if slack_token else {}
            resp = await self.download_file(url, headers=headers)
            if getattr(resp, "status_code", 200) != 200:
                await self.set_status("failed", error=f"Failed to download from Slack: {getattr(resp,'status_code',0)}")
                return
            content = resp.content  # httpx.Response
            content_b64 = base64.b64encode(content).decode("ascii")

        payload = {
            "channel_id": channel_id,
            "filename": filename,
            "content_base64": content_b64,
        }

        try:
            resp = await self.mm_api_post("/plugins/mm-importer/api/v1/attachment", payload)
            if resp.status_code not in (200, 201):
                # Try to parse error
                try:
                    data = resp.json()
                    err = data.get("error") or data
                except Exception:
                    err = resp.text
                await self.set_status("failed", error=f"Plugin upload failed: {resp.status_code} {err}")
                return
            data = resp.json()
            file_id = data.get("file_id")
            if not file_id:
                await self.set_status("failed", error=f"No file_id in plugin response: {data}")
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
                    select(Entity).where((Entity.entity_type == "channel") & (Entity.slack_id == ch_slack_id))
                )
                ch_entity = q.scalar_one_or_none()
                if ch_entity is not None:
                    mmid = getattr(ch_entity, "mattermost_id", None)
                    if isinstance(mmid, str) and mmid:
                        return mmid

            # 2) Walk relations: this attachment is from_entity in entity_relations to message, then message posted_in channel
            # Find message entity via attached_to relation
            from app.models.entity_relation import EntityRelation  # local import to avoid cycles
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

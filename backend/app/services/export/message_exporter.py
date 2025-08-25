from __future__ import annotations

import math
from typing import List, Optional

from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin
from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.entity import Entity
from sqlalchemy import select


class MessageExporter(ExporterBase, LoggingMixin, MMApiMixin):
    """
    Exports a Slack message to Mattermost via the plugin /import endpoint.
    - Resolves channel (posted_in relation) and author (posted_by relation or user lookup)
    - Collects uploaded attachment file_ids via attached_to relations
    - Handles threads: if thread_ts != ts, tries to resolve root post's mattermost_id
    - Sets entity.mattermost_id to created post id
    """

    async def export_entity(self):
        self.log_export(f"Экспорт сообщения {self.entity.slack_id}")

        raw = self.entity.raw_data or {}

        # Resolve required IDs
        channel_id = await self._resolve_mm_channel_id_for_message()
        if not channel_id:
            await self.set_status("failed", error="No target channel for message")
            return

        user_id = await self._resolve_mm_user_id_for_message()
        if not user_id:
            await self.set_status("failed", error="No author (user_id) for message")
            return

        file_ids = await self._collect_file_ids()

        # Build message text with rich-text conversion when possible
        text = await self._build_message_text(raw)
        if not (text and text.strip()):
            # If attachments exist, a single space is enough; otherwise put a hyphen to make it visible
            text = " " if file_ids else "-"

        # Timestamp to milliseconds
        create_at = self._parse_ts_ms(raw.get("ts"))

        # Root/thread
        root_id = await self._resolve_root_post_id()
        if (self.entity.raw_data or {}).get("thread_ts") and not root_id:
            backend_logger.debug(
                f"Message {self.entity.slack_id} is a reply but root post_id not found yet; posting as top-level for now"
            )

        # Best-effort: ensure author is a channel member to prevent CreatePost failure
        try:
            _ = await self.mm_api_post(
                "/plugins/mm-importer/api/v1/channel/members",
                {"channel_id": channel_id, "user_ids": [user_id]},
            )
        except Exception as e:  # noqa: BLE001
            backend_logger.debug(f"Ensure channel membership failed (non-fatal): {e}")

        payload = {
            "user_id": user_id,
            "channel_id": channel_id,
            "message": text,
            "create_at": create_at or 0,
        }
        if root_id:
            payload["root_id"] = root_id
        if file_ids:
            payload["file_ids"] = file_ids

        try:
            resp = await self.mm_api_post("/plugins/mm-importer/api/v1/import", payload)
            if resp.status_code not in (200, 201):
                try:
                    data = resp.json()
                    err = data.get("error") or data
                except Exception:
                    err = resp.text
                await self.set_status("failed", error=f"Plugin import failed: {resp.status_code} {err}")
                return
            data = resp.json()
            post_id = data.get("post_id")
            if not post_id:
                await self.set_status("failed", error=f"No post_id in plugin response: {data}")
                return
            self.entity.mattermost_id = post_id
            await self.set_status("success")
            backend_logger.debug(f"Message exported, post_id={post_id}")
        except Exception as e:  # noqa: BLE001
            await self.set_status("failed", error=str(e))

    async def _build_message_text(self, raw: dict) -> str:
        """Convert Slack message (text or blocks) to Mattermost-friendly Markdown.
        - Prefer rich "blocks" if present
        - Then render classic Slack attachments (title/text/actions)
        - Otherwise, convert Slack plain text markup (<@U>, <#C>, <url|label>) to Markdown
        """
        blocks = raw.get("blocks") or []
        if isinstance(blocks, list) and blocks:
            try:
                md = await self._blocks_to_markdown(blocks)
                if md and md.strip():
                    return md
                # If rich conversion produced nothing, fall back to plain text
                backend_logger.debug("Rich blocks produced empty text, falling back to raw text conversion")
            except Exception as e:  # noqa: BLE001
                backend_logger.debug(f"Rich blocks conversion failed, fallback to text: {e}")
        # Classic attachments (e.g., Alertmanager)
        atts = raw.get("attachments") or []
        if isinstance(atts, list) and atts:
            try:
                md = await self._attachments_to_markdown(atts)
                if md and md.strip():
                    return md
            except Exception as e:  # noqa: BLE001
                backend_logger.debug(f"Attachments conversion failed, fallback to text: {e}")
        # Fallback to text conversion
        txt = raw.get("text") or ""
        return await self._slack_text_to_markdown(txt)

    async def _attachments_to_markdown(self, attachments: list) -> str:
        parts: list[str] = []
        for a in attachments:
            lines: list[str] = []
            pretext = a.get("pretext")
            if pretext:
                lines.append(await self._slack_text_to_markdown(pretext))
            title = a.get("title")
            title_link = a.get("title_link")
            if title:
                if title_link:
                    lines.append(f"[{title}]({title_link})")
                else:
                    lines.append(f"**{title}**")
            text = a.get("text")
            if text:
                lines.append(await self._slack_text_to_markdown(text))
            # Actions rendered as inline links
            actions = a.get("actions") or []
            action_links: list[str] = []
            for act in actions:
                t = act.get("text") or ""
                url = act.get("url") or ""
                if t and url:
                    action_links.append(f"[{t}]({url})")
                elif t:
                    action_links.append(t)
            if action_links:
                lines.append(" ".join(action_links))
            # Fallback if still empty
            if not lines:
                fallback = a.get("fallback")
                if fallback:
                    lines.append(await self._slack_text_to_markdown(fallback))
            if lines:
                parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)

    async def _slack_text_to_markdown(self, txt: str) -> str:
        import re
        if not txt:
            return ""

        # Special mentions
        txt = re.sub(r"<!here>", "@here", txt)
        txt = re.sub(r"<!channel>", "@channel", txt)
        txt = re.sub(r"<!everyone>", "@all", txt)

        # Links with labels: <url|label>
        def repl_link(m):
            url = m.group(1)
            label = m.group(2)
            return f"[{label}]({url})"
        txt = re.sub(r"<((?:https?|mailto):[^>|]+)\|([^>]+)>", repl_link, txt)

        # Naked angled links: <url>
        txt = re.sub(r"<((?:https?|mailto):[^>]+)>", r"\1", txt)

        # User mentions: <@U12345|optional>
        async def repl_user(match):
            sid = match.group(1)
            username = await self._resolve_username_by_slack_id(sid)
            return f"@{username or sid}"

        # Channel mentions: <#C12345|optional>
        async def repl_channel(match):
            cid = match.group(1)
            ch_name = await self._resolve_channel_name_by_slack_id(cid)
            return f"~{ch_name or cid}"

        # Apply async replacements sequentially
        # Users
        for m in list(re.finditer(r"<@([A-Z0-9]+)(?:\|[^>]+)?>", txt)):
            sid = m.group(1)
            username = await self._resolve_username_by_slack_id(sid)
            if username:
                txt = txt.replace(m.group(0), f"@{username}")
            else:
                txt = txt.replace(m.group(0), f"@{sid}")

        # Channels
        for m in list(re.finditer(r"<#([A-Z0-9]+)(?:\|[^>]+)?>", txt)):
            cid = m.group(1)
            ch_name = await self._resolve_channel_name_by_slack_id(cid)
            if ch_name:
                txt = txt.replace(m.group(0), f"~{ch_name}")
            else:
                txt = txt.replace(m.group(0), f"~{cid}")

        return txt

    async def _blocks_to_markdown(self, blocks: list) -> str:
        lines: list[str] = []
        for b in blocks:
            btype = b.get("type")
            if btype == "rich_text":
                for el in b.get("elements", []) or []:
                    s = await self._rich_element_to_md(el)
                    if s:
                        lines.append(s)
                continue

            if btype == "section":
                # section.text {type: mrkdwn|plain_text, text: ...} OR fields
                txt_obj = b.get("text")
                if isinstance(txt_obj, dict):
                    ttype = txt_obj.get("type")
                    ttext = txt_obj.get("text", "")
                    if ttype == "mrkdwn":
                        lines.append(await self._slack_text_to_markdown(ttext))
                    else:
                        lines.append(ttext)
                else:
                    # fields: array of text objects
                    fields = b.get("fields") or []
                    for f in fields:
                        if isinstance(f, dict):
                            ttype = f.get("type")
                            ttext = f.get("text", "")
                            if ttype == "mrkdwn":
                                lines.append(await self._slack_text_to_markdown(ttext))
                            else:
                                lines.append(ttext)
                continue

            if btype == "header":
                txt_obj = b.get("text")
                txt = txt_obj.get("text", "") if isinstance(txt_obj, dict) else ""
                if txt:
                    lines.append(f"# {txt}")
                continue

            if btype == "divider":
                lines.append("---")
                continue

            if btype == "context":
                items = []
                for el in b.get("elements", []) or []:
                    if el.get("type") in ("plain_text", "mrkdwn"):
                        t = el.get("text", "")
                        if el.get("type") == "mrkdwn":
                            t = await self._slack_text_to_markdown(t)
                        items.append(t)
                    else:
                        # Try rich element conversion as fallback
                        s = await self._rich_element_to_md(el)
                        if s:
                            items.append(s)
                if items:
                    lines.append(" ".join([i for i in items if i]))
                continue

            if btype == "image":
                url = b.get("image_url") or ""
                alt = b.get("alt_text") or ""
                if url:
                    lines.append(f"![{alt}]({url})" if alt else url)
                continue
        return "\n".join(lines)

    async def _rich_element_to_md(self, el: dict) -> str:
        t = el.get("type")
        if t == "rich_text_section":
            parts: list[str] = []
            for e in el.get("elements", []) or []:
                s = await self._rich_element_to_md(e)
                if s:
                    parts.append(s)
            # Join with nothing to preserve inline formatting; ensure not empty
            return "".join(parts) or ""
        if t == "rich_text_list":
            style = el.get("style") or "bullet"
            bullet = "- " if style == "bullet" else "1. "
            out: list[str] = []
            for item in el.get("elements", []) or []:
                text = await self._rich_element_to_md(item)
                if text:
                    for line in text.splitlines() or [text]:
                        out.append(f"{bullet}{line}")
            return "\n".join(out)
        if t == "rich_text_quote":
            content = await self._rich_element_to_md({"type": "rich_text_section", "elements": el.get("elements", []) or []})
            return "\n".join([f"> {ln}" for ln in content.splitlines()])
        if t == "rich_text_preformatted":
            content = await self._rich_element_to_md({"type": "rich_text_section", "elements": el.get("elements", []) or []})
            return f"```\n{content}\n```"
        if t == "rich_text_line_break":
            return "\n"
        if t == "text":
            text = el.get("text", "")
            style = el.get("style") or {}
            if style.get("code"):
                return f"`{text}`"
            if style.get("bold"):
                text = f"**{text}**"
            if style.get("italic"):
                text = f"_{text}_"
            if style.get("strike"):
                text = f"~~{text}~~"
            return text
        if t == "emoji":
            name = el.get("name") or ""
            return f":{name}:" if name else ""
        if t == "user":
            uid = el.get("user_id")
            uname = await self._resolve_username_by_slack_id(uid) if uid else None
            return f"@{uname}" if uname else (f"@{uid}" if uid else "")
        if t == "usergroup":
            # Represent user groups as @group_name if possible; fallback to ID
            gid = el.get("usergroup_id")
            return f"@{gid}" if gid else ""
        if t == "channel":
            cid = el.get("channel_id")
            ch_name = await self._resolve_channel_name_by_slack_id(cid) if cid else None
            return f"~{ch_name}" if ch_name else (f"~{cid}" if cid else "")
        if t == "link":
            url = el.get("url", "")
            text = el.get("text") or url
            return f"[{text}]({url})" if url else text
        if t == "date":
            # Example: {type:'date', timestamp: 1234567890, format:'date_short'}
            ts = el.get("timestamp")
            # leave as plain text; improving to format can be future work
            return f"{ts}" if ts else ""
        # Fallback: try nested elements
        parts: list[str] = []
        for e in el.get("elements", []) or []:
            s = await self._rich_element_to_md(e)
            if s:
                parts.append(s)
        return "".join(parts)

    async def _resolve_username_by_slack_id(self, slack_uid: Optional[str]) -> Optional[str]:
        if not slack_uid:
            return None
        async with SessionLocal() as session:
            q = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "user") & (Entity.slack_id == slack_uid)
                )
            )
            ent = q.scalar_one_or_none()
            if ent is None:
                return None
            # Prefer Mattermost username via API only if needed; use Slack name from raw_data as our UserExporter mirrors it
            raw = ent.raw_data or {}
            return raw.get("name") or slack_uid

    async def _resolve_channel_name_by_slack_id(self, slack_cid: Optional[str]) -> Optional[str]:
        if not slack_cid:
            return None
        async with SessionLocal() as session:
            q = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "channel") & (Entity.slack_id == slack_cid)
                )
            )
            ent = q.scalar_one_or_none()
            if ent is None:
                return None
            raw = ent.raw_data or {}
            # Slack channel names are usually compatible with MM; plugin also normalized names
            return raw.get("name")

    async def _resolve_mm_channel_id_for_message(self) -> Optional[str]:
        """Find the Mattermost channel id where this message belongs (posted_in),
        fallback to raw_data.channel_id mapping.
        """
        async with SessionLocal() as session:
            from app.models.entity_relation import EntityRelation
            # Preferred: relation posted_in
            q = await session.execute(
                select(EntityRelation, Entity)
                .join(Entity, Entity.id == EntityRelation.to_entity_id)
                .where(
                    (EntityRelation.from_entity_id == self.entity.id)
                    & (EntityRelation.relation_type == "posted_in")
                )
            )
            row = q.first()
            if row:
                _, ch_entity = row
                mmid = getattr(ch_entity, "mattermost_id", None)
                if isinstance(mmid, str) and mmid:
                    return mmid

            # Fallback: raw_data.channel_id -> channel entity -> mattermost_id
            raw = self.entity.raw_data or {}
            ch_slack_id = raw.get("channel_id")
            if ch_slack_id:
                q2 = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "channel") & (Entity.slack_id == ch_slack_id)
                    )
                )
                ch_entity2 = q2.scalar_one_or_none()
                if ch_entity2:
                    mmid2 = getattr(ch_entity2, "mattermost_id", None)
                    if isinstance(mmid2, str) and mmid2:
                        return mmid2
        return None

    async def _resolve_mm_user_id_for_message(self) -> Optional[str]:
        """Find the Mattermost user id of the message author.
        Prefer relation posted_by -> user.mattermost_id, fallback to user lookup by slack_id,
        and finally fallback to the current token's user (admin) via /users/me.
        """
        # 1) Via posted_by relation
        async with SessionLocal() as session:
            from app.models.entity_relation import EntityRelation
            q = await session.execute(
                select(EntityRelation, Entity)
                .join(Entity, Entity.id == EntityRelation.from_entity_id)
                .where(
                    (EntityRelation.to_entity_id == self.entity.id)
                    & (EntityRelation.relation_type == "posted_by")
                )
            )
            row = q.first()
            if row:
                _, user_entity = row
                mmid = getattr(user_entity, "mattermost_id", None)
                if isinstance(mmid, str) and mmid:
                    return mmid

        # 2) Lookup by slack user id in raw_data
        raw = self.entity.raw_data or {}
        slack_uid = raw.get("user") or raw.get("bot_id")
        if slack_uid:
            async with SessionLocal() as session:
                q2 = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "user") & (Entity.slack_id == slack_uid)
                    )
                )
                user_entity = q2.scalar_one_or_none()
                if user_entity:
                    mmid2 = getattr(user_entity, "mattermost_id", None)
                    if isinstance(mmid2, str) and mmid2:
                        return mmid2

        # 3) Fallback: current token user (admin)
        try:
            resp = await self.mm_api_get("/api/v4/users/me")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("id")
        except Exception as e:  # noqa: BLE001
            backend_logger.error(f"Не удалось получить /users/me для fallback автора: {e}")
        return None

    async def _collect_file_ids(self) -> List[str]:
        """Collect mattermost file IDs for attachments attached to this message."""
        file_ids: List[str] = []
        async with SessionLocal() as session:
            from app.models.entity_relation import EntityRelation
            q = await session.execute(
                select(EntityRelation, Entity)
                .join(Entity, Entity.id == EntityRelation.from_entity_id)
                .where(
                    (EntityRelation.to_entity_id == self.entity.id)
                    & (EntityRelation.relation_type == "attached_to")
                )
            )
            rows = q.fetchall()
            for _, att_entity in rows:
                mmid = getattr(att_entity, "mattermost_id", None)
                if isinstance(mmid, str) and mmid:
                    file_ids.append(mmid)
        return file_ids

    async def _resolve_root_post_id(self) -> Optional[str]:
        raw = self.entity.raw_data or {}
        ts = raw.get("ts")
        thread_ts = raw.get("thread_ts")
        if not thread_ts or thread_ts == ts:
            return None

        # Find parent message entity by slack thread_ts
        async with SessionLocal() as session:
            q = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "message") & (Entity.slack_id == thread_ts)
                )
            )
            parent = q.scalar_one_or_none()
            if parent:
                mmid = getattr(parent, "mattermost_id", None)
                if isinstance(mmid, str) and mmid:
                    return mmid
        return None

    def _parse_ts_ms(self, ts_str: Optional[str]) -> Optional[int]:
        if not ts_str:
            return None
        # Slack ts is like "1234567890.123456" (seconds.fraction)
        try:
            val = float(ts_str)
            ms = int(math.floor(val * 1000.0))
            return ms
        except Exception:  # noqa: BLE001
            return None

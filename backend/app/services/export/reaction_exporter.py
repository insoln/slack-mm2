from __future__ import annotations

import math
from typing import Optional

from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.entity import Entity
from app.models.entity_relation import EntityRelation
from sqlalchemy import select

from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin
from .custom_emoji_exporter import transliterate_cyrillic


class ReactionExporter(ExporterBase, LoggingMixin, MMApiMixin):
    """Exports a Slack reaction to Mattermost via plugin /reaction endpoint.
    Requirements:
      - Message (post) must already exist (mattermost_id set on message entity)
      - Reacting user must exist in MM; we also ensure channel membership best-effort
    """

    async def export_entity(self):
        self.log_export(f"Экспорт реакции {self.entity.slack_id}")

        raw = self.entity.raw_data or {}

        post_id, channel_id = await self._resolve_target_post_and_channel()
        if not post_id:
            await self.set_status("failed", error="Target post_id not found for reaction")
            return

        user_id = await self._resolve_mm_user_id_for_reaction()
        if not user_id:
            await self.set_status("failed", error="Reacting user not resolved")
            return

        emoji_name = (raw.get("name") or raw.get("emoji") or "").strip()
        if not emoji_name:
            await self.set_status("failed", error="Emoji name missing")
            return
        # Build candidate names for standard emoji (strip tones, map aliases)
        candidates = self._emoji_candidates(emoji_name)
        # Use transliteration ONLY for custom emojis we created; keep standard names as-is
        # If it's a custom emoji, ensure the first candidate is the transliterated name
        if await self._is_custom_emoji(candidates[0]):
            candidates[0] = transliterate_cyrillic(candidates[0])

        # Best-effort membership: add user to channel to avoid AddReaction failure
        if channel_id:
            try:
                _ = await self.mm_api_post(
                    "/plugins/mm-importer/api/v1/channel/members",
                    {"channel_id": channel_id, "user_ids": [user_id]},
                )
            except Exception as e:  # noqa: BLE001
                backend_logger.debug(f"Ensure channel membership for reaction failed (non-fatal): {e}")

        # Timestamp to ms if present
        create_at = self._parse_ts_ms(raw.get("ts")) or 0

        last_err = None
        for name in candidates:
            payload = {
                "user_id": user_id,
                "post_id": post_id,
                "emoji_name": name,
                "create_at": create_at,
            }
            try:
                resp = await self.mm_api_post("/plugins/mm-importer/api/v1/reaction", payload)
                if resp.status_code in (200, 201):
                    await self.set_status("success")
                    return
                try:
                    data = resp.json()
                    err = data.get("error") or data
                except Exception:
                    err = resp.text
                # Treat duplicates as success: toned variants collapse to base in MM
                if resp.status_code in (200, 201):
                    await self.set_status("success")
                    return
                if resp.status_code == 409 or (
                    isinstance(err, str) and (
                        "already exists" in err.lower() or
                        "reaction exists" in err.lower() or
                        "duplicate" in err.lower()
                    )
                ):
                    await self.set_status("success")
                    return
                last_err = f"Plugin reaction failed: {resp.status_code} {err}"
                # If it's the not found emoji error, try next candidate
                if "We couldn’t find the emoji" in str(err) or "couldn't find the emoji" in str(err):
                    continue
                # Other errors: break
                break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                break
        # If we tried all candidates and the error is unknown emoji, mark as skipped
        if last_err and ("We couldn’t find the emoji" in last_err or "couldn't find the emoji" in last_err):
            await self.set_status("skipped", error=last_err)
        else:
            await self.set_status("failed", error=last_err or "Unknown error")

    async def _is_custom_emoji(self, name: Optional[str]) -> bool:
        if not name:
            return False
        async with SessionLocal() as session:
            row = await session.execute(
                select(Entity).where((Entity.entity_type == "custom_emoji") & (Entity.slack_id == name))
            )
            return row.scalar_one_or_none() is not None

    def _normalize_standard_emoji(self, name: str) -> str:
        # Strip Slack skin tone suffix (MM expects base name or handles tones via client)
        for suffix in ("::skin-tone-2", "::skin-tone-3", "::skin-tone-4", "::skin-tone-5", "::skin-tone-6", "::skin-tone-1"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        # Map Slack aliases to MM equivalents where needed
        alias_map = {
            "+1": "thumbs_up",
            "-1": "thumbs_down",
        }
        return alias_map.get(name, name)

    def _emoji_candidates(self, original: str) -> list[str]:
        base = self._normalize_standard_emoji(original)
        # Provide alternates for thumbs up/down across ecosystems
        alt_map = {
            "thumbs_up": ["thumbs_up", "thumbsup", "+1"],
            "thumbs_down": ["thumbs_down", "thumbsdown", "-1"],
        }
        if base in alt_map:
            return alt_map[base]
        # Default to single candidate
        return [base]

    async def _resolve_target_post_and_channel(self) -> tuple[Optional[str], Optional[str]]:
        """Find the MM post_id and channel_id for the message this reaction targets.
        Prefer the reacted_to relation to the message entity.
        """
        async with SessionLocal() as session:
            # Find message entity via reacted_to relation
            row = await session.execute(
                select(Entity)
                .join(EntityRelation, Entity.id == EntityRelation.to_entity_id)
                .where(
                    (EntityRelation.from_entity_id == self.entity.id)
                    & (EntityRelation.relation_type == "reacted_to")
                    & (Entity.entity_type == "message")
                )
            )
            msg_entity = row.scalar_one_or_none()
            if not msg_entity:
                # Fallback by raw_data.item.ts or raw_data.ts (sanitize) or slack_id prefix
                raw = self.entity.raw_data or {}
                ts = None
                item = raw.get("item") or {}
                if isinstance(item, dict):
                    ts = item.get("ts")
                if not ts:
                    ts = raw.get("ts")
                # Some older imports stored the whole slack_id in raw.ts; sanitize to prefix
                if isinstance(ts, str) and "_" in ts:
                    ts = ts.split("_", 1)[0]
                if not ts:
                    try:
                        ts = str(self.entity.slack_id).split("_", 1)[0]
                    except Exception:
                        ts = None
                if ts:
                    row2 = await session.execute(
                        select(Entity).where((Entity.entity_type == "message") & (Entity.slack_id == ts))
                    )
                    msg_entity = row2.scalar_one_or_none()
            if not msg_entity:
                return None, None

            post_id = getattr(msg_entity, "mattermost_id", None)
            if not (isinstance(post_id, str) and post_id):
                return None, None

            # Resolve channel for membership using posted_in relation
            ch_row = await session.execute(
                select(Entity)
                .join(EntityRelation, Entity.id == EntityRelation.to_entity_id)
                .where(
                    (EntityRelation.from_entity_id == msg_entity.id)
                    & (EntityRelation.relation_type == "posted_in")
                    & (Entity.entity_type == "channel")
                )
            )
            ch_entity = ch_row.scalar_one_or_none()
            channel_id = getattr(ch_entity, "mattermost_id", None) if ch_entity else None
            return post_id, (channel_id if isinstance(channel_id, str) and channel_id else None)

    async def _resolve_mm_user_id_for_reaction(self) -> Optional[str]:
        """Find the MM user id who reacted. Prefer reacted_by relation, fallback by raw user in reaction."""
        # Via reacted_by relation
        async with SessionLocal() as session:
            row = await session.execute(
                select(Entity)
                .join(EntityRelation, Entity.id == EntityRelation.from_entity_id)
                .where(
                    (EntityRelation.to_entity_id == self.entity.id)
                    & (EntityRelation.relation_type == "reacted_by")
                    & (Entity.entity_type == "user")
                )
            )
            user_entity = row.scalar_one_or_none()
            if user_entity:
                mmid = getattr(user_entity, "mattermost_id", None)
                if isinstance(mmid, str) and mmid:
                    return mmid

        # Fallback by raw_data.user
        raw = self.entity.raw_data or {}
        slack_uid = raw.get("user")
        if slack_uid:
            async with SessionLocal() as session:
                row2 = await session.execute(
                    select(Entity).where((Entity.entity_type == "user") & (Entity.slack_id == slack_uid))
                )
                u = row2.scalar_one_or_none()
                if u:
                    mmid2 = getattr(u, "mattermost_id", None)
                    if isinstance(mmid2, str) and mmid2:
                        return mmid2
        return None

    def _parse_ts_ms(self, ts_str: Optional[str]) -> Optional[int]:
        if not ts_str:
            return None
        try:
            val = float(ts_str)
            return int(math.floor(val * 1000.0))
        except Exception:  # noqa: BLE001
            return None

# channel.py
# Сущность канала Slack
from .base_mixin import BaseMapping
from app.models.entity import Entity
from app.models.base import SessionLocal
from app.logging_config import backend_logger
from sqlalchemy import select, cast, String
from app.models.entity_relation import EntityRelation


class Channel(BaseMapping):
    entity_type = "channel"
    # Можно добавить специфичные методы/валидацию, если нужно

    async def save_to_db(self):
        async with SessionLocal() as session:
            # Сначала ищем по slack_id
            query = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "channel")
                    & (Entity.slack_id == self.slack_id)
                )
            )
            existing = query.scalar_one_or_none()
            if existing:
                self.mattermost_id = existing.mattermost_id
                self.status = existing.status
                backend_logger.debug(
                    f"Channel mapping already exists by slack_id: {self.slack_id}, mattermost_id: {self.mattermost_id}, status: {self.status}"
                )
                return
            # Если не найдено — ищем по channel_name
            channel_name = (self.raw_data or {}).get("name")
            if channel_name:
                query = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "channel")
                        & (cast(Entity.raw_data["name"], String) == channel_name)
                    )
                )
                existing = query.scalar_one_or_none()
                if existing:
                    self.mattermost_id = existing.mattermost_id
                    self.status = existing.status
                    backend_logger.debug(
                        f"Channel mapping already exists by name: {channel_name}, mattermost_id: {self.mattermost_id}, status: {self.status}"
                    )
                    return
        entity = await super().save_to_db()
        if entity is not None:
            self.id = entity.id
            await self.create_member_relations()

    async def create_member_relations(self):
        async with SessionLocal() as session:
            members = (self.raw_data or {}).get("members") or []
            # Deduplicate Slack user IDs to avoid duplicate inserts in one batch
            try:
                members = list(dict.fromkeys(members))
            except Exception:
                members = list(set(members))
            relations = []
            for user_id in members:
                user_query = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "user") & (Entity.slack_id == user_id)
                    )
                )
                user_entity = user_query.scalar_one_or_none()
                if user_entity:
                    # Skip if relation already exists (idempotent across re-imports)
                    existing_rel = await session.execute(
                        select(EntityRelation).where(
                            (EntityRelation.from_entity_id == user_entity.id)
                            & (EntityRelation.to_entity_id == self.id)
                            & (EntityRelation.relation_type == "member_of")
                        )
                    )
                    if existing_rel.scalar_one_or_none():
                        continue
                    relation = EntityRelation(
                        from_entity_id=user_entity.id,
                        to_entity_id=self.id,
                        relation_type="member_of",
                        raw_data=None,
                    )
                    relations.append(relation)
            if relations:
                session.add_all(relations)
                await session.commit()
                backend_logger.debug(
                    f"Created member relations for channel {self.id} ({len(relations)})"
                )

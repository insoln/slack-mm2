# reaction.py
# Сущность реакции Slack
from .base_mixin import BaseMapping
from app.models.entity_relation import EntityRelation
from app.models.entity import Entity
from app.models.base import SessionLocal
from sqlalchemy import select

class Reaction(BaseMapping):
    entity_type = "reaction"
    # Можно добавить специфичные методы/валидацию, если нужно

    async def save_to_db(self):
        # Ensure raw_data.ts is a proper Slack ts (not the whole slack_id)
        if self.raw_data is not None and 'ts' not in self.raw_data and self.slack_id:
            try:
                self.raw_data['ts'] = str(self.slack_id).split('_')[0]
            except Exception:
                pass
        return await super().save_to_db()

    async def create_custom_emoji_relation(self, emoji_name):
        if not emoji_name:
            return
        async with SessionLocal() as session:
            # Найти Entity.id кастомного эмодзи по slack_id (emoji_name)
            query_emoji = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "custom_emoji")
                    & (Entity.slack_id == emoji_name)
                    & (
                        (Entity.job_id == getattr(self, "job_id", None))
                        | (Entity.job_id.is_(None))
                    )
                )
            )
            emoji_entity = query_emoji.scalar_one_or_none()
            if not emoji_entity:
                return
            # Найти Entity.id реакции по slack_id
            query_reaction = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "reaction")
                    & (Entity.slack_id == self.slack_id)
                    & (
                        (Entity.job_id == getattr(self, "job_id", None))
                        | (Entity.job_id.is_(None))
                    )
                )
            )
            reaction_entity = query_reaction.scalar_one_or_none()
            if not reaction_entity:
                return
            # Check if relation already exists
            existing_rel = await session.execute(
                select(EntityRelation).where(
                    (EntityRelation.from_entity_id == reaction_entity.id)
                    & (EntityRelation.to_entity_id == emoji_entity.id)
                    & (EntityRelation.relation_type == "custom_emoji_used")
                )
            )
            if existing_rel.scalar_one_or_none():
                return
            relation = EntityRelation(
                from_entity_id=reaction_entity.id,
                to_entity_id=emoji_entity.id,
                relation_type="custom_emoji_used",
                raw_data=None,
                job_id=getattr(self, "job_id", None),
            )
            session.add(relation)
            await session.commit()

    async def create_reacted_by_relation(self):
        user_id = (self.raw_data or {}).get("user")
        if not user_id:
            return
        async with SessionLocal() as session:
            query_user = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "user")
                    & (Entity.slack_id == user_id)
                    & (
                        (Entity.job_id == getattr(self, "job_id", None))
                        | (Entity.job_id.is_(None))
                    )
                )
            )
            user_entity = query_user.scalar_one_or_none()
            if not user_entity:
                return
            # Check if relation already exists
            existing_rel = await session.execute(
                select(EntityRelation).where(
                    (EntityRelation.from_entity_id == user_entity.id)
                    & (EntityRelation.to_entity_id == self.id)
                    & (EntityRelation.relation_type == "reacted_by")
                )
            )
            if existing_rel.scalar_one_or_none():
                return
            relation = EntityRelation(
                from_entity_id=user_entity.id,
                to_entity_id=self.id,
                relation_type="reacted_by",
                raw_data=None,
                job_id=getattr(self, "job_id", None),
            )
            session.add(relation)
            await session.commit()

    async def create_reacted_to_relation(self):
        raw = self.raw_data or {}
        item = raw.get("item")
        ts = None
        if item and item.get("type") == "message":
            ts = item.get("ts")
        # Fallback: use raw ts captured from the parent message
        if not ts:
            ts = raw.get("ts")
            if not ts and self.slack_id:
                try:
                    ts = str(self.slack_id).split('_')[0]
                except Exception:
                    ts = None
        if not ts:
            return
        async with SessionLocal() as session:
            query_msg = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "message")
                    & (Entity.slack_id == ts)
                    & (
                        (Entity.job_id == getattr(self, "job_id", None))
                        | (Entity.job_id.is_(None))
                    )
                )
            )
            msg_entity = query_msg.scalar_one_or_none()
            if not msg_entity:
                return
            # Check if relation already exists
            existing_rel = await session.execute(
                select(EntityRelation).where(
                    (EntityRelation.from_entity_id == self.id)
                    & (EntityRelation.to_entity_id == msg_entity.id)
                    & (EntityRelation.relation_type == "reacted_to")
                )
            )
            if existing_rel.scalar_one_or_none():
                return
            relation = EntityRelation(
                from_entity_id=self.id,
                to_entity_id=msg_entity.id,
                relation_type="reacted_to",
                raw_data=None,
                job_id=getattr(self, "job_id", None),
            )
            session.add(relation)
            await session.commit()
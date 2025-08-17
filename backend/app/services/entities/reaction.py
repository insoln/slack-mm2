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
        if self.raw_data is not None and 'ts' not in self.raw_data:
            self.raw_data['ts'] = self.slack_id
        return await super().save_to_db()

    async def create_custom_emoji_relation(self, emoji_name):
        if not emoji_name:
            return
        async with SessionLocal() as session:
            # Найти Entity.id кастомного эмодзи по slack_id (emoji_name)
            query_emoji = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "custom_emoji") &
                    (Entity.slack_id == emoji_name)
                )
            )
            emoji_entity = query_emoji.scalar_one_or_none()
            if not emoji_entity:
                return
            # Найти Entity.id реакции по slack_id
            query_reaction = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "reaction") &
                    (Entity.slack_id == self.slack_id)
                )
            )
            reaction_entity = query_reaction.scalar_one_or_none()
            if not reaction_entity:
                return
            relation = EntityRelation(
                from_entity_id=reaction_entity.id,
                to_entity_id=emoji_entity.id,
                relation_type="custom_emoji_used",
                raw_data=None
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
                    (Entity.entity_type == "user") &
                    (Entity.slack_id == user_id)
                )
            )
            user_entity = query_user.scalar_one_or_none()
            if not user_entity:
                return
            relation = EntityRelation(
                from_entity_id=user_entity.id,
                to_entity_id=self.id,
                relation_type="reacted_by",
                raw_data=None
            )
            session.add(relation)
            await session.commit()

    async def create_reacted_to_relation(self):
        item = (self.raw_data or {}).get("item")
        if not item or item.get("type") != "message":
            return
        channel_id = item.get("channel")
        ts = item.get("ts")
        if not channel_id or not ts:
            return
        async with SessionLocal() as session:
            query_msg = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "message") &
                    (Entity.slack_id == ts)
                )
            )
            msg_entity = query_msg.scalar_one_or_none()
            if not msg_entity:
                return
            relation = EntityRelation(
                from_entity_id=self.id,
                to_entity_id=msg_entity.id,
                relation_type="reacted_to",
                raw_data=None
            )
            session.add(relation)
            await session.commit() 
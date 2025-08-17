# attachment.py
# Сущность аттачмента Slack
from .base_mixin import BaseMapping
from app.models.entity_relation import EntityRelation
from app.models.entity import Entity
from app.models.base import SessionLocal
from sqlalchemy import select

class Attachment(BaseMapping):
    entity_type = "attachment"
    # Можно добавить специфичные методы/валидацию, если нужно

    async def create_attached_to_relation(self, message_ts):
        if not message_ts or not hasattr(self, 'id'):
            return
        async with SessionLocal() as session:
            # Найти Entity.id сообщения по ts
            query_msg = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "message") &
                    (Entity.slack_id == message_ts)
                )
            )
            msg_entity = query_msg.scalar_one_or_none()
            if not msg_entity:
                return
            relation = EntityRelation(
                from_entity_id=self.id,
                to_entity_id=msg_entity.id,
                relation_type="attached_to",
                raw_data=None
            )
            session.add(relation)
            await session.commit() 
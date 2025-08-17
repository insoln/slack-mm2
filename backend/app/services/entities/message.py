# message.py
# Сущность сообщения Slack
from .base_mixin import BaseMapping
from app.models.entity_relation import EntityRelation
from app.models.entity import Entity
from app.models.base import SessionLocal
from sqlalchemy import select
from app.logging_config import backend_logger

class Message(BaseMapping):
    entity_type = "message"
    # Можно добавить специфичные методы/валидацию, если нужно

    async def save_to_db(self, channel_id):
        if self.raw_data is not None and 'channel_id' not in self.raw_data:
            self.raw_data['channel_id'] = channel_id
        return await super().save_to_db()

    async def create_posted_in_relation(self, channel_id):
        async with SessionLocal() as session:
            query = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "channel") &
                    (Entity.slack_id == channel_id)
                )
            )
            channel_entity = query.scalar_one_or_none()
            if channel_entity:
                relation = EntityRelation(
                    from_entity_id=self.id,
                    to_entity_id=channel_entity.id,
                    relation_type="posted_in",
                    raw_data=None
                )
                session.add(relation)
                await session.commit()

    async def create_posted_by_relation(self):
        user_id = (self.raw_data or {}).get("user")
        if not user_id:
            user_id = (self.raw_data or {}).get("bot_id")
        if not user_id:
            return
        async with SessionLocal() as session:
            query = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "user") &
                    (Entity.slack_id == user_id)
                )
            )
            user_entity = query.scalar_one_or_none()
            # Если user не найден, а user_id похож на bot_id, создать user-entity для бота
            if not user_entity and user_id and (user_id.startswith('B') or user_id == 'USLACKBOT'):
                backend_logger.debug(f"Создание user-entity для бота: {user_id}")
                from app.services.entities.user import User
                username = self.raw_data.get("username") if self.raw_data else None
                bot_user = User(slack_id=user_id, raw_data={"is_bot": True, "first_name": username}, status="pending", auto_save=False)
                user_entity = await bot_user.save_to_db()
                if user_entity:
                    backend_logger.debug(f"user-entity для бота создан: id={user_entity.id}, slack_id={user_id}")
                else:
                    backend_logger.error(f"user-entity для бота НЕ создан: slack_id={user_id}")
            if user_entity:
                try:
                    backend_logger.debug(f"Пробую создать связь posted_by: from_entity_id={user_entity.id}, to_entity_id={self.id}")
                    relation = EntityRelation(
                        from_entity_id=user_entity.id,
                            to_entity_id=self.id,
                        relation_type="posted_by",
                        raw_data=None
                    )
                    session.add(relation)
                    await session.commit()
                    backend_logger.debug(f"Связь posted_by создана: from_entity_id={user_entity.id}, to_entity_id={self.id}")
                except Exception as e:
                    backend_logger.error(f"Ошибка при создании связи posted_by: from_entity_id={user_entity.id}, to_entity_id={self.id}, ошибка: {e}")

    async def create_thread_relation(self):
        thread_ts = (self.raw_data or {}).get("thread_ts")
        ts = (self.raw_data or {}).get("ts")
        if not thread_ts or thread_ts == ts:
            return
        async with SessionLocal() as session:
            # Найти Entity.id родительского сообщения по thread_ts
            query_parent = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "message") &
                    (Entity.slack_id == thread_ts)
                )
            )
            parent_entity = query_parent.scalar_one_or_none()
            if not parent_entity:
                return
            relation = EntityRelation(
                from_entity_id=self.id,
                to_entity_id=parent_entity.id,
                relation_type="thread_reply",
                raw_data=None
            )
            session.add(relation)
            await session.commit() 
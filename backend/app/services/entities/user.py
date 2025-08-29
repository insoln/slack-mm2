# user.py
# Сущность пользователя Slack
from .base_mixin import BaseMapping
from app.models.entity import Entity
from app.models.base import SessionLocal
from app.logging_config import backend_logger
from sqlalchemy import select, cast, String


class User(BaseMapping):
    entity_type = "user"
    # Можно добавить специфичные методы/валидацию, если нужно

    @classmethod
    def from_entity(cls, entity):
        obj = cls(
            slack_id=entity.slack_id,
            mattermost_id=entity.mattermost_id,
            raw_data=entity.raw_data,
            status=entity.status,
            auto_save=False,
        )
        obj.id = entity.id
        return obj

    async def save_to_db(self):
        async with SessionLocal() as session:
            # Сначала ищем по slack_id
            query = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "user") & (Entity.slack_id == self.slack_id)
                )
            )
            existing = query.scalar_one_or_none()
            if existing:
                self.mattermost_id = existing.mattermost_id
                self.status = existing.status
                backend_logger.debug(
                    f"User mapping already exists by slack_id: {self.slack_id}, mattermost_id: {self.mattermost_id}, status: {self.status}"
                )
                return existing
            # Если не найдено — ищем по username
            username = (self.raw_data or {}).get("username")
            if username:
                query = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == "user")
                        & (cast(Entity.raw_data["username"], String) == username)
                    )
                )
                existing = query.scalar_one_or_none()
                if existing:
                    self.mattermost_id = existing.mattermost_id
                    self.status = existing.status
                    backend_logger.debug(
                        f"User mapping already exists by username: {username}, mattermost_id: {self.mattermost_id}, status: {self.status}"
                    )
                    return existing
        return await super().save_to_db()

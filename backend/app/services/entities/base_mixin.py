# base_mixin.py
# Базовые миксины для парсинга сущностей

import asyncio
from app.models.entity import Entity
from app.models.base import SessionLocal
from app.models.status_enum import MappingStatus
from sqlalchemy.exc import IntegrityError
from app.logging_config import backend_logger
from sqlalchemy import select


class BaseMapping:
    entity_type = None  # Должен быть определён в наследнике

    def __init__(
        self,
        slack_id,
        mattermost_id=None,
        raw_data=None,
        status="pending",
        auto_save=True,
        job_id=None,
    ):
        self.slack_id = str(slack_id)  # Приведение к строке для совместимости с БД
        self.mattermost_id = mattermost_id
        self.raw_data = raw_data
        self.status = status
        self.job_id = job_id
        backend_logger.debug(
            f"Инициализация маппинга: {self.entity_type}, slack_id={self.slack_id}, mattermost_id={self.mattermost_id}, status={self.status}"
        )
        if auto_save:
            asyncio.create_task(self.save_to_db())

    async def save_to_db(self):
        async with SessionLocal() as session:
            # Проверка на существование
            if self.job_id is None:
                query = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == self.entity_type)
                        & (Entity.slack_id == self.slack_id)
                        & (Entity.job_id.is_(None))
                    )
                )
            else:
                query = await session.execute(
                    select(Entity).where(
                        (Entity.entity_type == self.entity_type)
                        & (Entity.slack_id == self.slack_id)
                        & (Entity.job_id == self.job_id)
                    )
                )
            existing = query.scalar_one_or_none()
            if existing:
                self.id = existing.id
                backend_logger.debug(f"{self.entity_type} already exists: slack_id={self.slack_id}")
                return existing
            entity = Entity(
                entity_type=self.entity_type,
                slack_id=self.slack_id,
                mattermost_id=self.mattermost_id,
                raw_data=self.raw_data,
                job_id=self.job_id,
                status=self.status,
            )
            session.add(entity)
            try:
                await session.commit()
                backend_logger.debug(f"Сохранен маппинг: {self.entity_type}, slack_id={self.slack_id}, mattermost_id={self.mattermost_id}, status={self.status}")
                self.id = entity.id
                return entity
            except IntegrityError as e:
                await session.rollback()
                # Повторно ищем запись: возможно, она уже появилась из другого потока
                if self.job_id is None:
                    query = await session.execute(
                        select(Entity).where(
                            (Entity.entity_type == self.entity_type)
                            & (Entity.slack_id == self.slack_id)
                            & (Entity.job_id.is_(None))
                        )
                    )
                else:
                    query = await session.execute(
                        select(Entity).where(
                            (Entity.entity_type == self.entity_type)
                            & (Entity.slack_id == self.slack_id)
                            & (Entity.job_id == self.job_id)
                        )
                    )
                existing = query.scalar_one_or_none()
                if existing:
                    backend_logger.error(f"IntegrityError: {self.entity_type} already exists after IntegrityError: slack_id={self.slack_id}, ошибка: {e}")
                    return existing
                backend_logger.error(f"Ошибка при сохранении маппинга: {self.entity_type}, slack_id={self.slack_id}, mattermost_id={self.mattermost_id}, status={self.status}, ошибка: {e}")
                return None

    def to_dict(self):
        return self.__dict__

    def to_entity(self):
        return Entity(
            entity_type=self.entity_type,
            slack_id=self.slack_id,
            mattermost_id=self.mattermost_id,
            raw_data=self.raw_data,
            status=self.status,
        )

    async def set_status(self, new_status, error=None):
        self.status = new_status
        if error:
            self.error_message = str(error)
        
        async with SessionLocal() as session:
            # Обновляем существующую запись
            from sqlalchemy import update
            cond = (
                (Entity.entity_type == self.entity_type)
                & (Entity.slack_id == self.slack_id)
                & (Entity.job_id.is_(None) if self.job_id is None else (Entity.job_id == self.job_id))
            )
            stmt = update(Entity).where(cond).values(
                status=MappingStatus(new_status),
                error_message=str(error) if error else None
            )
            result = await session.execute(stmt)
            await session.commit()
            
            if result.rowcount > 0:
                backend_logger.debug(f"Обновлен статус {self.entity_type} {self.slack_id}: {new_status}")
            else:
                backend_logger.error(f"Не найдена запись для обновления статуса: {self.entity_type} {self.slack_id}")

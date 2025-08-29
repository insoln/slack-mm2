import logging
from abc import ABC, abstractmethod
from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.status_enum import MappingStatus
from app.models.entity import Entity
from sqlalchemy import update

class ExporterBase(ABC):
    def __init__(self, entity, mm_client=None):
        self.entity = entity
        self.mm_client = mm_client  # Клиент Mattermost API, если нужен

    @abstractmethod
    async def export_entity(self):
        """Экспортировать сущность в Mattermost. Реализуется в наследниках."""
        pass

    async def set_status(self, status, error=None):
        self.entity.status = status
        if error:
            self.entity.error_message = str(error)
        
        # Обновляем запись в БД используя модель Entity
        async with SessionLocal() as session:
            update_values = {
                "status": MappingStatus(status),
                "error_message": str(error) if error else None
            }
            
            # Если есть mattermost_id, добавляем его в обновление
            if hasattr(self.entity, 'mattermost_id') and self.entity.mattermost_id:
                update_values["mattermost_id"] = self.entity.mattermost_id
            
            where_cond = (
                (Entity.entity_type == self.entity.entity_type)
                & (Entity.slack_id == self.entity.slack_id)
            )
            # If this entity has job_id, include it to avoid cross-job collisions
            job_id = getattr(self.entity, "job_id", None)
            if job_id is not None:
                try:
                    where_cond = where_cond & (Entity.job_id == job_id)
                except Exception:
                    pass
            stmt = update(Entity).where(where_cond).values(**update_values)
            
            result = await session.execute(stmt)
            await session.commit()
            
            if result.rowcount > 0:
                backend_logger.debug(f"Set status {status} for {self.entity.entity_type} {self.entity.slack_id}")
            else:
                backend_logger.error(f"Failed to update status for {self.entity.entity_type} {self.entity.slack_id}")

# Пример миксина для логирования
class LoggingMixin:
    def log_export(self, msg):
        backend_logger.debug(f"[EXPORT] {msg}")

# Пример миксина для работы с Mattermost API
class MMApiMixin:
    def send_to_mm(self, payload):
        # Здесь будет логика отправки в Mattermost
        pass 
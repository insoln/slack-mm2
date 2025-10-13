# base_mixin.py
# Базовые миксины для парсинга сущностей

import asyncio
from app.models.entity import Entity
from app.models.base import SessionLocal
from app.models.status_enum import MappingStatus
from sqlalchemy.exc import IntegrityError
from app.logging_config import backend_logger
from sqlalchemy import select
from typing import List, Optional, Dict, Set, Tuple


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
                backend_logger.debug(
                    f"{self.entity_type} already exists: slack_id={self.slack_id}"
                )
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
                backend_logger.debug(
                    f"Сохранен маппинг: {self.entity_type}, slack_id={self.slack_id}, mattermost_id={self.mattermost_id}, status={self.status}"
                )
                self.id = entity.id
                return entity
            except IntegrityError as e:
                await session.rollback()
                # Возможен гонок: другая корутина вставила запись между select и commit
                # Делаем до 2 быстрых повторных проверок с короткой задержкой
                for attempt in range(2):
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
                        backend_logger.info(
                            f"[DUPLICATE] {self.entity_type} slack_id={self.slack_id} reuse id={existing.id} (attempt={attempt})"
                        )
                        return existing
                    # micro sleep only if not last attempt
                    if attempt == 0:
                        try:
                            import asyncio as _a

                            await _a.sleep(0)
                        except Exception:  # pragma: no cover
                            pass
                # Если после повторных проверок не нашли — это реальная ошибка
                backend_logger.error(
                    f"[DBERR] save_failed entity={self.entity_type} slack_id={self.slack_id} job_id={self.job_id} status={self.status} err={e}"
                )
                return None

    @staticmethod
    async def batch_save_to_db(mappings: List["BaseMapping"]) -> Dict[str, int]:
        """
        Batch save multiple mappings to database in a single transaction.
        
        Returns:
            Dict with counts of {'saved': int, 'existing': int, 'failed': int}
        """
        if not mappings:
            return {'saved': 0, 'existing': 0, 'failed': 0}
            
        # Group by entity_type for better logging and potential optimization
        grouped_mappings: Dict[str, List["BaseMapping"]] = {}
        for mapping in mappings:
            entity_type = mapping.entity_type
            if entity_type not in grouped_mappings:
                grouped_mappings[entity_type] = []
            grouped_mappings[entity_type].append(mapping)
        
        saved_count = 0
        existing_count = 0 
        failed_count = 0
        
        async with SessionLocal() as session:
            try:
                # Check for existing entities to avoid duplicates
                existing_entities: Set[Tuple[str, str, Optional[int]]] = set()
                
                for entity_type, type_mappings in grouped_mappings.items():
                    # Build query to check for existing entities of this type
                    slack_ids = [m.slack_id for m in type_mappings]
                    job_ids = list({m.job_id for m in type_mappings})
                    
                    # Query for existing entities
                    if len(job_ids) == 1 and job_ids[0] is None:
                        # All mappings have job_id=None
                        query = await session.execute(
                            select(Entity).where(
                                (Entity.entity_type == entity_type)
                                & (Entity.slack_id.in_(slack_ids))
                                & (Entity.job_id.is_(None))
                            )
                        )
                    elif None not in job_ids:
                        # All mappings have non-None job_ids
                        query = await session.execute(
                            select(Entity).where(
                                (Entity.entity_type == entity_type)
                                & (Entity.slack_id.in_(slack_ids))
                                & (Entity.job_id.in_(job_ids))
                            )
                        )
                    else:
                        # Mixed job_ids (some None, some not) - need separate queries
                        for mapping in type_mappings:
                            if mapping.job_id is None:
                                query = await session.execute(
                                    select(Entity).where(
                                        (Entity.entity_type == mapping.entity_type)
                                        & (Entity.slack_id == mapping.slack_id)
                                        & (Entity.job_id.is_(None))
                                    )
                                )
                            else:
                                query = await session.execute(
                                    select(Entity).where(
                                        (Entity.entity_type == mapping.entity_type)
                                        & (Entity.slack_id == mapping.slack_id)
                                        & (Entity.job_id == mapping.job_id)
                                    )
                                )
                            existing = query.scalar_one_or_none()
                            if existing:
                                existing_entities.add((mapping.entity_type, mapping.slack_id, mapping.job_id))
                                mapping.id = existing.id
                        continue
                    
                    # Process results for simple cases (all same job_id pattern)
                    existing_results = query.scalars().all()
                    for existing in existing_results:
                        existing_entities.add((existing.entity_type, existing.slack_id, existing.job_id))
                        # Update mapping.id for the corresponding mapping
                        for mapping in type_mappings:
                            if (mapping.slack_id == existing.slack_id and 
                                mapping.job_id == existing.job_id):
                                mapping.id = existing.id
                                break

                # Prepare entities to insert
                new_entities = []
                for mapping in mappings:
                    if (mapping.entity_type, mapping.slack_id, mapping.job_id) in existing_entities:
                        existing_count += 1
                        backend_logger.debug(
                            f"Batch: {mapping.entity_type} already exists: slack_id={mapping.slack_id}"
                        )
                    else:
                        entity = Entity(
                            entity_type=mapping.entity_type,
                            slack_id=mapping.slack_id,
                            mattermost_id=mapping.mattermost_id,
                            raw_data=mapping.raw_data,
                            job_id=mapping.job_id,
                            status=mapping.status,
                        )
                        new_entities.append((entity, mapping))
                
                # Bulk insert new entities
                if new_entities:
                    session.add_all([entity for entity, _ in new_entities])
                    await session.commit()
                    
                    # Update mapping ids after commit
                    for entity, mapping in new_entities:
                        mapping.id = entity.id
                        saved_count += 1
                    
                    backend_logger.debug(
                        f"Batch saved {saved_count} new mappings, found {existing_count} existing"
                    )
                
                await session.commit()
                
            except Exception as e:
                await session.rollback()
                failed_count = len(mappings) - existing_count - saved_count
                backend_logger.error(f"[BATCH_ERR] Batch save failed: {e}")
                # Fallback to individual saves for error resilience
                for mapping in mappings:
                    if not hasattr(mapping, 'id') or mapping.id is None:
                        try:
                            result = await mapping.save_to_db()
                            if result is not None:
                                saved_count += 1
                                failed_count -= 1
                        except Exception:
                            backend_logger.error(f"[FALLBACK_ERR] Individual save failed for {mapping.entity_type} {mapping.slack_id}")
        
        return {'saved': saved_count, 'existing': existing_count, 'failed': failed_count}

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
                & (
                    Entity.job_id.is_(None)
                    if self.job_id is None
                    else (Entity.job_id == self.job_id)
                )
            )
            stmt = (
                update(Entity)
                .where(cond)
                .values(
                    status=MappingStatus(new_status),
                    error_message=str(error) if error else None,
                )
            )
            result = await session.execute(stmt)
            await session.commit()

            if result.rowcount > 0:
                backend_logger.debug(
                    f"Обновлен статус {self.entity_type} {self.slack_id}: {new_status}"
                )
            else:
                backend_logger.error(
                    f"Не найдена запись для обновления статуса: {self.entity_type} {self.slack_id}"
                )

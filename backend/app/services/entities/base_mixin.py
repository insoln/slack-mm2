# base_mixin.py
# Базовые миксины для парсинга сущностей

import asyncio
from app.models.entity import Entity
from app.models.base import SessionLocal
from app.models.status_enum import MappingStatus
from sqlalchemy.exc import IntegrityError
from app.logging_config import backend_logger
from sqlalchemy import select
from sqlalchemy import func as _sa_func
from typing import List, Optional, Dict, Set, Tuple
from sqlalchemy.dialects.postgresql import insert as pg_insert


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
            # Global uniqueness: ignore job_id when checking for existing entity.
            # job_id now only serves as provenance / grouping metadata and MUST NOT
            # create duplicate logical entities differing only by job_id.
            query = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == self.entity_type)
                    & (Entity.slack_id == self.slack_id)
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
                job_id=self.job_id,  # retained for lineage only
                status=self.status,
            )
            # Manual PK assignment for SQLite in-memory where BIGINT autoincrement can misbehave.
            try:
                bind = session.get_bind()
                if bind and bind.dialect.name == "sqlite":
                    curmax = await session.execute(select(_sa_func.max(Entity.id)))
                    next_id = (curmax.scalar() or 0) + 1
                    # Use setattr to avoid static type complaints; SQLAlchemy will treat as PK value.
                    setattr(entity, "id", next_id)
            except Exception:  # pragma: no cover
                pass
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
                    query = await session.execute(
                        select(Entity).where(
                            (Entity.entity_type == self.entity_type)
                            & (Entity.slack_id == self.slack_id)
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
            return {"saved": 0, "existing": 0, "failed": 0}

        # Group by entity_type for better logging and potential optimization
        grouped_mappings: Dict[str, List["BaseMapping"]] = {}
        for mapping in mappings:
            entity_type = mapping.entity_type or "__undefined__"
            if entity_type not in grouped_mappings:
                grouped_mappings[entity_type] = []
            grouped_mappings[entity_type].append(mapping)

        saved_count = 0
        existing_count = 0
        failed_count = 0

        async with SessionLocal() as session:
            try:
                bind = session.get_bind()
                is_sqlite = bool(bind and bind.dialect.name == "sqlite")
                # Fast path with ON CONFLICT for Postgres
                if not is_sqlite:
                    rows = []
                    for m in mappings:
                        status_value = m.status
                        if not isinstance(status_value, MappingStatus):
                            try:
                                status_value = MappingStatus(status_value)
                            except ValueError:
                                backend_logger.warning(
                                    "[BATCH_WARN] Unknown status %s for %s/%s, defaulting to pending",
                                    status_value,
                                    m.entity_type,
                                    m.slack_id,
                                )
                                status_value = MappingStatus.pending

                        rows.append(
                            {
                                "entity_type": m.entity_type,
                                "slack_id": m.slack_id,
                                "mattermost_id": m.mattermost_id,
                                "raw_data": m.raw_data,
                                "job_id": m.job_id,
                                "status": status_value,
                            }
                        )

                    if rows:
                        stmt = (
                            pg_insert(Entity.__table__)
                            .values(rows)
                            .on_conflict_do_nothing(
                                index_elements=["entity_type", "slack_id"]
                            )
                            .returning(Entity.id, Entity.entity_type, Entity.slack_id)
                        )
                        res = await session.execute(stmt)
                        inserted = res.fetchall()
                        inserted_map: Dict[tuple[Optional[str], str], int] = {}
                        for rid, etype, sid in inserted:
                            inserted_map[
                                (etype if etype is not None else "__undefined__", sid)
                            ] = rid
                        saved_count = len(inserted_map)

                        for m in mappings:
                            etype = (
                                m.entity_type
                                if m.entity_type is not None
                                else "__undefined__"
                            )
                            key = (etype, m.slack_id)
                            if key in inserted_map:
                                m.id = inserted_map[key]
                            else:
                                existing_count += 1

                        await session.commit()
                        backend_logger.debug(
                            f"Batch saved {saved_count} new mappings, existing={existing_count}"
                        )
                else:
                    # SQLite fallback: previous approach (no ON CONFLICT support here)
                    existing_entities: Set[Tuple[str, str]] = set()
                    for entity_type, type_mappings in grouped_mappings.items():
                        slack_ids = [m.slack_id for m in type_mappings]
                        query = await session.execute(
                            select(Entity).where(
                                (Entity.entity_type == entity_type)
                                & (Entity.slack_id.in_(slack_ids))
                            )
                        )
                        existing_results = query.scalars().all()
                        for existing in existing_results:
                            existing_entities.add(
                                (str(existing.entity_type), str(existing.slack_id))
                            )
                            for mapping in type_mappings:
                                if mapping.slack_id == existing.slack_id:
                                    mapping.id = existing.id
                                    break
                    new_entities = []
                    for m in mappings:
                        if (m.entity_type, m.slack_id) in existing_entities:
                            existing_count += 1
                        else:
                            entity = Entity(
                                entity_type=m.entity_type,
                                slack_id=m.slack_id,
                                mattermost_id=m.mattermost_id,
                                raw_data=m.raw_data,
                                job_id=m.job_id,
                                status=m.status,
                            )
                            new_entities.append((entity, m))
                    if new_entities:
                        curmax = await session.execute(select(_sa_func.max(Entity.id)))
                        base = curmax.scalar() or 0
                        for off, (entity, _m) in enumerate(new_entities, start=1):
                            setattr(entity, "id", base + off)
                        session.add_all([e for e, _ in new_entities])
                        await session.commit()
                        for e, m in new_entities:
                            m.id = e.id
                            saved_count += 1
            except Exception as e:  # noqa: BLE001
                await session.rollback()
                failed_count = len(mappings) - existing_count - saved_count
                backend_logger.error(f"[BATCH_ERR] Batch save failed: {e}")
                # Fallback to individual saves
                for m in mappings:
                    if not hasattr(m, "id") or m.id is None:
                        try:
                            result = await m.save_to_db()
                            if result is not None:
                                saved_count += 1
                                failed_count -= 1
                        except Exception:
                            backend_logger.error(
                                f"[FALLBACK_ERR] Individual save failed for {m.entity_type} {m.slack_id}"
                            )

        return {
            "saved": saved_count,
            "existing": existing_count,
            "failed": failed_count,
        }

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

            # Global uniqueness: locate by (entity_type, slack_id) only
            cond = (Entity.entity_type == self.entity_type) & (
                Entity.slack_id == self.slack_id
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

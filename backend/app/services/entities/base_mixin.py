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
from typing import List, Optional, Dict, Tuple, cast
from sqlalchemy.dialects.postgresql import insert as pg_insert


class BaseMapping:
    entity_type = None  # Должен быть определён в наследнике
    _TAKEOVER_STATUSES = {
        MappingStatus.failed,
        MappingStatus.skipped,
    }

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
                if self._takeover_existing_entity(existing):
                    await session.commit()
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
    def is_newly_created(previous_id: Optional[int], result: Optional[Entity]) -> bool:
        """Determine if save_to_db resulted in a newly created entity."""
        if previous_id is not None:
            return False
        if result is None:
            return False
        return getattr(result, "id", None) is not None

    @staticmethod
    async def save_individually_with_stats(
        mappings: List["BaseMapping"],
    ) -> Dict[str, int]:
        """Persist mappings sequentially, capturing created/existing/failed counts."""
        created = 0
        existing = 0
        failed = 0
        for mapping in mappings:
            prev_id = getattr(mapping, "id", None)
            try:
                result = await mapping.save_to_db()
            except Exception:
                failed += 1
                backend_logger.error(
                    f"[FALLBACK_ERR] Individual save failed for {mapping.entity_type} {mapping.slack_id}"
                )
                continue
            if BaseMapping.is_newly_created(prev_id, result):
                created += 1
            else:
                existing += 1
        return {"saved": created, "existing": existing, "failed": failed}

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
            if mapping.entity_type is None:
                raise ValueError(
                    "BaseMapping.batch_save_to_db requires each mapping to define entity_type"
                )
            entity_type = cast(str, mapping.entity_type)
            if entity_type not in grouped_mappings:
                grouped_mappings[entity_type] = []
            grouped_mappings[entity_type].append(mapping)

        saved_count = 0
        existing_count = 0
        failed_count = 0
        needs_fallback = False

        async with SessionLocal() as session:
            try:
                bind = session.get_bind()
                is_sqlite = bool(bind and bind.dialect.name == "sqlite")
                existing_entities: Dict[Tuple[str, str], Entity] = {}
                for entity_type, type_mappings in grouped_mappings.items():
                    slack_ids = list({m.slack_id for m in type_mappings})
                    if not slack_ids:
                        continue
                    query = await session.execute(
                        select(Entity).where(
                            (Entity.entity_type == entity_type)
                            & (Entity.slack_id.in_(slack_ids))
                        )
                    )
                    for existing in query.scalars().all():
                        existing_entities[(entity_type, existing.slack_id)] = existing

                takeover_count = 0

                pending_mappings: List["BaseMapping"] = []
                for mapping in mappings:
                    key = (mapping.entity_type, mapping.slack_id)
                    existing = existing_entities.get(key)
                    if existing:
                        if BaseMapping._apply_takeover(existing, mapping):
                            takeover_count += 1
                            continue
                        mapping.id = existing.id
                        existing_count += 1
                        continue
                    pending_mappings.append(mapping)
                saved_count = takeover_count
                # Fast path with ON CONFLICT for Postgres
                if not is_sqlite:
                    rows = []
                    for m in pending_mappings:
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
                        inserted_map: Dict[tuple[str, str], int] = {}
                        for rid, etype, sid in inserted:
                            if etype is None:
                                raise ValueError(
                                    "Entity row returned without entity_type; possible database constraint or ORM mapping issue"
                                )
                            inserted_map[(etype, sid)] = rid
                        saved_count += len(inserted_map)

                        for m in pending_mappings:
                            key = (m.entity_type, m.slack_id)
                            if key in inserted_map:
                                m.id = inserted_map[key]
                        await session.commit()
                    elif takeover_count:
                        await session.commit()
                    backend_logger.debug(
                        f"Batch saved {saved_count} new mappings (takeover={takeover_count}), existing={existing_count}"
                    )
                else:
                    # SQLite fallback: previous approach (no ON CONFLICT support here)
                    new_entities = []
                    for m in pending_mappings:
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
                    if takeover_count and not new_entities:
                        await session.commit()
            except Exception as e:  # noqa: BLE001
                await session.rollback()
                backend_logger.error(f"[BATCH_ERR] Batch save failed: {e}")
                needs_fallback = True
                saved_count = 0
                existing_count = 0
                failed_count = 0

        if needs_fallback:
            fallback = await BaseMapping.save_individually_with_stats(mappings)
            saved_count += fallback["saved"]
            existing_count += fallback["existing"]
            failed_count += fallback["failed"]

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

    @staticmethod
    def _coerce_status(value) -> Optional[MappingStatus]:
        if isinstance(value, MappingStatus):
            return value
        try:
            return MappingStatus(value)
        except Exception:  # pragma: no cover
            return None

    @classmethod
    def _should_takeover(cls, status) -> bool:
        status_enum = cls._coerce_status(status)
        return bool(status_enum and status_enum in cls._TAKEOVER_STATUSES)

    def _takeover_existing_entity(self, existing: Entity) -> bool:
        if not self._should_takeover(existing.status):
            return False
        pending = MappingStatus.pending
        existing.status = pending
        existing.error_message = None
        if self.job_id is not None:
            existing.job_id = self.job_id
        if self.raw_data is not None:
            existing.raw_data = self.raw_data
        existing.mattermost_id = self.mattermost_id
        self.status = pending
        self.id = existing.id
        if self.job_id is None:
            self.job_id = existing.job_id
        backend_logger.debug(
            f"Takeover existing mapping {self.entity_type} slack_id={self.slack_id} -> job_id={existing.job_id}"
        )
        return True

    @classmethod
    def _apply_takeover(cls, existing: Entity, mapping: "BaseMapping") -> bool:
        if not cls._should_takeover(existing.status):
            return False
        pending = MappingStatus.pending
        existing.status = pending
        existing.error_message = None
        if mapping.job_id is not None:
            existing.job_id = mapping.job_id
        if mapping.raw_data is not None:
            existing.raw_data = mapping.raw_data
        existing.mattermost_id = mapping.mattermost_id
        mapping.status = pending
        mapping.id = existing.id
        if mapping.job_id is None:
            mapping.job_id = existing.job_id
        backend_logger.debug(
            f"Batch takeover {mapping.entity_type} slack_id={mapping.slack_id} -> job_id={existing.job_id}"
        )
        return True

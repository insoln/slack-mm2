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
                "error_message": str(error) if error else None,
            }

            # Если есть mattermost_id, добавляем его в обновление
            if hasattr(self.entity, "mattermost_id") and self.entity.mattermost_id:
                update_values["mattermost_id"] = self.entity.mattermost_id

            where_cond = (Entity.entity_type == self.entity.entity_type) & (
                Entity.slack_id == self.entity.slack_id
            )  # Global uniqueness now; job_id ignored for matching
            stmt = update(Entity).where(where_cond).values(**update_values)

            result = await session.execute(stmt)
            await session.commit()

            if result.rowcount > 0:
                backend_logger.debug(
                    f"Set status {status} for {self.entity.entity_type} {self.entity.slack_id}"
                )
            else:
                backend_logger.error(
                    f"Failed to update status for {self.entity.entity_type} {self.entity.slack_id}"
                )

    async def guard_dependencies(self) -> tuple[bool, str | None]:
        """Проверить зависимости сущности через entity_relations.
        Возвращает (skip, reason). Если skip=True — экспорт должен быть пропущен.
        Правила жёсткие: любая необходимая зависимость должна иметь статус success.
        Отсутствующая relation = неготовая зависимость => skip.
        """
        from app.models.entity_relation import EntityRelation
        from app.models.entity import Entity as _Entity
        from app.models.status_enum import MappingStatus as _MS
        from sqlalchemy import select as _select

        et = self.entity.entity_type
        # Dependency map describes HARD requirements that must already be exported.
        # We are inverting previous logic: now a message that references attachments
        # should wait for those attachments (so we can include their file_ids).
        # Attachments no longer require the parent message to be success; they only
        # need a channel (resolved later when uploading) and existence of the relation
        # is enough for contextual linking.
        #
        # For messages we still require channel + author; additionally, if there are
        # outgoing attached_to relations (i.e., attachments pointing to this message),
        # we impose a conditional check later to ensure those attachments are success
        # before exporting the message so file_ids can be gathered.
        dep_map: dict[str, list[tuple[str, str, str]]] = {
            "message": [
                ("posted_in", "out", "channel"),
                ("posted_by", "in", "user"),
                # attachments handled conditionally after base loop
            ],
            "reaction": [
                ("reacted_to", "out", "message"),
                ("reacted_by", "in", "user"),
            ],
            # attachment: no hard dependency on message success anymore
        }
        deps = dep_map.get(et)
        if not deps:
            return (False, None)
        ent_id = getattr(self.entity, "id", None)
        if ent_id is None:
            async with SessionLocal() as _s:
                q = await _s.execute(
                    _select(_Entity).where(
                        (_Entity.entity_type == et)
                        & (_Entity.slack_id == self.entity.slack_id)
                    )
                )
                row = q.scalars().first()
                ent_id = getattr(row, "id", None)
        if ent_id is None:
            return (False, None)
        async with SessionLocal() as _s:
            for rel_type, direction, dep_type in deps:
                cond_rel = EntityRelation.relation_type == rel_type
                if direction == "out":
                    cond_rel = cond_rel & (EntityRelation.from_entity_id == ent_id)
                else:
                    cond_rel = cond_rel & (EntityRelation.to_entity_id == ent_id)
                rel_rows = await _s.execute(_select(EntityRelation).where(cond_rel))
                rels = rel_rows.scalars().all()
                if not rels:
                    return (True, f"missing relation {rel_type}")
                dep_ids = [
                    (r.to_entity_id if direction == "out" else r.from_entity_id)
                    for r in rels
                ]
                if not dep_ids:
                    return (True, f"empty dependency set {rel_type}")
                dep_rows = await _s.execute(
                    _select(_Entity).where(
                        (_Entity.id.in_(dep_ids)) & (_Entity.entity_type == dep_type)
                    )
                )
                dep_entities = dep_rows.scalars().all()
                if not dep_entities:
                    return (True, f"missing dependency entities for {rel_type}")
                for de in dep_entities:
                    if getattr(de, "status", None) != _MS.success:
                        return (
                            True,
                            f"dependency {dep_type}:{de.slack_id} status={getattr(de, 'status', None)}",
                        )

            # Extra conditional dependency logic for message replies: if a message has
            # an outgoing thread_reply relation, its parent message MUST be success.
            # Additionally (new logic): if the message has incoming 'attached_to' relations
            # (i.e., attachments pointing to this message), ensure those attachment
            # entities are success so we can include their file_ids now. If any are still
            # pending/failed/skipped -> skip the message for now; orchestrator loop will retry.
            if et == "message":
                cond_rel = (EntityRelation.relation_type == "thread_reply") & (
                    EntityRelation.from_entity_id == ent_id
                )
                rel_rows = await _s.execute(_select(EntityRelation).where(cond_rel))
                reply_rels = rel_rows.scalars().all()
                if reply_rels:
                    parent_ids = [r.to_entity_id for r in reply_rels]
                    if not parent_ids:
                        return (True, "empty dependency set thread_reply")
                    dep_rows = await _s.execute(
                        _select(_Entity).where(
                            (_Entity.id.in_(parent_ids))
                            & (_Entity.entity_type == "message")
                        )
                    )
                    parents = dep_rows.scalars().all()
                    if not parents:
                        return (True, "missing thread parent message")
                    for pm in parents:
                        if getattr(pm, "status", None) != _MS.success:
                            return (
                                True,
                                f"dependency message:{pm.slack_id} status={getattr(pm, 'status', None)}",
                            )

                # Conditional attachment dependency inversion:
                # Find attachments (from_entity attachment -> to_entity this message) relation 'attached_to'
                cond_att = (EntityRelation.relation_type == "attached_to") & (
                    EntityRelation.to_entity_id == ent_id
                )
                att_rows = await _s.execute(_select(EntityRelation).where(cond_att))
                att_rels = att_rows.scalars().all()
                if att_rels:
                    att_ids = [r.from_entity_id for r in att_rels]
                    if att_ids:
                        att_entities_rows = await _s.execute(
                            _select(_Entity).where(
                                (_Entity.id.in_(att_ids))
                                & (_Entity.entity_type == "attachment")
                            )
                        )
                        atts = att_entities_rows.scalars().all()
                        if not atts:
                            return (True, "missing attachment entities")
                        for att in atts:
                            st = getattr(att, "status", None)
                            if st != _MS.success:
                                return (
                                    True,
                                    f"dependency attachment:{att.slack_id} status={st}",
                                )
        return (False, None)


# Пример миксина для логирования
class LoggingMixin:
    def log_export(self, msg):
        backend_logger.debug(f"[EXPORT] {msg}")


# Пример миксина для работы с Mattermost API
class MMApiMixin:
    def send_to_mm(self, payload):
        # Здесь будет логика отправки в Mattermost
        pass

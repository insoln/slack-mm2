# attachment.py
# Сущность аттачмента Slack
from .base_mixin import BaseMapping
from app.models.entity_relation import EntityRelation
from app.models.entity import Entity
from app.models.base import SessionLocal
from sqlalchemy import select, func as sa_func


class Attachment(BaseMapping):
    entity_type = "attachment"
    # Можно добавить специфичные методы/валидацию, если нужно

    async def create_attached_to_relation(self, message_ts):
        if not message_ts or not hasattr(self, "id"):
            return
        async with SessionLocal() as session:
            # Найти Entity.id сообщения по ts независимо от job_id и предпочесть
            # 1) то же задание, 2) уже экспортированное сообщение (есть mattermost_id).
            query_msg = await session.execute(
                select(Entity).where(
                    (Entity.entity_type == "message") & (Entity.slack_id == message_ts)
                )
            )
            candidates = query_msg.scalars().all()
            if not candidates:
                return

            current_job = getattr(self, "job_id", None)

            def sort_key(entity):
                same_job = int(current_job is not None and entity.job_id == current_job)
                has_mm = int(bool(getattr(entity, "mattermost_id", None)))
                return (-same_job, -has_mm, entity.id or 0)

            msg_entity = sorted(candidates, key=sort_key)[0]
            # Skip if relation already exists
            existing_rel = await session.execute(
                select(EntityRelation).where(
                    (EntityRelation.from_entity_id == self.id)
                    & (EntityRelation.to_entity_id == msg_entity.id)
                    & (EntityRelation.relation_type == "attached_to")
                )
            )
            if not existing_rel.scalar_one_or_none():
                relation = EntityRelation(
                    from_entity_id=self.id,
                    to_entity_id=msg_entity.id,
                    relation_type="attached_to",
                    raw_data=None,
                    job_id=getattr(self, "job_id", None),
                )
                try:
                    bind = session.get_bind()
                    if bind and bind.dialect.name == "sqlite":
                        current_max = await session.execute(
                            select(sa_func.max(EntityRelation.id))
                        )
                        next_id = (current_max.scalar() or 0) + 1
                        setattr(relation, "id", next_id)
                except Exception:  # pragma: no cover
                    pass
                session.add(relation)
                await session.commit()

import asyncio
import os
import httpx
from app.models.base import SessionLocal
from sqlalchemy import select
from .user_exporter import UserExporter
from .custom_emoji_exporter import CustomEmojiExporter
from .attachment_exporter import AttachmentExporter
from .message_exporter import MessageExporter
from .reaction_exporter import ReactionExporter
from .channel_exporter import ChannelExporter
from app.logging_config import backend_logger
from app.models.entity import Entity
from app.services.entities.user import User
from app.services.entities.custom_emoji import CustomEmoji
from app.services.entities.attachment import Attachment
# TODO: добавить остальные экспортеры (AttachmentExporter, MessageExporter, ReactionExporter)

EXPORT_ORDER = [
    ("user", UserExporter),
    ("custom_emoji", CustomEmojiExporter),
    ("channel", ChannelExporter),
    ("attachment", AttachmentExporter),
    ("message", MessageExporter),
    ("reaction", ReactionExporter),
]

async def get_mm_user_id():
    """Получить ID пользователя-владельца токена из Mattermost"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{os.environ['MM_URL']}/api/v4/users/me",
                headers={"Authorization": f"Bearer {os.environ['MM_TOKEN']}"},
                timeout=10
            )
            if resp.status_code == 200:
                user_data = resp.json()
                user_id = user_data.get("id")
                backend_logger.info(f"Получен ID пользователя Mattermost: {user_id}")
                return user_id
            else:
                backend_logger.error(f"Ошибка получения ID пользователя: {resp.status_code}")
                return None
    except Exception as e:
        backend_logger.error(f"Ошибка при получении ID пользователя: {e}")
        return None

async def get_entities_to_export(entity_type):
    async with SessionLocal() as session:
        query = await session.execute(
            select(Entity).where(
                (Entity.entity_type == entity_type)
                & (Entity.status.in_(["pending", "skipped", "failed"]))
            )
        )
        entities = query.scalars().all()
        if entity_type == "user":
            return [User.from_entity(e) for e in entities]
        elif entity_type == "custom_emoji":
            return [CustomEmoji.from_entity(e) for e in entities]
        elif entity_type == "attachment":
            # For attachments we can use BaseMapping as-is (no special from_entity)
            return entities
        elif entity_type == "message":
            # Sort messages so that roots go before replies, and by timestamp ascending
            try:
                from app.models.entity_relation import EntityRelation
                ids = [e.id for e in entities]
                reply_set = set()
                if ids:
                    rel_rows = await session.execute(
                        select(EntityRelation.from_entity_id).where(
                            (EntityRelation.relation_type == "thread_reply")
                            & (EntityRelation.from_entity_id.in_(ids))
                        )
                    )
                    reply_set = {row[0] for row in rel_rows.all()}

                def ts_key(ent):
                    try:
                        return float(ent.slack_id)
                    except Exception:
                        return float("inf")

                entities_sorted = sorted(
                    entities,
                    key=lambda ent: (0 if ent.id not in reply_set else 1, ts_key(ent)),
                )
                return entities_sorted
            except Exception as e:
                backend_logger.error(
                    f"Не удалось отсортировать сообщения для тредов: {e}"
                )
                return entities
        elif entity_type == "reaction":
            # Ensure reactions are processed after their target messages; simple ts sort as tie-breaker
            def ts_key(ent):
                try:
                    # slack_id format might be "<ts>_<name>_<user>"; take ts part
                    return float(str(ent.slack_id).split("_")[0])
                except Exception:
                    return float("inf")
            return sorted(entities, key=ts_key)
        return entities

async def export_worker(queue, mm_user_id):
    while True:
        item = await queue.get()
        if item is None:
            # Mark sentinel as done to keep queue counters consistent
            queue.task_done()
            break
        entity, exporter_cls = item
        try:
            # Передаем mm_user_id только для CustomEmojiExporter
            if exporter_cls == CustomEmojiExporter:
                exporter = exporter_cls(entity, mm_user_id=mm_user_id)
            else:
                exporter = exporter_cls(entity)
            await exporter.export_entity()
        except Exception as e:
            backend_logger.error(f"Ошибка экспорта {entity.entity_type} {entity.slack_id}: {e}")
        queue.task_done()

async def orchestrate_mm_export():
    # Получаем ID пользователя-владельца токена
    mm_user_id = await get_mm_user_id()
    if not mm_user_id:
        backend_logger.error("Не удалось получить ID пользователя Mattermost, прерываю экспорт")
        return
    
    workers_count = int(os.getenv("EXPORT_WORKERS", 5))
    queue = asyncio.Queue()
    
    for entity_type, exporter_cls in EXPORT_ORDER:
        backend_logger.info(f"Экспорт сущностей типа {entity_type}")
        entities = await get_entities_to_export(entity_type)
        for entity in entities:
            backend_logger.debug(f"[EXPORT] enqueue {entity_type} {entity.slack_id}")
            await queue.put((entity, exporter_cls))
        # Use a single worker for messages to preserve order (root before replies)
        workers_for_type = 1 if entity_type == "message" else workers_count
        backend_logger.debug(f"[EXPORT] starting {workers_for_type} workers for {entity_type}")
        workers = [asyncio.create_task(export_worker(queue, mm_user_id)) for _ in range(workers_for_type)]
        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)
        backend_logger.info(f"Экспорт {entity_type} завершён")
 
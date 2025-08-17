import os
import glob
import json
from app.services.entities.message import Message
from app.logging_config import backend_logger
from app.models.base import SessionLocal
from sqlalchemy import select

async def parse_channel_messages(export_dir, folder_channel_map):
    message_entities = []
    message_channel_pairs = []
    for folder, channel in folder_channel_map.items():
        backend_logger.debug(f"Обработка папки: {folder}, канал: {(channel.get('name') if channel else None) or (channel.get('slack_id') if channel else None)}")
        if not channel:
            backend_logger.debug(f"Пропуск папки {folder}: канал не найден")
            continue
        channel_id = channel["id"]
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            backend_logger.debug(f"Пропуск: {folder_path} не является директорией")
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            backend_logger.debug(f"Чтение файла сообщений: {msg_file}")
            with open(msg_file, encoding="utf-8") as f:
                try:
                    messages = json.load(f)
                except Exception as e:
                    backend_logger.error(f"Ошибка чтения {msg_file}: {e}")
                    continue
            backend_logger.debug(f"В файле {msg_file} найдено сообщений: {len(messages)}")
            for msg in messages:
                slack_id = msg.get("ts")
                if not slack_id:
                    backend_logger.debug(f"Пропуск сообщения без ts: {msg}")
                    continue
                message_entity = Message(
                    slack_id=slack_id,
                    mattermost_id=None,
                    raw_data=msg,
                    status="pending",
                    auto_save=False
                )
                message_entities.append(message_entity)
                message_channel_pairs.append((message_entity, channel_id))
                backend_logger.debug(f"Добавлен маппинг сообщения: slack_id={slack_id}, канал={channel.get('name') or channel.get('slack_id')} ({channel_id})")
    for message_entity, channel_id in message_channel_pairs:
        entity = await message_entity.save_to_db(channel_id)
    backend_logger.debug(f"Сохранено сообщений: {len(message_entities)}. Начинаю создание связей posted_in...")
    for message_entity, channel_id in message_channel_pairs:
        if getattr(message_entity, 'id', None) is not None:
            await message_entity.create_posted_in_relation(channel_id)
            await message_entity.create_posted_by_relation()
            await message_entity.create_thread_relation()
    backend_logger.debug(f"Все связи posted_in сохранены. Всего: {len(message_channel_pairs)}")
    backend_logger.info(f"Импортировано сообщений: {len(message_entities)} и связей posted_in: {len(message_channel_pairs)}")
    return message_entities 
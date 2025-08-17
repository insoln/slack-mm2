import os
import json
import tempfile
import shutil
from app.logging_config import backend_logger
from .users_import import parse_users
from .channels_import import parse_channels_and_chats, find_channel_for_folder
from .messages_import import parse_channel_messages
from .attachments_import import parse_attachments_from_messages
from .reactions_import import parse_reactions_from_messages
from app.services.export.orchestrator import orchestrate_mm_export
from app.services.entities.custom_emoji import get_slack_emoji_list

async def orchestrate_slack_import(zip_path):
    extract_dir = tempfile.mkdtemp(prefix="slack-extract-")
    backend_logger.info(f"Распаковываю архив {zip_path} в {extract_dir}")
    from app.services.backup.zip_utils import extract_zip
    await extract_zip(zip_path, extract_dir)
    
    # Получаем список эмодзи из Slack API один раз
    emoji_list = await get_slack_emoji_list()
    
    backend_logger.info(f"Архив распакован. Начинаю парсинг пользователей...")
    users = await parse_users(extract_dir)
    backend_logger.info(f"Импорт пользователей завершён. Всего обработано: {len(users)}")
    channels = await parse_channels_and_chats(extract_dir)
    backend_logger.info(f"Импорт каналов завершён. Всего обработано: {len(channels)}")
    folder_channel_map = find_channel_for_folder(extract_dir, [])
    backend_logger.debug(f"Сопоставление папок и каналов/групп/чатов: {len(folder_channel_map)}")
    message_entities = await parse_channel_messages(extract_dir, folder_channel_map)
    await parse_reactions_from_messages(message_entities, emoji_list)
    await parse_attachments_from_messages(extract_dir, message_entities)
    try:
        shutil.rmtree(extract_dir)
        backend_logger.debug(f"Временная директория {extract_dir} удалена")
    except Exception as e:
        backend_logger.error(f"Ошибка при удалении временной директории {extract_dir}: {e}")
    # Запуск экспорта после завершения импорта
    await orchestrate_mm_export()
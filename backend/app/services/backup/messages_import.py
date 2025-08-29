import os
import glob
import ijson
from app.services.entities.message import Message
from app.logging_config import backend_logger
from typing import Awaitable, Callable, Optional


async def parse_channel_messages(
    export_dir,
    folder_channel_map,
    batch_size: int = 1000,
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
    file_progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
):
    """Stream-parse messages by JSON file and persist incrementally.
    Returns count, not a big list, to keep memory low.
    """
    saved_count = 0
    for folder, channel in folder_channel_map.items():
        backend_logger.debug(
            f"Обработка папки: {folder}, канал: {(channel.get('name') if channel else None) or (channel.get('slack_id') if channel else None)}"
        )
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
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    # Slack daily files are JSON arrays; stream items
                    for msg in ijson.items(f, "item"):
                        try:
                            slack_id = msg.get("ts")
                            if not slack_id:
                                continue
                            message_entity = Message(
                                slack_id=slack_id,
                                mattermost_id=None,
                                raw_data=msg,
                                status="pending",
                                auto_save=False,
                                job_id=job_id,
                            )
                            # Save and link immediately to avoid memory growth
                            await message_entity.save_to_db(channel_id)
                            if getattr(message_entity, "id", None) is not None:
                                await message_entity.create_posted_in_relation(
                                    channel_id
                                )
                                await message_entity.create_posted_by_relation()
                                await message_entity.create_thread_relation()
                            saved_count += 1
                            if saved_count % batch_size == 0:
                                backend_logger.debug(
                                    f"Сохранено сообщений: {saved_count}…"
                                )
                                if progress:
                                    await progress(batch_size)
                        except Exception as e:
                            backend_logger.error(
                                f"Ошибка при сохранении сообщения из {msg_file}: {e}"
                            )
                # file processed successfully
                if file_progress:
                    try:
                        await file_progress(1)
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file}: {e}")
                continue
    backend_logger.info(f"Импортировано сообщений: {saved_count}")
    if progress and saved_count % batch_size:
        # flush remaining
        await progress(saved_count % batch_size)
    return saved_count

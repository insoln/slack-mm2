import os
import json
from typing import Optional
from app.services.entities.channel import Channel
from app.logging_config import backend_logger


async def parse_channels_and_chats(extract_dir, job_id: Optional[int] = None):
    files = ["channels.json", "dms.json", "mpims.json", "groups.json"]
    channel_objs = []
    for fname in files:
        path = os.path.join(extract_dir, fname)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            backend_logger.info(f"Найдено {len(data)} объектов в {fname}")
            for channel_json in data:
                slack_id = channel_json.get("id")
                mattermost_id = None
                channel = Channel(
                    slack_id=slack_id,
                    mattermost_id=mattermost_id,
                    raw_data=channel_json,
                    auto_save=False,
                    job_id=job_id,
                )
                channel_objs.append(channel)
        else:
            backend_logger.info(f"{fname} не найден в {extract_dir}")
    for channel in channel_objs:
        await channel.save_to_db()
    return channel_objs


def find_channel_for_folder(export_dir, _):
    folders = [
        f for f in os.listdir(export_dir) if os.path.isdir(os.path.join(export_dir, f))
    ]
    all_channels = []
    for fname in ["channels.json", "groups.json", "dms.json", "mpims.json"]:
        path = os.path.join(export_dir, fname)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                try:
                    all_channels.extend(json.load(f))
                except Exception as e:
                    backend_logger.error(f"Ошибка чтения {fname}: {e}")
    channels_by_id = {c["id"]: c for c in all_channels if "id" in c}
    channels_by_name = {c["name"]: c for c in all_channels if "name" in c}
    result = {}
    for folder in folders:
        channel = channels_by_id.get(folder) or channels_by_name.get(folder)
        result[folder] = channel
    return result

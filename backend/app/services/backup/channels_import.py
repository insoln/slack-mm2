import os
import json
from typing import Optional
from app.services.entities.channel import Channel
from app.logging_config import backend_logger


async def parse_channels_and_chats(extract_dir, job_id: Optional[int] = None):
    """Parse channel-like entities (public channels, private groups, DMs, MPIMs).

    Legacy progress tracker removed; we simply save each channel to DB.
    Returns list of Channel wrappers (may be empty).
    """
    files = ["channels.json", "dms.json", "mpims.json", "groups.json"]
    channel_objs = []
    for fname in files:
        path = os.path.join(extract_dir, fname)
        if not os.path.exists(path):
            backend_logger.info(f"{fname} не найден в {extract_dir}")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):  # pragma: no cover
                backend_logger.warning(f"{fname} имеет неожиданный формат, пропускаю")
                continue
            backend_logger.info(f"Найдено {len(data)} объектов в {fname}")
            for channel_json in data:
                slack_id = channel_json.get("id")
                channel = Channel(
                    slack_id=slack_id,
                    mattermost_id=None,
                    raw_data=channel_json,
                    auto_save=False,
                    job_id=job_id,
                )
                channel_objs.append(channel)
        except Exception as e:  # pragma: no cover
            backend_logger.error(f"Ошибка чтения {fname}: {e}")
    # Persist
    for channel in channel_objs:
        try:
            await channel.save_to_db()
        except Exception:  # pragma: no cover
            pass
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

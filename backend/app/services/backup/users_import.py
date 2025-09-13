import os
import json
from typing import Optional
from app.services.entities.user import User
from app.logging_config import backend_logger
from app.services.backup.progress_tracker import make_tracker


async def parse_users(extract_dir, job_id: Optional[int] = None):
    users_path = os.path.join(extract_dir, "users.json")
    if not os.path.exists(users_path):
        backend_logger.error(f"users.json не найден в {extract_dir}")
        return []
    with open(users_path, encoding="utf-8") as f:
        users_data = json.load(f)
    backend_logger.info(f"Найдено пользователей: {len(users_data)}")
    tracker = make_tracker(job_id, "user")
    user_objs = []
    for user_json in users_data:
        slack_id = user_json.get("id")
        mattermost_id = None
        user = User(
            slack_id=slack_id,
            mattermost_id=mattermost_id,
            raw_data=user_json,
            auto_save=False,
            job_id=job_id,
        )
        user_objs.append(user)
        # parsed increment on discovery
        await tracker.incr_parsed(1)
    for u in user_objs:
        ent = await u.save_to_db()
        if ent is not None:
            await tracker.incr_processed(1)
    # final flush
    await tracker.flush()
    return user_objs

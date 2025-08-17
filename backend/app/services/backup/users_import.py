import os
import json
from app.services.entities.user import User
from app.logging_config import backend_logger

async def parse_users(extract_dir):
    users_path = os.path.join(extract_dir, "users.json")
    if not os.path.exists(users_path):
        backend_logger.error(f"users.json не найден в {extract_dir}")
        return []
    with open(users_path, encoding="utf-8") as f:
        users_data = json.load(f)
    backend_logger.info(f"Найдено пользователей: {len(users_data)}")
    user_objs = []
    for user_json in users_data:
        slack_id = user_json.get("id")
        mattermost_id = None
        user = User(slack_id=slack_id, mattermost_id=mattermost_id, raw_data=user_json, auto_save=False)
        user_objs.append(user)
    for user in user_objs:
        await user.save_to_db()
    return user_objs 
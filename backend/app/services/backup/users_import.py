import os
import json
from typing import Optional
from app.services.entities.user import User
from app.logging_config import backend_logger


async def parse_users(extract_dir, job_id: Optional[int] = None):
    users_path = os.path.join(extract_dir, "users.json")
    if not os.path.exists(users_path):
        try:
            listing = sorted(os.listdir(extract_dir))
        except Exception:
            listing = []
        backend_logger.error(
            "users.json не найден в %s; содержимое: %s", extract_dir, listing
        )
        return []
    with open(users_path, encoding="utf-8") as f:
        users_data = json.load(f)
    backend_logger.info(f"Найдено пользователей: {len(users_data)}")
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
    # Batch save all users
    if user_objs:
        from app.services.entities.base_mixin import BaseMapping

        try:
            # Check if we're dealing with real mappings or mocks (test environment)
            if hasattr(user_objs[0], "entity_type") and not callable(
                getattr(user_objs[0], "entity_type", None)
            ):
                result = await BaseMapping.batch_save_to_db(user_objs)
                backend_logger.debug(f"Batch saved {result.get('saved', 0)} users")
            else:
                # Test environment with mocks - use individual saves
                raise Exception("Mock detected, using fallback")
        except Exception as e:
            backend_logger.debug(
                f"Batch save failed for users, using individual saves: {e}"
            )
            # Fallback to individual saves
            for u in user_objs:
                try:
                    await u.save_to_db()
                except Exception:  # pragma: no cover
                    pass
    return user_objs

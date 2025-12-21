"""
Integration test for Slack bot to Mattermost Bot Account export.
This test verifies that:
1. Slack users with is_bot=true are created as Mattermost Bot Accounts
2. Regular Slack users are created as regular Mattermost users
3. Bot mapping is stored correctly in the database
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.backup.users_import import parse_users
from app.services.export.user_exporter import UserExporter
from app.services.entities.user import User


@pytest.mark.asyncio
async def test_bot_user_export_integration(tmp_path):
    """Integration test: bot users are exported as Bot Accounts"""
    # Create test data
    users_json = tmp_path / "users.json"
    users_data = [
        {
            "id": "UBOT001",
            "name": "test_bot",
            "is_bot": True,
            "profile": {
                "real_name": "Test Bot",
                "first_name": "Test",
                "last_name": "Bot",
                "title": "A test bot",
            },
        },
        {
            "id": "UUSER001",
            "name": "john_doe",
            "is_bot": False,
            "profile": {
                "email": "john@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "real_name": "John Doe",
            },
        },
    ]

    import json

    with open(users_json, "w") as f:
        json.dump(users_data, f)

    # Import users
    result = await parse_users(str(tmp_path))

    assert result["discovered"] == 2
    user_objs = result["objects"]

    # Find bot and regular user
    bot_user = next(u for u in user_objs if u.raw_data.get("is_bot"))
    regular_user = next(u for u in user_objs if not u.raw_data.get("is_bot"))

    # Setup mock for bot export
    bot_exporter = UserExporter(bot_user)
    bot_exporter.mm_api_get = AsyncMock()
    bot_exporter.mm_api_post = AsyncMock()
    bot_exporter._upload_avatar = AsyncMock()
    bot_exporter.set_status = AsyncMock()

    # Mock: no existing bot
    bot_exporter.mm_api_get.return_value.status_code = 200
    bot_exporter.mm_api_get.return_value.json = lambda: []

    # Mock: bot creation succeeds
    mock_bot_resp = MagicMock()
    mock_bot_resp.status_code = 201
    mock_bot_resp.json = lambda: {
        "user_id": "bot-user-id-001",
        "username": "test_bot",
        "display_name": "Test Bot",
    }
    bot_exporter.mm_api_post.return_value = mock_bot_resp

    # Export bot
    await bot_exporter.export_entity()

    # Verify bot was created via /api/v4/bots
    bot_exporter.mm_api_post.assert_called_once()
    call_args = bot_exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/bots"

    # Verify bot payload
    bot_payload = call_args[0][1]
    assert bot_payload["username"] == "test_bot"
    assert bot_payload["display_name"] == "Test Bot"
    assert bot_payload["description"] == "A test bot"

    # Verify bot user_id stored
    assert bot_user.mattermost_id == "bot-user-id-001"
    bot_exporter.set_status.assert_called_with("success")

    # Setup mock for regular user export
    user_exporter = UserExporter(regular_user)
    user_exporter.mm_api_get = AsyncMock()
    user_exporter.mm_api_post = AsyncMock()
    user_exporter._ensure_user_in_team = AsyncMock()
    user_exporter._upload_avatar = AsyncMock()
    user_exporter.set_status = AsyncMock()

    # Mock: no existing user by email/username
    def mm_api_get_side_effect(path):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        return mock_resp

    user_exporter.mm_api_get.side_effect = mm_api_get_side_effect

    # Mock: user creation succeeds
    mock_user_resp = MagicMock()
    mock_user_resp.status_code = 201
    mock_user_resp.json = lambda: {"id": "user-id-001"}
    user_exporter.mm_api_post.return_value = mock_user_resp

    # Export regular user
    await user_exporter.export_entity()

    # Verify user was created via /api/v4/users (NOT /api/v4/bots)
    user_exporter.mm_api_post.assert_called_once()
    call_args = user_exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/users"

    # Verify user payload
    user_payload = call_args[0][1]
    assert user_payload["username"] == "john_doe"
    assert user_payload["email"] == "john@example.com"
    assert user_payload["first_name"] == "John"
    assert user_payload["last_name"] == "Doe"

    # Verify user_id stored
    assert regular_user.mattermost_id == "user-id-001"
    user_exporter.set_status.assert_called_with("success")


@pytest.mark.asyncio
async def test_bot_user_from_issue_example():
    """Test using the exact bot data from the GitHub issue"""
    bot_data = {
        "id": "U03AQJVH2HM",
        "tz": "America/Los_Angeles",
        "name": "reminder_bot",
        "is_bot": True,
        "deleted": False,
        "profile": {
            "team": "T012WR22D9R",
            "phone": "",
            "skype": "",
            "title": "",
            "bot_id": "B03AYHB4D7Y",
            "fields": {},
            "last_name": "Bot",
            "real_name": "Reminder Bot",
            "api_app_id": "A83H5QZPT",
            "first_name": "Reminder",
            "avatar_hash": "b83a71292d38",
            "status_text": "",
            "display_name": "",
            "status_emoji": "",
            "always_active": False,
            "is_custom_image": True,
            "status_expiration": 0,
            "real_name_normalized": "Reminder Bot",
            "status_text_canonical": "",
            "display_name_normalized": "",
            "status_emoji_display_info": [],
        },
        "team_id": "T012WR22D9R",
        "updated": 1649761675,
        "is_admin": False,
        "is_owner": False,
        "tz_label": "Pacific Standard Time",
        "real_name": "Reminder Bot",
        "tz_offset": -28800,
        "is_app_user": False,
        "is_restricted": False,
        "is_primary_owner": False,
        "is_email_confirmed": False,
        "is_ultra_restricted": False,
        "who_can_share_contact_card": "EVERYONE",
    }

    # Create user object
    bot_user = User(
        slack_id=bot_data["id"],
        mattermost_id=None,
        raw_data=bot_data,
        auto_save=False,
    )

    # Verify bot detection
    exporter = UserExporter(bot_user)
    assert exporter._is_slack_bot() is True

    # Verify bot payload generation
    payload = exporter._build_bot_payload()
    assert payload["username"] == "reminder_bot"
    assert payload["display_name"] == "Reminder Bot"
    assert payload["description"] == ""

    # Mock export
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()
    exporter.set_status = AsyncMock()

    # No existing bot
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: []

    # Bot creation succeeds
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json = lambda: {
        "user_id": "mm-bot-id",
        "username": "reminder_bot",
        "display_name": "Reminder Bot",
    }
    exporter.mm_api_post.return_value = mock_resp

    # Export
    await exporter.export_entity()

    # Verify correct API endpoint used
    exporter.mm_api_post.assert_called_once()
    assert exporter.mm_api_post.call_args[0][0] == "/api/v4/bots"

    # Verify bot was created successfully
    assert bot_user.mattermost_id == "mm-bot-id"
    exporter.set_status.assert_called_with("success")

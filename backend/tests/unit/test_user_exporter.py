import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.export.user_exporter import UserExporter


class DummyEntity:
    def __init__(self, slack_id, raw_data):
        self.slack_id = slack_id
        self.raw_data = raw_data
        self.mattermost_id = None


@pytest.mark.asyncio
async def test_user_exporter_reuse_by_email(monkeypatch):
    entity = DummyEntity(
        "UADMIN", {"name": "admin", "profile": {"email": "admin@example.com"}}
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._ensure_user_in_team = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # email lookup returns existing user
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: {"id": "existing-id"}

    await exporter.export_entity()
    # Explicitly await any pending coroutine on set_status to avoid AsyncMock un-awaited RuntimeWarning
    if exporter.set_status.await_count == 0:
        await exporter.set_status("noop")  # pragma: no cover

    assert entity.mattermost_id == "existing-id"
    exporter.set_status.assert_awaited_with("success")
    exporter.mm_api_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_exporter_reuse_by_username(monkeypatch):
    entity = DummyEntity("UUSER", {"name": "john", "profile": {}})

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._ensure_user_in_team = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # First call (email lookup) -> simulate not found (404)
    # Second call (username lookup) -> 200
    def mm_api_get_side_effect(path):
        mock_resp = MagicMock()
        if path.startswith("/api/v4/users/email/"):
            mock_resp.status_code = 404
        else:
            mock_resp.status_code = 200
            mock_resp.json = lambda: {"id": "by-username-id"}
        return mock_resp

    exporter.mm_api_get.side_effect = mm_api_get_side_effect

    await exporter.export_entity()

    assert entity.mattermost_id == "by-username-id"
    exporter.set_status.assert_awaited_with("success")
    exporter.mm_api_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_exporter_create_new(monkeypatch):
    entity = DummyEntity("UNEW", {"name": "newuser", "profile": {}})

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._ensure_user_in_team = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # email lookup 404, username lookup 404
    def mm_api_get_side_effect(path):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        return mock_resp

    exporter.mm_api_get.side_effect = mm_api_get_side_effect

    # POST creates user
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_post_resp.json = lambda: {"id": "created-id"}
    exporter.mm_api_post.return_value = mock_post_resp

    await exporter.export_entity()

    assert entity.mattermost_id == "created-id"
    exporter.set_status.assert_awaited_with("success")
    exporter.mm_api_post.assert_awaited()


@pytest.mark.asyncio
async def test_bot_exporter_create_new_bot():
    """Test that Slack bots are created as Mattermost Bot Accounts."""
    entity = DummyEntity(
        "U03AQJVH2HM",
        {
            "name": "reminder_bot",
            "is_bot": True,
            "profile": {
                "real_name": "Reminder Bot",
                "first_name": "Reminder",
                "last_name": "Bot",
            },
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # No existing bot found
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: []

    # Bot creation returns bot with user_id
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_post_resp.json = lambda: {"user_id": "bot-user-id", "username": "reminder_bot"}
    exporter.mm_api_post.return_value = mock_post_resp

    await exporter.export_entity()

    # Verify bot was created via /api/v4/bots endpoint
    exporter.mm_api_post.assert_awaited_once()
    call_args = exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/bots"

    # Verify payload contains bot-specific fields
    payload = call_args[0][1]
    assert payload["username"] == "reminder_bot"
    assert payload["display_name"] == "Reminder Bot"

    # Verify user_id was stored
    assert entity.mattermost_id == "bot-user-id"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_bot_exporter_reuse_existing_bot():
    """Test that existing Mattermost bots are reused."""
    entity = DummyEntity(
        "UBOT123",
        {
            "name": "test_bot",
            "is_bot": True,
            "profile": {"real_name": "Test Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # Existing bot found
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: [
        {"username": "test_bot", "user_id": "existing-bot-user-id"}
    ]

    await exporter.export_entity()

    # Verify no bot creation attempted
    exporter.mm_api_post.assert_not_awaited()

    # Verify existing bot user_id was stored
    assert entity.mattermost_id == "existing-bot-user-id"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_is_slack_bot_detection():
    """Test _is_slack_bot correctly identifies bots."""
    # Bot user
    bot_entity = DummyEntity("UBOT", {"is_bot": True})
    bot_exporter = UserExporter(bot_entity)
    assert bot_exporter._is_slack_bot() is True

    # Regular user
    user_entity = DummyEntity("UUSER", {"is_bot": False})
    user_exporter = UserExporter(user_entity)
    assert user_exporter._is_slack_bot() is False

    # User without is_bot field
    no_bot_field_entity = DummyEntity("UUSER2", {"name": "user"})
    no_bot_exporter = UserExporter(no_bot_field_entity)
    assert no_bot_exporter._is_slack_bot() is False

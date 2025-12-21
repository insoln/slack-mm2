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
async def test_bot_export_error_no_user_id():
    """Test error handling when bot creation succeeds but user_id is missing."""
    entity = DummyEntity(
        "UBOT_NO_ID",
        {
            "name": "broken_bot",
            "is_bot": True,
            "profile": {"real_name": "Broken Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # No existing bot
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: []

    # Bot creation returns 201 but without user_id
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json = lambda: {"username": "broken_bot"}  # Missing user_id
    exporter.mm_api_post.return_value = mock_resp

    await exporter.export_entity()

    # Verify error was logged and status set to failed
    assert entity.mattermost_id is None
    exporter.set_status.assert_awaited_with(
        "failed", error="user_id not in bot response"
    )


@pytest.mark.asyncio
async def test_bot_export_error_creation_fails():
    """Test error handling when bot creation fails."""
    entity = DummyEntity(
        "UBOT_FAIL",
        {
            "name": "fail_bot",
            "is_bot": True,
            "profile": {"real_name": "Fail Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # No existing bot
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: []

    # Bot creation fails
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json = lambda: {"message": "Invalid bot data", "id": "api.error"}
    exporter.mm_api_post.return_value = mock_resp

    await exporter.export_entity()

    # Verify error was handled
    assert entity.mattermost_id is None
    exporter.set_status.assert_awaited_with("failed", error="Invalid bot data")


@pytest.mark.asyncio
async def test_bot_export_username_conflict():
    """Test error handling when bot username already exists."""
    entity = DummyEntity(
        "UBOT_CONFLICT",
        {
            "name": "conflict_bot",
            "is_bot": True,
            "profile": {"real_name": "Conflict Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # Mock mm_api_get to be called twice:
    # First call returns empty (no bot found initially)
    # Second call after username conflict returns the existing bot
    get_responses = [
        MagicMock(status_code=200, json=lambda: []),  # First call: no bot found
        MagicMock(
            status_code=200,
            json=lambda: [{"username": "conflict_bot", "user_id": "existing-user-id"}],
        ),  # Second call: bot found
    ]
    exporter.mm_api_get = AsyncMock(side_effect=get_responses)

    # Bot creation fails with username conflict
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json = lambda: {
        "message": "Username already exists",
        "id": "store.sql_user.save.username_exists.app_error",
    }
    exporter.mm_api_post.return_value = mock_resp

    await exporter.export_entity()

    # Verify the existing bot was retrieved and used
    assert entity.mattermost_id == "existing-user-id"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_bot_export_exception_handling():
    """Test exception handling in bot export."""
    entity = DummyEntity(
        "UBOT_EXCEPTION",
        {
            "name": "exception_bot",
            "is_bot": True,
            "profile": {"real_name": "Exception Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    # Mock to return a valid response first (for _find_existing_bot check)
    # then raise exception on bot creation
    exporter.mm_api_get = AsyncMock()
    exporter.mm_api_get.return_value.status_code = 200
    exporter.mm_api_get.return_value.json = lambda: []
    exporter.mm_api_post = AsyncMock(side_effect=Exception("Network error"))
    exporter._upload_avatar = AsyncMock()

    await exporter.export_entity()

    # Verify exception was handled
    assert entity.mattermost_id is None
    exporter.set_status.assert_awaited()
    call_args = exporter.set_status.call_args
    assert call_args[0][0] == "failed"
    assert "Network error" in call_args[1]["error"]


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

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


@pytest.mark.asyncio
async def test_bot_creation_disabled_fallback_to_user():
    """Test that bots are created as users when EnableBotAccountCreation is false."""
    entity = DummyEntity(
        "UBOT_DISABLED",
        {
            "name": "test_bot",
            "is_bot": True,
            "profile": {
                "real_name": "Test Bot",
                "email": "test@bot.com",
            },
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter.mm_api_post = AsyncMock()
    exporter._upload_avatar = AsyncMock()
    exporter._ensure_user_in_team = AsyncMock()

    # Mock config check to return bot creation disabled
    mock_config_resp = MagicMock()
    mock_config_resp.status_code = 200
    mock_config_resp.json = lambda: {
        "ServiceSettings": {"EnableBotAccountCreation": False}
    }

    # Mock user creation success
    mock_user_resp = MagicMock()
    mock_user_resp.status_code = 201
    mock_user_resp.json = lambda: {"id": "user-id-123", "username": "test_bot"}

    # Mock for email/username lookups (return 404 - not found)
    mock_not_found = MagicMock()
    mock_not_found.status_code = 404

    # Set up mock responses: first for config, then for user lookups
    async def mock_get(path):
        if path == "/api/v4/config":
            return mock_config_resp
        else:
            return mock_not_found

    exporter.mm_api_get = AsyncMock(side_effect=mock_get)
    exporter.mm_api_post.return_value = mock_user_resp

    # Reset cache before test
    UserExporter._config_cache_checked = False
    UserExporter._mm_config_cache = None

    await exporter.export_entity()

    # Verify config was checked
    config_calls = [
        call
        for call in exporter.mm_api_get.call_args_list
        if call[0][0] == "/api/v4/config"
    ]
    assert len(config_calls) == 1

    # Verify bot was created as user (not via /api/v4/bots)
    exporter.mm_api_post.assert_awaited_once()
    call_args = exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/users"  # User endpoint, not bot endpoint

    # Verify user was created successfully
    assert entity.mattermost_id == "user-id-123"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_bot_creation_enabled_creates_bot():
    """Test that bots are created as Bot Accounts when EnableBotAccountCreation is true."""
    entity = DummyEntity(
        "UBOT_ENABLED",
        {
            "name": "test_bot",
            "is_bot": True,
            "profile": {"real_name": "Test Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # Mock config check to return bot creation enabled
    mock_config_resp = MagicMock()
    mock_config_resp.status_code = 200
    mock_config_resp.json = lambda: {
        "ServiceSettings": {"EnableBotAccountCreation": True}
    }

    # Mock bot listing (no existing bots)
    mock_bot_list_resp = MagicMock()
    mock_bot_list_resp.status_code = 200
    mock_bot_list_resp.json = lambda: []

    # Mock bot creation success
    mock_bot_resp = MagicMock()
    mock_bot_resp.status_code = 201
    mock_bot_resp.json = lambda: {"user_id": "bot-user-id-456", "username": "test_bot"}

    # Set up mock responses
    async def mock_get(path):
        if path == "/api/v4/config":
            return mock_config_resp
        elif path.startswith("/api/v4/bots"):
            return mock_bot_list_resp
        return MagicMock(status_code=404)

    exporter.mm_api_get = AsyncMock(side_effect=mock_get)
    exporter.mm_api_post = AsyncMock(return_value=mock_bot_resp)

    # Reset cache before test
    UserExporter._config_cache_checked = False
    UserExporter._mm_config_cache = None

    await exporter.export_entity()

    # Verify config was checked
    assert any(
        call[0][0] == "/api/v4/config" for call in exporter.mm_api_get.call_args_list
    )

    # Verify bot was created via bot endpoint
    exporter.mm_api_post.assert_awaited_once()
    call_args = exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/bots"

    # Verify bot was created successfully
    assert entity.mattermost_id == "bot-user-id-456"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_config_check_failure_assumes_enabled():
    """Test that config check failure assumes bot creation is enabled (fail open)."""
    entity = DummyEntity(
        "UBOT_CONFIG_FAIL",
        {
            "name": "test_bot",
            "is_bot": True,
            "profile": {"real_name": "Test Bot"},
        },
    )

    exporter = UserExporter(entity)
    exporter.set_status = AsyncMock()
    exporter._upload_avatar = AsyncMock()

    # Mock config check to fail
    mock_config_resp = MagicMock()
    mock_config_resp.status_code = 500

    # Mock bot listing and creation
    mock_bot_list_resp = MagicMock()
    mock_bot_list_resp.status_code = 200
    mock_bot_list_resp.json = lambda: []

    mock_bot_resp = MagicMock()
    mock_bot_resp.status_code = 201
    mock_bot_resp.json = lambda: {"user_id": "bot-user-id-789", "username": "test_bot"}

    async def mock_get(path):
        if path == "/api/v4/config":
            return mock_config_resp
        elif path.startswith("/api/v4/bots"):
            return mock_bot_list_resp
        return MagicMock(status_code=404)

    exporter.mm_api_get = AsyncMock(side_effect=mock_get)
    exporter.mm_api_post = AsyncMock(return_value=mock_bot_resp)

    # Reset cache before test
    UserExporter._config_cache_checked = False
    UserExporter._mm_config_cache = None

    await exporter.export_entity()

    # Should still try to create as bot (fail open)
    exporter.mm_api_post.assert_awaited_once()
    call_args = exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/bots"

    assert entity.mattermost_id == "bot-user-id-789"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_normalize_bot_username():
    """Test username normalization for various bot usernames."""
    entity = DummyEntity("USLACKBOT", {"is_bot": True})
    exporter = UserExporter(entity)

    # Test USLACKBOT (all uppercase) - 'u' is a letter so no prefix needed
    result = exporter._normalize_bot_username("USLACKBOT", "USLACKBOT")
    assert result == "uslackbot"
    assert result[0].isalpha()  # Must start with letter
    assert len(result) <= 64

    # Test empty name (falls back to slack_ + hash of slack_id for uniqueness)
    result = exporter._normalize_bot_username("", "UBOT123")
    assert result.startswith("slack_")
    # Verify hash is present (8 hex chars after slack_)
    assert len(result) == 14  # "slack_" (6) + 8 hex chars
    import re

    assert re.match(r"^slack_[0-9a-f]{8}$", result)

    # Test already valid lowercase name
    result = exporter._normalize_bot_username("reminder_bot", "UBOT456")
    assert result == "reminder_bot"

    # Test name with invalid characters
    result = exporter._normalize_bot_username("Test Bot!", "UBOT789")
    assert result == "test_bot_"

    # Test name starting with number (needs prefix)
    result = exporter._normalize_bot_username("123bot", "UBOT999")
    assert result == "slack_123bot"

    # Test very long name (should truncate and add hash)
    long_name = "a" * 100
    result = exporter._normalize_bot_username(long_name, "ULONGBOT")
    assert len(result) == 64  # Exactly 64 chars: 55 base + "_" + 8 hex chars
    assert result.startswith("a" * 55)
    # Verify hash suffix is present
    assert result[55] == "_"
    assert re.match(r"^[0-9a-f]{8}$", result[56:])

    # Test name with special characters (@ is replaced, . is kept as valid)
    result = exporter._normalize_bot_username("bot@company.com", "UBOT111")
    assert result == "bot_company.com"

    # Test name with only invalid characters uses slack_id for uniqueness
    result = exporter._normalize_bot_username("@@@", "UBOT222")
    assert result.startswith("slack_")
    assert len(result) == 14  # "slack_" (6) + 8 hex chars
    assert re.match(r"^slack_[0-9a-f]{8}$", result)


@pytest.mark.asyncio
async def test_uslackbot_export_with_empty_name():
    """Test that USLACKBOT with empty name field exports successfully."""
    entity = DummyEntity(
        "USLACKBOT",
        {
            "name": "",  # Empty name field as seen in real exports
            "is_bot": True,
            "profile": {"real_name": "Slackbot"},
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

    # Bot creation succeeds with normalized username
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_post_resp.json = lambda: {
        "user_id": "slackbot-user-id",
        "username": "uslackbot",  # Normalized
    }
    exporter.mm_api_post.return_value = mock_post_resp

    await exporter.export_entity()

    # Verify bot was created with normalized username
    exporter.mm_api_post.assert_awaited_once()
    call_args = exporter.mm_api_post.call_args
    assert call_args[0][0] == "/api/v4/bots"

    payload = call_args[0][1]
    assert (
        payload["username"] == "uslackbot"
    )  # Normalized from empty -> USLACKBOT -> uslackbot
    assert payload["display_name"] == "Slackbot"

    # Verify success
    assert entity.mattermost_id == "slackbot-user-id"
    exporter.set_status.assert_awaited_with("success")


@pytest.mark.asyncio
async def test_uslackbot_export_uppercase_name():
    """Test that USLACKBOT with uppercase name field exports successfully."""
    entity = DummyEntity(
        "USLACKBOT",
        {
            "name": "USLACKBOT",  # Uppercase name
            "is_bot": True,
            "profile": {},
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

    # Bot creation succeeds
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_post_resp.json = lambda: {
        "user_id": "slackbot-user-id-2",
        "username": "uslackbot",
    }
    exporter.mm_api_post.return_value = mock_post_resp

    await exporter.export_entity()

    # Verify normalized username in payload
    call_args = exporter.mm_api_post.call_args
    payload = call_args[0][1]
    assert payload["username"] == "uslackbot"

    # Verify success
    assert entity.mattermost_id == "slackbot-user-id-2"
    exporter.set_status.assert_awaited_with("success")

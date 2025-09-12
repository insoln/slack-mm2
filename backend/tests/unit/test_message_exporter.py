import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.export.message_exporter import MessageExporter
from app.models.entity import Entity


@pytest.mark.asyncio
async def test_resolve_emoji_name_by_slack_name_standard_emoji():
    """Test that standard emojis (not custom) return as-is"""
    # Create a mock entity
    mock_entity = MagicMock()
    mock_entity.raw_data = {}

    exporter = MessageExporter(mock_entity)

    # Mock the database session
    with patch(
        "app.services.export.message_exporter.SessionLocal"
    ) as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session

        # Mock query that returns no custom emoji entity
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Test standard emoji
        result = await exporter._resolve_emoji_name_by_slack_name("smile")
        assert result == "smile"


@pytest.mark.asyncio
async def test_resolve_emoji_name_by_slack_name_custom_emoji():
    """Test that custom emojis are resolved correctly"""
    # Create a mock entity
    mock_entity = MagicMock()
    mock_entity.raw_data = {}

    exporter = MessageExporter(mock_entity)

    # Mock custom emoji entity
    mock_custom_emoji = MagicMock()
    mock_custom_emoji.raw_data = {
        "name": "blob-yes",
        "url": "https://example.com/blob-yes.png",
    }

    # Mock the database session
    with patch(
        "app.services.export.message_exporter.SessionLocal"
    ) as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session

        # Mock query that returns the custom emoji entity
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_custom_emoji
        mock_session.execute.return_value = mock_result

        # Test custom emoji
        result = await exporter._resolve_emoji_name_by_slack_name("blob-yes")
        assert result == "blob_yes"  # Hyphen gets converted to underscore


@pytest.mark.asyncio
async def test_resolve_emoji_name_by_slack_name_transliterated():
    """Test that custom emojis with Cyrillic names are transliterated"""
    # Create a mock entity
    mock_entity = MagicMock()
    mock_entity.raw_data = {}

    exporter = MessageExporter(mock_entity)

    # Mock custom emoji entity with Cyrillic name
    mock_custom_emoji = MagicMock()
    mock_custom_emoji.raw_data = {
        "name": "привет",
        "url": "https://example.com/privet.png",
    }

    # Mock the database session
    with patch(
        "app.services.export.message_exporter.SessionLocal"
    ) as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session

        # Mock query that returns the custom emoji entity
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_custom_emoji
        mock_session.execute.return_value = mock_result

        # Test custom emoji with Cyrillic name
        result = await exporter._resolve_emoji_name_by_slack_name("привет")
        assert result == "privet"  # Should be transliterated


@pytest.mark.asyncio
async def test_resolve_emoji_name_with_cache():
    """Test that emoji name resolution uses cache"""
    # Create a mock entity
    mock_entity = MagicMock()
    mock_entity.raw_data = {}

    # Create cache with pre-populated emoji
    cache = {"emoji_name_by_slack_name": {"cached-emoji": "cached-result"}}
    exporter = MessageExporter(mock_entity, caches=cache)

    # Should return cached result without database call
    result = await exporter._resolve_emoji_name_by_slack_name("cached-emoji")
    assert result == "cached-result"


@pytest.mark.asyncio
async def test_rich_element_to_md_emoji():
    """Test that emoji elements in rich text are processed correctly"""
    # Create a mock entity
    mock_entity = MagicMock()
    mock_entity.raw_data = {}

    exporter = MessageExporter(mock_entity)

    # Mock the emoji resolution method
    exporter._resolve_emoji_name_by_slack_name = AsyncMock(return_value="blob_yes")

    # Test emoji element
    emoji_element = {"type": "emoji", "name": "blob-yes"}
    result = await exporter._rich_element_to_md(emoji_element)

    assert result == ":blob_yes:"
    exporter._resolve_emoji_name_by_slack_name.assert_called_once_with("blob-yes")

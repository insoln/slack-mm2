"""Unit tests for reaction export with archived channels."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
from app.services.export.orchestrator import (
    _get_archived_channel_ids,
    _unarchive_channels,
    _rearchive_channels,
)


@pytest.mark.asyncio
async def test_get_archived_channel_ids():
    """Test identification of archived channels."""
    # Mock channels: 2 archived, 1 not archived, 1 without mattermost_id
    mock_channels = [
        SimpleNamespace(
            mattermost_id="ch1", raw_data={"is_archived": True}, status="success"
        ),
        SimpleNamespace(
            mattermost_id="ch2", raw_data={"is_archived": False}, status="success"
        ),
        SimpleNamespace(
            mattermost_id="ch3", raw_data={"is_archived": True}, status="success"
        ),
        SimpleNamespace(mattermost_id=None, raw_data={"is_archived": True}),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_channels

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        result = await _get_archived_channel_ids()

    assert result == {"ch1", "ch3"}


@pytest.mark.asyncio
async def test_unarchive_channels_success(monkeypatch):
    """Test successful unarchiving of channels."""
    monkeypatch.setenv("MM_URL", "http://test")
    monkeypatch.setenv("MM_TOKEN", "test_token")

    channel_ids = {"ch1", "ch2"}

    mock_response = SimpleNamespace(status_code=200, text="OK")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None

        await _unarchive_channels(channel_ids)

    # Verify API was called for each channel
    assert mock_client.post.call_count == 2
    calls = mock_client.post.call_args_list
    for call in calls:
        assert "/channel/unarchive" in call[0][0]
        assert "Bearer test_token" in str(call)


@pytest.mark.asyncio
async def test_unarchive_channels_empty_set():
    """Test unarchive with empty channel set does nothing."""
    with patch("httpx.AsyncClient") as mock_client_class:
        await _unarchive_channels(set())
    mock_client_class.assert_not_called()


@pytest.mark.asyncio
async def test_unarchive_channels_missing_env(monkeypatch):
    """Test unarchive handles missing environment variables gracefully."""
    # Clear environment variables
    monkeypatch.delenv("MM_URL", raising=False)
    monkeypatch.delenv("MM_TOKEN", raising=False)

    channel_ids = {"ch1"}

    with patch("httpx.AsyncClient") as mock_client_class:
        await _unarchive_channels(channel_ids)

    # Should not make API calls
    mock_client_class.assert_not_called()


@pytest.mark.asyncio
async def test_rearchive_channels_success(monkeypatch):
    """Test successful re-archiving of channels."""
    monkeypatch.setenv("MM_URL", "http://test")
    monkeypatch.setenv("MM_TOKEN", "test_token")

    channel_ids = {"ch1", "ch2"}

    mock_response = SimpleNamespace(status_code=200, text="OK")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None

        await _rearchive_channels(channel_ids)

    # Verify API was called for each channel
    assert mock_client.post.call_count == 2
    calls = mock_client.post.call_args_list
    for call in calls:
        assert "/channel/archive" in call[0][0]
        assert "Bearer test_token" in str(call)


@pytest.mark.asyncio
async def test_rearchive_channels_empty_set():
    """Test re-archive with empty channel set does nothing."""
    with patch("httpx.AsyncClient") as mock_client_class:
        await _rearchive_channels(set())
    mock_client_class.assert_not_called()


@pytest.mark.asyncio
async def test_rearchive_channels_handles_errors(monkeypatch):
    """Test re-archive handles API errors gracefully."""
    monkeypatch.setenv("MM_URL", "http://test")
    monkeypatch.setenv("MM_TOKEN", "test_token")

    channel_ids = {"ch1"}

    # Mock an error response
    mock_response = SimpleNamespace(status_code=500, text="Internal error")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None

        # Should not raise exception
        await _rearchive_channels(channel_ids)

    # API was still called
    mock_client.post.assert_called_once()

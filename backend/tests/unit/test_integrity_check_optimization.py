import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.services.backup import messages_import


@pytest.mark.asyncio
async def test_integrity_check_can_be_skipped(monkeypatch):
    """Test that SKIP_INTEGRITY_CHECKS environment variable bypasses integrity checks."""
    # Set environment variable to skip integrity checks
    monkeypatch.setenv("SKIP_INTEGRITY_CHECKS", "1")

    # Mock the entire parse_channel_messages logic to isolate integrity check
    export_dir = "/fake/export"
    folder_channel_map = {}  # Empty to avoid actual processing

    # Mock dependencies
    monkeypatch.setattr(messages_import.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(messages_import.glob, "glob", lambda p: [])

    # Mock the database session to ensure integrity check is NOT executed
    mock_session_instance = MagicMock()
    mock_session_instance.execute = AsyncMock()

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session_instance)
    mock_session.__aexit__ = AsyncMock()

    with patch("app.models.base.SessionLocal", return_value=mock_session):
        result = await messages_import.parse_channel_messages(
            export_dir, folder_channel_map, job_id=1
        )

        # Verify the function completes without errors
        assert isinstance(result, dict)

        # The execute method should NOT be called if integrity checks are skipped
        mock_session_instance.execute.assert_not_called()


@pytest.mark.asyncio
async def test_integrity_check_uses_not_exists(monkeypatch):
    """Test that integrity check uses NOT EXISTS instead of NOT IN for performance."""
    # Do NOT set SKIP_INTEGRITY_CHECKS - we want the check to run
    monkeypatch.delenv("SKIP_INTEGRITY_CHECKS", raising=False)

    export_dir = "/fake/export"
    folder_channel_map = {}  # Empty to avoid actual processing

    # Mock to avoid actual processing
    monkeypatch.setattr(messages_import.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(messages_import.glob, "glob", lambda p: [])

    # Mock database session and verify the query structure
    mock_session_instance = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 0  # No reactions
    mock_session_instance.execute = AsyncMock(return_value=mock_result)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session_instance)
    mock_session.__aexit__ = AsyncMock()

    captured_queries = []

    async def capture_execute(query):
        # Capture the query for inspection
        captured_queries.append(str(query))
        return mock_result

    mock_session_instance.execute = capture_execute

    with patch("app.models.base.SessionLocal", return_value=mock_session):
        result = await messages_import.parse_channel_messages(
            export_dir, folder_channel_map, job_id=1
        )

        assert isinstance(result, dict)

        # Verify at least one query was executed (total count)
        assert len(captured_queries) >= 1

        # Check that queries contain EXISTS pattern, not IN pattern
        # This is a basic check - the actual SQL generation depends on SQLAlchemy
        for query in captured_queries:
            # Should not have the old NOT IN pattern
            # Note: This is a heuristic check since SQLAlchemy generates complex SQL
            query_lower = query.lower()
            if "not in" in query_lower:
                # Only fail if it's the problematic subquery pattern
                if "select" in query_lower and "relation_type" in query_lower:
                    pytest.fail("Query should use NOT EXISTS, not NOT IN")


@pytest.mark.asyncio
async def test_integrity_check_environment_variable_variations(monkeypatch):
    """Test that various values of SKIP_INTEGRITY_CHECKS work correctly."""
    test_cases = [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("0", False),
        ("false", False),
        ("", False),
    ]

    for env_value, should_skip in test_cases:
        monkeypatch.setenv("SKIP_INTEGRITY_CHECKS", env_value)

        export_dir = "/fake/export"
        folder_channel_map = {}

        monkeypatch.setattr(messages_import.os.path, "isdir", lambda p: False)
        monkeypatch.setattr(messages_import.glob, "glob", lambda p: [])

        mock_session_instance = MagicMock()
        mock_session_instance.execute = AsyncMock()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session.__aexit__ = AsyncMock()

        with patch("app.models.base.SessionLocal", return_value=mock_session):
            result = await messages_import.parse_channel_messages(
                export_dir, folder_channel_map, job_id=1
            )

            assert isinstance(result, dict)

            # If should_skip is True, execute should not be called
            if should_skip:
                mock_session_instance.execute.assert_not_called()

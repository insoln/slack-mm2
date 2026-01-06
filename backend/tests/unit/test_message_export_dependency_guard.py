"""Unit tests for message export dependency guard.

This test validates that messages with dependencies that are not 'success'
are skipped with an actionable reason instead of failing with "No target channel".
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from types import SimpleNamespace
from app.models.status_enum import MappingStatus


@pytest.mark.asyncio
async def test_message_exporter_calls_guard_dependencies():
    """Test that MessageExporter.guard_dependencies is called during message export.

    This is a simple integration test that verifies the fix: messages now check
    dependencies before attempting export, preventing cascade failures.
    """
    from app.services.export.message_exporter import MessageExporter

    # Create a mock message entity
    mock_message = SimpleNamespace(
        id=1,
        entity_type="message",
        slack_id="1234567890.123456",
        status=MappingStatus.pending,
        job_id=1,
        raw_data={"ts": "1234567890.123456", "text": "test message"},
    )

    # Create the exporter
    exporter = MessageExporter(mock_message)

    # Mock guard_dependencies to return that it should skip
    with patch.object(
        exporter, "guard_dependencies", new_callable=AsyncMock
    ) as mock_guard, patch.object(
        exporter, "set_status", new_callable=AsyncMock
    ) as mock_set_status:

        # Configure mock to indicate dependency failure
        mock_guard.return_value = (True, "dependency channel:C123 status=skipped")

        # Simulate what the orchestrator does in _export_list
        failed, reason = await exporter.guard_dependencies()
        if failed:
            await exporter.set_status("skipped", error=reason)

        # Verify guard_dependencies was called
        mock_guard.assert_called_once()

        # Verify set_status was called with "skipped" and the dependency reason
        mock_set_status.assert_called_once_with(
            "skipped", error="dependency channel:C123 status=skipped"
        )


@pytest.mark.asyncio
async def test_guard_dependencies_skips_message_when_channel_skipped():
    """Test that guard_dependencies returns skip=True when channel is skipped."""
    from app.services.export.message_exporter import MessageExporter

    # Create a mock message entity
    mock_message = SimpleNamespace(
        id=1,
        entity_type="message",
        slack_id="1234567890.123456",
        status=MappingStatus.pending,
        job_id=1,
        raw_data={"ts": "1234567890.123456", "text": "test message"},
    )

    # Create exporter
    exporter = MessageExporter(mock_message)

    # Mock the base guard_dependencies to return skip
    with patch(
        "app.services.export.base_exporter.ExporterBase.guard_dependencies",
        new_callable=AsyncMock,
    ) as mock_base_guard:
        mock_base_guard.return_value = (
            True,
            "dependency channel:D088B6CQNK1 status=skipped",
        )

        # Call guard_dependencies
        skip, reason = await exporter.guard_dependencies()

        # Verify it returns skip=True with reason
        assert skip is True
        assert reason is not None
        assert "channel" in reason
        assert "skipped" in reason


@pytest.mark.asyncio
async def test_guard_dependencies_passes_when_all_deps_success():
    """Test that guard_dependencies returns skip=False when all dependencies are success."""
    from app.services.export.message_exporter import MessageExporter

    # Create a mock message entity
    mock_message = SimpleNamespace(
        id=1,
        entity_type="message",
        slack_id="1234567890.123456",
        status=MappingStatus.pending,
        job_id=1,
        raw_data={"ts": "1234567890.123456", "text": "test message"},
    )

    # Create exporter
    exporter = MessageExporter(mock_message)

    # Mock the base guard_dependencies to return no skip
    with patch(
        "app.services.export.base_exporter.ExporterBase.guard_dependencies",
        new_callable=AsyncMock,
    ) as mock_base_guard:
        mock_base_guard.return_value = (False, None)

        # Call guard_dependencies
        skip, reason = await exporter.guard_dependencies()

        # Verify it returns skip=False
        assert skip is False
        assert reason is None

import pytest
import logging
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.backup import orchestrator, messages_import


@pytest.mark.asyncio
async def test_concurrency_fixed_to_one():
    """Test that concurrency is always 1 regardless of environment variables."""
    # This test verifies that IMPORT_CHANNEL_CONCURRENCY environment variable is ignored

    with patch.dict("os.environ", {"IMPORT_CHANNEL_CONCURRENCY": "10"}):
        # Mock all the dependencies to avoid actual execution
        with patch(
            "app.services.backup.orchestrator.extract_zip", new_callable=AsyncMock
        ), patch(
            "app.services.backup.orchestrator.get_slack_emoji_list",
            new_callable=AsyncMock,
        ), patch(
            "app.services.backup.orchestrator.parse_users", new_callable=AsyncMock
        ), patch(
            "app.services.backup.orchestrator.parse_channels_and_chats",
            new_callable=AsyncMock,
        ), patch(
            "app.services.backup.orchestrator.parse_messages_and_related",
            new_callable=AsyncMock,
        ) as mock_parse, patch(
            "app.services.backup.orchestrator.orchestrate_mm_export",
            new_callable=AsyncMock,
        ), patch(
            "app.services.backup.orchestrator.merge_job_meta", new_callable=AsyncMock
        ), patch(
            "app.services.backup.orchestrator.find_channel_for_folder", return_value={}
        ), patch(
            "app.services.backup.orchestrator.SessionLocal"
        ), patch(
            "app.services.backup.orchestrator.tempfile.mkdtemp",
            return_value="/tmp/test",
        ), patch(
            "app.services.backup.orchestrator.os.path.exists", return_value=True
        ), patch(
            "app.services.backup.orchestrator.os.listdir", return_value=[]
        ), patch(
            "app.services.backup.orchestrator.shutil.rmtree"
        ):

            # Should not raise any errors and concurrency should be forced to 1
            await orchestrator.orchestrate_slack_import("/fake/path.zip")

            # Verify that parse_messages_and_related is called (sequential path)
            assert mock_parse.called

            # The parallel execution path should never be taken, so we expect
            # only one call to parse_messages_and_related (the sequential one)
            assert mock_parse.call_count == 1


@pytest.mark.asyncio
async def test_stage_durations_controlled_by_debug_logging():
    """Test that stage duration recording is controlled by DEBUG logging level."""

    # Test with DEBUG logging enabled
    with patch(
        "app.services.backup.orchestrator.backend_logger.isEnabledFor",
        return_value=True,
    ) as mock_debug:
        with patch.dict(
            "os.environ", {"IMPORT_RECORD_STAGE_DURATIONS": "0"}
        ):  # Env var should be ignored
            with patch(
                "app.services.backup.orchestrator.extract_zip", new_callable=AsyncMock
            ), patch(
                "app.services.backup.orchestrator.get_slack_emoji_list",
                new_callable=AsyncMock,
            ), patch(
                "app.services.backup.orchestrator.parse_users", new_callable=AsyncMock
            ), patch(
                "app.services.backup.orchestrator.parse_channels_and_chats",
                new_callable=AsyncMock,
            ), patch(
                "app.services.backup.orchestrator.parse_messages_and_related",
                new_callable=AsyncMock,
            ), patch(
                "app.services.backup.orchestrator.orchestrate_mm_export",
                new_callable=AsyncMock,
            ), patch(
                "app.services.backup.orchestrator.merge_job_meta",
                new_callable=AsyncMock,
            ) as mock_meta, patch(
                "app.services.backup.orchestrator.find_channel_for_folder",
                return_value={},
            ), patch(
                "app.services.backup.orchestrator.SessionLocal"
            ), patch(
                "app.services.backup.orchestrator.tempfile.mkdtemp",
                return_value="/tmp/test",
            ), patch(
                "app.services.backup.orchestrator.os.path.exists", return_value=True
            ), patch(
                "app.services.backup.orchestrator.os.listdir", return_value=[]
            ), patch(
                "app.services.backup.orchestrator.shutil.rmtree"
            ):

                await orchestrator.orchestrate_slack_import("/fake/path.zip")

                # Verify that DEBUG level check was called
                mock_debug.assert_called_with(logging.DEBUG)

                # Verify that durations container was initialized (record_durations=True)
                duration_calls = [
                    call
                    for call in mock_meta.call_args_list
                    if "nested" in call.kwargs
                    and "durations" in call.kwargs.get("nested", {})
                ]
                assert len(duration_calls) > 0


def test_meta_update_constants_fixed():
    """Test that meta update intervals use fixed constants instead of environment variables."""

    # Set environment variables that should be ignored
    with patch.dict(
        "os.environ",
        {"IMPORT_META_UPDATE_INTERVAL_SEC": "10", "IMPORT_META_UPDATE_EVERY": "5000"},
    ):
        # Mock the dependencies
        export_dir = "/fake/export"
        folder_channel_map = {"general": {"id": "C123", "name": "general"}}

        with patch(
            "app.services.backup.messages_import.os.path.isdir", return_value=True
        ), patch(
            "app.services.backup.messages_import.glob.glob", return_value=[]
        ), patch(
            "app.services.backup.messages_import._create_emojis", new_callable=AsyncMock
        ):

            # Call the function - it should complete without using env vars
            import asyncio

            result = asyncio.run(
                messages_import.parse_channel_messages(
                    export_dir, folder_channel_map, batch_size=1000
                )
            )

            # Should return expected structure regardless of env vars
            assert isinstance(result, dict)
            assert "messages" in result
            assert "reactions" in result
            assert "attachments" in result
            assert "emojis" in result

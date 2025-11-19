"""Test cases for AttachmentExporter with new direct plugin flow."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import os
from app.services.export.attachment_exporter import AttachmentExporter
from app.models.entity import Entity


class TestAttachmentExporter:
    """Test the AttachmentExporter with new plugin flow."""

    def create_mock_attachment_entity(self, raw_data=None):
        """Create a mock attachment entity for testing."""
        if raw_data is None:
            raw_data = {
                "id": "F123456789",
                "name": "test-file.pdf",
                "url_private": "https://files.slack.com/files-pri/T123/F123456789/test-file.pdf",
                "size": 1024,
                "channel_id": "C123456789",
            }

        entity = MagicMock(spec=Entity)
        entity.slack_id = "F123456789"
        entity.raw_data = raw_data
        entity.mattermost_id = None
        return entity

    @pytest.mark.asyncio
    async def test_export_entity_success_flow(self):
        """Test successful attachment export via new plugin flow."""
        # Setup
        raw_data = {
            "name": "test-document.pdf",
            "url_private": "https://files.slack.com/files-pri/T123/F123/test-document.pdf",
            "size": 2048,
        }
        entity = self.create_mock_attachment_entity(raw_data)

        exporter = AttachmentExporter(entity)

        # Mock dependencies including user resolution and parent message
        with patch.object(
            exporter,
            "_resolve_parent_message_mm_id",
            return_value="mm_post_123",
        ), patch.object(
            exporter,
            "_resolve_mm_channel_id_for_attachment",
            return_value="mm_channel_123",
        ), patch.object(
            exporter, "_resolve_mm_user_id_for_attachment", return_value="mm_user_123"
        ), patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(
            exporter, "log_export"
        ), patch.object(
            exporter, "mm_api_post_attachment_from_url", new_callable=AsyncMock
        ) as mock_api_call, patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test-token"}
        ):

            # Setup successful API response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"file_id": "mattermost_file_123"}
            mock_api_call.return_value = mock_response

            # Execute
            await exporter.export_entity()

            # Verify API was called with correct parameters (now includes post_id)
            mock_api_call.assert_called_once_with(
                "mm_channel_123",
                "test-document.pdf",
                "https://files.slack.com/files-pri/T123/F123/test-document.pdf",
                "Bearer xoxb-test-token",
                "mm_user_123",
                "mm_post_123",
            )

            # Verify success status was set
            mock_set_status.assert_called_once_with("success")

            # Verify entity was updated with file_id
            assert entity.mattermost_id == "mattermost_file_123"

    @pytest.mark.asyncio
    async def test_export_entity_missing_url_private(self):
        """Test that export fails when url_private is missing."""
        # Setup entity without url_private
        raw_data = {
            "name": "test-document.pdf",
            "size": 2048,
            # Missing url_private
        }
        entity = self.create_mock_attachment_entity(raw_data)

        exporter = AttachmentExporter(entity)

        # Mock dependencies
        with patch.object(
            exporter,
            "_resolve_parent_message_mm_id",
            return_value="mm_post_123",
        ), patch.object(
            exporter,
            "_resolve_mm_channel_id_for_attachment",
            return_value="mm_channel_123",
        ), patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(
            exporter, "log_export"
        ):

            # Execute
            await exporter.export_entity()

            # Verify failure status was set with appropriate error
            mock_set_status.assert_called_once_with(
                "failed",
                error="No content source: neither url_private nor url_private_download",
            )

    @pytest.mark.asyncio
    async def test_export_entity_missing_slack_token(self):
        """Test that export fails when Slack token is missing."""
        entity = self.create_mock_attachment_entity()

        exporter = AttachmentExporter(entity)

        # Mock dependencies with no SLACK_BOT_TOKEN
        with patch.object(
            exporter,
            "_resolve_parent_message_mm_id",
            return_value="mm_post_123",
        ), patch.object(
            exporter,
            "_resolve_mm_channel_id_for_attachment",
            return_value="mm_channel_123",
        ), patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(
            exporter, "log_export"
        ), patch.dict(
            os.environ, {}, clear=True
        ):  # Clear environment

            # Execute
            await exporter.export_entity()

            # Verify failure status was set
            mock_set_status.assert_called_once_with(
                "failed", error="No Slack token available"
            )

    @pytest.mark.asyncio
    async def test_export_entity_plugin_error(self):
        """Test handling of plugin API errors."""
        entity = self.create_mock_attachment_entity()

        exporter = AttachmentExporter(entity)

        # Mock dependencies
        with patch.object(
            exporter,
            "_resolve_parent_message_mm_id",
            return_value="mm_post_123",
        ), patch.object(
            exporter,
            "_resolve_mm_channel_id_for_attachment",
            return_value="mm_channel_123",
        ), patch.object(
            exporter, "_resolve_mm_user_id_for_attachment", return_value="mm_user_123"
        ), patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(
            exporter, "log_export"
        ), patch.object(
            exporter, "mm_api_post_attachment_from_url", new_callable=AsyncMock
        ) as mock_api_call, patch.dict(
            os.environ, {"SLACK_BOT_TOKEN": "xoxb-test-token"}
        ):

            # Setup error response
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {"error": "Invalid file URL"}
            mock_response.text = '{"error": "Invalid file URL"}'
            mock_api_call.return_value = mock_response

            # Execute
            await exporter.export_entity()

            # Verify failure status was set with plugin error
            mock_set_status.assert_called_once_with(
                "failed", error="Plugin upload failed: 400 Invalid file URL"
            )

    @pytest.mark.asyncio
    async def test_export_entity_size_cap_exceeded(self):
        """Test that large files are skipped based on size cap."""
        # Setup large file
        raw_data = {
            "name": "large-file.pdf",
            "url_private": "https://files.slack.com/files-pri/T123/F123/large-file.pdf",
            "size": 50 * 1024 * 1024,  # 50MB
        }
        entity = self.create_mock_attachment_entity(raw_data)

        exporter = AttachmentExporter(entity)

        # Mock dependencies with 10MB size cap
        with patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(exporter, "log_export"), patch.dict(
            os.environ, {"ATTACHMENT_MAX_MB": "10"}
        ):

            # Execute
            await exporter.export_entity()

            # Verify file was skipped due to size
            mock_set_status.assert_called_once_with(
                "skipped", error="Attachment large-file.pdf 50.0MB exceeds cap 10.0MB"
            )

    @pytest.mark.asyncio
    async def test_attachment_extended_timeout_env_applied(self):
        """Smoke test: ensure ATTACHMENT_URL_TIMEOUT_SECONDS presence does not break export flow."""
        raw_data = {
            "name": "timeout-file.bin",
            "url_private": "https://files.slack.com/files-pri/T123/F999/timeout-file.bin",
            "channel_id": "C123456789",
        }
        entity = self.create_mock_attachment_entity(raw_data)
        exporter = AttachmentExporter(entity)
        with patch.object(
            exporter,
            "_resolve_parent_message_mm_id",
            return_value="mm_post_123",
        ), patch.object(
            exporter,
            "_resolve_mm_channel_id_for_attachment",
            return_value="mm_channel_123",
        ), patch.object(
            exporter, "_resolve_mm_user_id_for_attachment", return_value="mm_user_123"
        ), patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(
            exporter, "log_export"
        ), patch.object(
            exporter, "mm_api_post_attachment_from_url", new_callable=AsyncMock
        ) as mock_api_call, patch.dict(
            os.environ,
            {
                "SLACK_BOT_TOKEN": "xoxb-test-token",
                "ATTACHMENT_URL_TIMEOUT_SECONDS": "5",
            },
        ):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"file_id": "mattermost_file_timeout"}
            mock_api_call.return_value = mock_response
            await exporter.export_entity()
            mock_api_call.assert_called_once()
            mock_set_status.assert_called_once_with("success")
            assert entity.mattermost_id == "mattermost_file_timeout"

    @pytest.mark.asyncio
    async def test_export_entity_missing_parent_message(self):
        """Test that export is skipped when parent message has no mattermost_id."""
        entity = self.create_mock_attachment_entity()

        exporter = AttachmentExporter(entity)

        # Mock parent message resolution to return None
        with patch.object(
            exporter,
            "_resolve_parent_message_mm_id",
            return_value=None,
        ), patch.object(
            exporter, "set_status", new_callable=AsyncMock
        ) as mock_set_status, patch.object(
            exporter, "log_export"
        ):

            # Execute
            await exporter.export_entity()

            # Verify skipped status was set with appropriate error
            mock_set_status.assert_called_once_with(
                "skipped",
                error="Parent message not yet exported or missing mattermost_id",
            )

"""Test cases for MMApiMixin methods."""

import pytest
from unittest.mock import AsyncMock, patch
from app.services.export.mm_api_mixin import MMApiMixin


class TestMMApiMixin:
    """Test the MMApiMixin methods."""

    def test_redact_payload_with_base64_content(self):
        """Test that base64 content is redacted in log payload."""
        mixin = MMApiMixin()
        payload = {
            "channel_id": "test_channel",
            "filename": "test.txt",
            "content_base64": "SGVsbG8gV29ybGQ=",  # "Hello World" in base64
        }
        redacted = mixin._redact_payload(payload)

        assert redacted["channel_id"] == "test_channel"
        assert redacted["filename"] == "test.txt"
        assert "redacted base64" in redacted["content_base64"]
        assert "16 chars" in redacted["content_base64"]

    @pytest.mark.asyncio
    async def test_mm_api_post_attachment_from_url(self):
        """Test the new attachment_from_url method."""
        mixin = MMApiMixin()

        # Mock the mm_api_post method
        with patch.object(mixin, "mm_api_post", new_callable=AsyncMock) as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"file_id": "test_file_id"}
            mock_post.return_value = mock_response

            result = await mixin.mm_api_post_attachment_from_url(
                channel_id="test_channel",
                filename="test.txt",
                file_url="https://files.slack.com/test.txt",
                auth_header="Bearer xoxb-test-token",
            )

            # Verify mm_api_post was called with correct parameters
            mock_post.assert_called_once_with(
                "/plugins/mm-importer/api/v1/attachment_from_url",
                {
                    "channel_id": "test_channel",
                    "filename": "test.txt",
                    "file_url": "https://files.slack.com/test.txt",
                    "auth_header": "Bearer xoxb-test-token",
                },
            )

            assert result == mock_response

    @pytest.mark.asyncio
    async def test_mm_api_post_attachment_from_url_with_user_id(self):
        """Test the attachment_from_url method with user_id parameter."""
        mixin = MMApiMixin()
        
        # Mock the mm_api_post method
        with patch.object(mixin, 'mm_api_post', new_callable=AsyncMock) as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"file_id": "test_file_id"}
            mock_post.return_value = mock_response
            
            result = await mixin.mm_api_post_attachment_from_url(
                channel_id="test_channel",
                filename="test.txt",
                file_url="https://files.slack.com/test.txt",
                auth_header="Bearer xoxb-test-token",
                user_id="test_user_id"
            )
            
            # Verify mm_api_post was called with correct parameters including user_id
            mock_post.assert_called_once_with(
                "/plugins/mm-importer/api/v1/attachment_from_url",
                {
                    "channel_id": "test_channel",
                    "filename": "test.txt",
                    "file_url": "https://files.slack.com/test.txt",
                    "auth_header": "Bearer xoxb-test-token",
                    "user_id": "test_user_id",
                }
            )
            
            assert result == mock_response

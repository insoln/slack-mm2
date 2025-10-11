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

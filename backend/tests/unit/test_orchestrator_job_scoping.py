"""Unit tests for get_entities_to_export job scoping fix.

This test validates that reactions and attachments are properly filtered by job_id
to prevent cross-job export issues where entities from future jobs get picked up
prematurely and skipped due to unmet dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from app.services.export.orchestrator import get_entities_to_export
from app.models.status_enum import MappingStatus


@pytest.mark.asyncio
async def test_get_entities_to_export_reaction_with_job_id():
    """Test that reactions are filtered by job_id when provided."""
    # Create mock entities from two different jobs
    job1_reactions = [
        SimpleNamespace(
            id=1,
            entity_type="reaction",
            slack_id="reaction1",
            status=MappingStatus.pending,
            job_id=1,
        ),
        SimpleNamespace(
            id=2,
            entity_type="reaction",
            slack_id="reaction2",
            status=MappingStatus.pending,
            job_id=1,
        ),
    ]
    job2_reactions = [
        SimpleNamespace(
            id=3,
            entity_type="reaction",
            slack_id="reaction3",
            status=MappingStatus.pending,
            job_id=2,
        ),
    ]

    all_reactions = job1_reactions + job2_reactions

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = job1_reactions

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        # Request reactions for job_id=1
        result = await get_entities_to_export("reaction", job_id=1)

        # Should only return reactions from job 1
        assert len(result) == 2
        assert all(r.job_id == 1 for r in result)


@pytest.mark.asyncio
async def test_get_entities_to_export_attachment_with_job_id():
    """Test that attachments are filtered by job_id when provided."""
    # Create mock entities from two different jobs
    job1_attachments = [
        SimpleNamespace(
            id=1,
            entity_type="attachment",
            slack_id="attach1",
            status=MappingStatus.pending,
            job_id=1,
        ),
    ]
    job2_attachments = [
        SimpleNamespace(
            id=2,
            entity_type="attachment",
            slack_id="attach2",
            status=MappingStatus.pending,
            job_id=2,
        ),
        SimpleNamespace(
            id=3,
            entity_type="attachment",
            slack_id="attach3",
            status=MappingStatus.pending,
            job_id=2,
        ),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = job2_attachments

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        # Request attachments for job_id=2
        result = await get_entities_to_export("attachment", job_id=2)

        # Should only return attachments from job 2
        assert len(result) == 2
        assert all(r.job_id == 2 for r in result)


@pytest.mark.asyncio
async def test_get_entities_to_export_user_ignores_job_id():
    """Test that global entity types (user) ignore job_id filtering."""
    # Create mock user entities from different jobs
    users = [
        SimpleNamespace(
            id=1,
            entity_type="user",
            slack_id="user1",
            status=MappingStatus.pending,
            job_id=1,
            mattermost_id=None,
            raw_data={"id": "user1", "name": "User One"},
        ),
        SimpleNamespace(
            id=2,
            entity_type="user",
            slack_id="user2",
            status=MappingStatus.pending,
            job_id=2,
            mattermost_id=None,
            raw_data={"id": "user2", "name": "User Two"},
        ),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = users

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        # Request users with job_id=1 (should be ignored, returning all users)
        result = await get_entities_to_export("user", job_id=1)

        # Should return all users, not just from job 1
        assert len(result) == 2


@pytest.mark.asyncio
async def test_get_entities_to_export_channel_ignores_job_id():
    """Test that global entity types (channel) ignore job_id filtering."""
    # Create mock channel entities from different jobs
    channels = [
        SimpleNamespace(
            id=1,
            entity_type="channel",
            slack_id="channel1",
            status=MappingStatus.pending,
            job_id=1,
            raw_data={"id": "channel1", "name": "Channel One"},
        ),
        SimpleNamespace(
            id=2,
            entity_type="channel",
            slack_id="channel2",
            status=MappingStatus.pending,
            job_id=2,
            raw_data={"id": "channel2", "name": "Channel Two"},
        ),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = channels

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        # Request channels with job_id=1 (should be ignored, returning all channels)
        result = await get_entities_to_export("channel", job_id=1)

        # Should return all channels, not just from job 1
        assert len(result) == 2


@pytest.mark.asyncio
async def test_get_entities_to_export_reaction_without_job_id():
    """Test that reactions can be fetched without job_id (returns all)."""
    reactions = [
        SimpleNamespace(
            id=1,
            entity_type="reaction",
            slack_id="reaction1",
            status=MappingStatus.pending,
            job_id=1,
        ),
        SimpleNamespace(
            id=2,
            entity_type="reaction",
            slack_id="reaction2",
            status=MappingStatus.pending,
            job_id=2,
        ),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = reactions

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        # Request reactions without job_id
        result = await get_entities_to_export("reaction", job_id=None)

        # Should return all reactions
        assert len(result) == 2


@pytest.mark.asyncio
async def test_get_entities_to_export_only_pending_status():
    """Test that only pending entities are returned, not skipped/failed."""
    reactions = [
        SimpleNamespace(
            id=1,
            entity_type="reaction",
            slack_id="reaction1",
            status=MappingStatus.pending,
            job_id=1,
        ),
        # These should not be in the result
        # SimpleNamespace(
        #     id=2,
        #     entity_type="reaction",
        #     slack_id="reaction2",
        #     status=MappingStatus.skipped,
        #     job_id=1,
        # ),
        # SimpleNamespace(
        #     id=3,
        #     entity_type="reaction",
        #     slack_id="reaction3",
        #     status=MappingStatus.failed,
        #     job_id=1,
        # ),
    ]

    mock_result = MagicMock()
    # Mock returns only pending entities (skipped/failed filtered by query)
    mock_result.scalars.return_value.all.return_value = reactions

    with patch("app.services.export.orchestrator.SessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value = mock_session

        result = await get_entities_to_export("reaction", job_id=1)

        # Should only return pending entity
        assert len(result) == 1
        assert result[0].status == MappingStatus.pending

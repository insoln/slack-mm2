"""Unit tests for job restart functionality."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from app.api.jobs import restart_job
from app.models.job_status_enum import JobStatus


@pytest.mark.asyncio
async def test_restart_job_not_found():
    """Test restart fails when job doesn't exist."""
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)

    background_tasks = MagicMock()

    with patch("app.api.jobs.SessionLocal") as mock_session_local:
        mock_session_local.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await restart_job(999, background_tasks)

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_restart_job_invalid_status():
    """Test restart fails when job is still running."""
    mock_job = MagicMock()
    mock_job.status = JobStatus.running

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_job)

    background_tasks = MagicMock()

    with patch("app.api.jobs.SessionLocal") as mock_session_local:
        mock_session_local.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await restart_job(1, background_tasks)

        assert exc_info.value.status_code == 400
        assert "cannot restart" in exc_info.value.detail


@pytest.mark.asyncio
async def test_restart_job_no_retryable_entities():
    """Test restart fails when there are no failed/skipped entities."""
    mock_job = MagicMock()
    mock_job.status = JobStatus.success

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_job)

    # Mock the count query to return 0
    mock_result = MagicMock()
    mock_result.scalar_one = MagicMock(return_value=0)
    mock_session.execute = AsyncMock(return_value=mock_result)

    background_tasks = MagicMock()

    with patch("app.api.jobs.SessionLocal") as mock_session_local:
        mock_session_local.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await restart_job(1, background_tasks)

        assert exc_info.value.status_code == 400
        assert "no failed or skipped" in exc_info.value.detail


@pytest.mark.asyncio
async def test_restart_job_success():
    """Test successful job restart."""
    mock_job = MagicMock()
    mock_job.status = JobStatus.success

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_job)

    # Mock the count query to return 5 failed/skipped entities
    mock_count_result = MagicMock()
    mock_count_result.scalar_one = MagicMock(return_value=5)

    # Mock execute to return count result for first call, None for updates
    call_count = [0]

    async def mock_execute(query):
        call_count[0] += 1
        if call_count[0] == 1:  # First call is the count query
            return mock_count_result
        return None  # Subsequent calls are updates

    mock_session.execute = mock_execute
    mock_session.commit = AsyncMock()

    background_tasks = MagicMock()

    with patch("app.api.jobs.SessionLocal") as mock_session_local:
        mock_session_local.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("app.api.jobs.backend_logger") as mock_logger:
            result = await restart_job(1, background_tasks)

        assert result["status"] == "restart_initiated"
        assert result["reset_count"] == 5
        assert "Job 1 restarted" in result["message"]

        # Verify background task was added
        background_tasks.add_task.assert_called_once()

        # Verify logger was called
        mock_logger.info.assert_called_once()

"""Integration test for job restart functionality.

This test validates the complete restart workflow including:
- Creating a job with failed/skipped entities
- Calling the restart endpoint
- Verifying entities are reset to pending
- Verifying job status is updated
"""

import pytest
from httpx import AsyncClient
from app.main import app
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
from sqlalchemy import select


@pytest.mark.asyncio
async def test_job_restart_integration():
    """Test the complete restart workflow end-to-end."""
    # Create a test job with some failed/skipped entities
    async with SessionLocal() as session:
        # Create a completed job
        job = ImportJob(
            status=JobStatus.success,
            current_stage="done",
            meta={"test": "integration"},
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        # Create some entities with different statuses
        entities = [
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="msg1",
                status=MappingStatus.success,
            ),
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="msg2",
                status=MappingStatus.failed,
                error_message="Test error",
            ),
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="msg3",
                status=MappingStatus.skipped,
                error_message="Test skip",
            ),
            Entity(
                job_id=job_id,
                entity_type="user",
                slack_id="user1",
                status=MappingStatus.failed,
                error_message="User failed",
            ),
        ]
        for entity in entities:
            session.add(entity)
        await session.commit()

    try:
        # Call the restart endpoint
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(f"/api/jobs/{job_id}/restart")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "restart_initiated"
            assert data["reset_count"] == 3  # 2 failed + 1 skipped

        # Verify entities were reset
        async with SessionLocal() as session:
            # Check job status
            job = await session.get(ImportJob, job_id)
            assert job.status == JobStatus.running
            assert job.current_stage == "exporting"
            assert job.error_message is None

            # Check entities - failed/skipped should be pending now
            result = await session.execute(
                select(Entity).where(Entity.job_id == job_id)
            )
            entities = result.scalars().all()

            status_counts = {}
            for entity in entities:
                status = entity.status.value
                status_counts[status] = status_counts.get(status, 0) + 1

            assert status_counts.get(MappingStatus.pending.value, 0) == 3  # Reset
            assert status_counts.get(MappingStatus.success.value, 0) == 1  # Unchanged
            assert status_counts.get(MappingStatus.failed.value, 0) == 0  # Reset
            assert status_counts.get(MappingStatus.skipped.value, 0) == 0  # Reset

            # Verify error messages were cleared
            for entity in entities:
                if entity.status == MappingStatus.pending:
                    assert entity.error_message is None

    finally:
        # Cleanup
        async with SessionLocal() as session:
            from sqlalchemy import delete

            await session.execute(delete(Entity).where(Entity.job_id == job_id))
            await session.execute(delete(ImportJob).where(ImportJob.id == job_id))
            await session.commit()


@pytest.mark.asyncio
async def test_restart_running_job_fails():
    """Test that restarting a running job returns an error."""
    async with SessionLocal() as session:
        job = ImportJob(
            status=JobStatus.running,
            current_stage="exporting",
        )
        session.add(job)
        await session.flush()
        job_id = job.id
        await session.commit()

    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(f"/api/jobs/{job_id}/restart")
            assert response.status_code == 400
            data = response.json()
            assert "Cannot restart" in data["detail"]

    finally:
        async with SessionLocal() as session:
            from sqlalchemy import delete

            await session.execute(delete(ImportJob).where(ImportJob.id == job_id))
            await session.commit()


@pytest.mark.asyncio
async def test_restart_job_without_retryable_entities():
    """Test that restarting a job with no failed/skipped entities fails."""
    async with SessionLocal() as session:
        job = ImportJob(
            status=JobStatus.success,
            current_stage="done",
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        # Add only successful entities
        entity = Entity(
            job_id=job_id,
            entity_type="message",
            slack_id="msg1",
            status=MappingStatus.success,
        )
        session.add(entity)
        await session.commit()

    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(f"/api/jobs/{job_id}/restart")
            assert response.status_code == 400
            data = response.json()
            assert "No failed or skipped" in data["detail"]

    finally:
        async with SessionLocal() as session:
            from sqlalchemy import delete

            await session.execute(delete(Entity).where(Entity.job_id == job_id))
            await session.execute(delete(ImportJob).where(ImportJob.id == job_id))
            await session.commit()

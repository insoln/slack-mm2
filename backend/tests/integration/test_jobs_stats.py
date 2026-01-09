"""Integration test for /api/jobs stats field.

This test validates that the /api/jobs endpoint includes per-type export stats
in a structured format that matches /api/stats/mappings endpoint.
"""

import pytest
import os
from httpx import AsyncClient
from app.main import app
from app.models.base import SessionLocal
from app.models.import_job import ImportJob
from app.models.job_status_enum import JobStatus
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
from sqlalchemy import select


# Skip these tests if no database is available (e.g., in CI)
pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite:///")
    or not os.getenv("DATABASE_URL"),
    reason="Integration tests require PostgreSQL database",
)


@pytest.mark.asyncio
async def test_jobs_includes_stats_field():
    """Test that /api/jobs returns stats field with proper structure."""
    # Create a test job with entities in various statuses
    async with SessionLocal() as session:
        # Create a job
        job = ImportJob(
            status=JobStatus.running,
            current_stage="exporting",
            meta={"test": "stats_field"},
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        # Create entities with various statuses
        entities = [
            # Users: 2 success, 1 failed
            Entity(
                job_id=job_id,
                entity_type="user",
                slack_id="U001",
                status=MappingStatus.success,
                data={},
            ),
            Entity(
                job_id=job_id,
                entity_type="user",
                slack_id="U002",
                status=MappingStatus.success,
                data={},
            ),
            Entity(
                job_id=job_id,
                entity_type="user",
                slack_id="U003",
                status=MappingStatus.failed,
                data={},
            ),
            # Messages: 3 success, 1 pending, 1 skipped
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="M001",
                status=MappingStatus.success,
                data={},
            ),
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="M002",
                status=MappingStatus.success,
                data={},
            ),
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="M003",
                status=MappingStatus.success,
                data={},
            ),
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="M004",
                status=MappingStatus.pending,
                data={},
            ),
            Entity(
                job_id=job_id,
                entity_type="message",
                slack_id="M005",
                status=MappingStatus.skipped,
                data={},
            ),
            # Attachments: 1 success
            Entity(
                job_id=job_id,
                entity_type="attachment",
                slack_id="A001",
                status=MappingStatus.success,
                data={},
            ),
        ]
        session.add_all(entities)
        await session.commit()

        # Call /api/jobs endpoint
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jobs")
            assert response.status_code == 200
            data = response.json()
            
            # Find our test job
            jobs = data.get("jobs", [])
            test_job = None
            for j in jobs:
                if j["id"] == job_id:
                    test_job = j
                    break
            
            assert test_job is not None, "Test job not found in /api/jobs response"
            
            # Verify stats field exists
            assert "stats" in test_job, "stats field missing from job"
            stats = test_job["stats"]
            
            # Verify stats structure
            assert "types" in stats, "types field missing from stats"
            assert "statuses" in stats, "statuses field missing from stats"
            assert "matrix" in stats, "matrix field missing from stats"
            
            # Verify types list
            assert isinstance(stats["types"], list), "types should be a list"
            assert len(stats["types"]) > 0, "types list should not be empty"
            expected_types = ["user", "custom_emoji", "channel", "message", "attachment", "reaction"]
            assert stats["types"] == expected_types, f"Expected types {expected_types}, got {stats['types']}"
            
            # Verify statuses list
            assert isinstance(stats["statuses"], list), "statuses should be a list"
            assert stats["statuses"] == ["success", "failed", "skipped", "pending"], \
                f"Expected statuses in UI-friendly order, got {stats['statuses']}"
            
            # Verify matrix structure
            matrix = stats["matrix"]
            assert isinstance(matrix, dict), "matrix should be a dict"
            
            # Verify user counts
            assert "user" in matrix, "user type missing from matrix"
            user_counts = matrix["user"]
            assert user_counts["success"] == 2, f"Expected 2 success users, got {user_counts['success']}"
            assert user_counts["failed"] == 1, f"Expected 1 failed user, got {user_counts['failed']}"
            assert user_counts["skipped"] == 0, f"Expected 0 skipped users, got {user_counts['skipped']}"
            assert user_counts["pending"] == 0, f"Expected 0 pending users, got {user_counts['pending']}"
            
            # Verify message counts
            assert "message" in matrix, "message type missing from matrix"
            msg_counts = matrix["message"]
            assert msg_counts["success"] == 3, f"Expected 3 success messages, got {msg_counts['success']}"
            assert msg_counts["failed"] == 0, f"Expected 0 failed messages, got {msg_counts['failed']}"
            assert msg_counts["skipped"] == 1, f"Expected 1 skipped message, got {msg_counts['skipped']}"
            assert msg_counts["pending"] == 1, f"Expected 1 pending message, got {msg_counts['pending']}"
            
            # Verify attachment counts
            assert "attachment" in matrix, "attachment type missing from matrix"
            att_counts = matrix["attachment"]
            assert att_counts["success"] == 1, f"Expected 1 success attachment, got {att_counts['success']}"
            assert att_counts["failed"] == 0, f"Expected 0 failed attachments, got {att_counts['failed']}"
            
            # Verify types without entities have zero counts
            assert "custom_emoji" in matrix, "custom_emoji type missing from matrix"
            emoji_counts = matrix["custom_emoji"]
            assert all(emoji_counts[s] == 0 for s in stats["statuses"]), \
                f"Expected all zero counts for custom_emoji, got {emoji_counts}"
            
            # Also verify backward compatibility: export_status should still exist in meta
            meta = test_job.get("meta", {})
            assert "export_status" in meta, "export_status missing from meta (backward compatibility)"
            export_status = meta["export_status"]
            assert export_status["user"]["success"] == 2, "export_status should match stats"

        # Cleanup
        await session.delete(job)
        for entity in entities:
            await session.delete(entity)
        await session.commit()


@pytest.mark.asyncio
async def test_jobs_stats_totals_calculation():
    """Test that stats field enables correct total calculation without SSE."""
    # Create a completed job with mixed status entities
    async with SessionLocal() as session:
        job = ImportJob(
            status=JobStatus.success,
            current_stage="done",
            meta={"totals": {"messages": 10, "attachments": 5}},
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        # Create entities: some success, some failed, some skipped
        entities = [
            Entity(job_id=job_id, entity_type="message", slack_id=f"M{i:03d}",
                   status=MappingStatus.success if i < 7 else 
                          MappingStatus.failed if i < 9 else MappingStatus.skipped, 
                   data={})
            for i in range(10)
        ] + [
            Entity(job_id=job_id, entity_type="attachment", slack_id=f"A{i:03d}",
                   status=MappingStatus.success if i < 4 else MappingStatus.failed, 
                   data={})
            for i in range(5)
        ]
        session.add_all(entities)
        await session.commit()

        # Call /api/jobs endpoint
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jobs")
            assert response.status_code == 200
            data = response.json()
            
            # Find our test job
            jobs = data.get("jobs", [])
            test_job = next((j for j in jobs if j["id"] == job_id), None)
            assert test_job is not None
            
            # Extract stats
            stats = test_job["stats"]
            matrix = stats["matrix"]
            
            # Calculate totals from stats (simulating frontend logic)
            success_total = sum(matrix[t]["success"] for t in ["message", "attachment"] if t in matrix)
            failed_total = sum(matrix[t]["failed"] for t in ["message", "attachment"] if t in matrix)
            skipped_total = sum(matrix[t]["skipped"] for t in ["message", "attachment"] if t in matrix)
            completed_total = success_total + failed_total + skipped_total
            
            # Verify calculations
            assert success_total == 11, f"Expected 11 total successes (7 msg + 4 att), got {success_total}"
            assert failed_total == 3, f"Expected 3 total failures (2 msg + 1 att), got {failed_total}"
            assert skipped_total == 1, f"Expected 1 skipped, got {skipped_total}"
            assert completed_total == 15, f"Expected 15 completed total, got {completed_total}"
            
            # Verify we can calculate correct percentage immediately (no SSE needed)
            # This is the key acceptance criterion: no "success > total" on first render
            assert success_total <= completed_total, \
                f"Success count {success_total} should not exceed total {completed_total}"

        # Cleanup
        await session.delete(job)
        for entity in entities:
            await session.delete(entity)
        await session.commit()

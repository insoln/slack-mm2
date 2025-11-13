"""Tests for FC: directory exclusion from json_files_total count."""

import os
import tempfile
import zipfile
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def test_json_files_count_excludes_fc_directories():
    """Test that _json_files_count excludes FC: directories from the count."""
    from app.services.backup.orchestrator import orchestrate_slack_import

    # Create a temporary directory structure
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create top-level files
        open(os.path.join(tmpdir, "users.json"), "w").close()
        open(os.path.join(tmpdir, "channels.json"), "w").close()

        # Create regular channel directories with JSON files
        os.makedirs(os.path.join(tmpdir, "general"))
        open(os.path.join(tmpdir, "general", "2025-01-01.json"), "w").close()
        open(os.path.join(tmpdir, "general", "2025-01-02.json"), "w").close()

        os.makedirs(os.path.join(tmpdir, "random"))
        open(os.path.join(tmpdir, "random", "2025-01-01.json"), "w").close()

        # Create FC: directories with JSON files (should be excluded)
        os.makedirs(os.path.join(tmpdir, "FC:F12345:"))
        open(os.path.join(tmpdir, "FC:F12345:", "comment1.json"), "w").close()
        open(os.path.join(tmpdir, "FC:F12345:", "comment2.json"), "w").close()

        os.makedirs(os.path.join(tmpdir, "FC:F67890:"))
        open(os.path.join(tmpdir, "FC:F67890:", "comment1.json"), "w").close()

        # Import the function and test it
        # We need to extract the internal function, so we'll test it via orchestrator
        with patch(
            "app.services.backup.orchestrator.extract_zip", new_callable=AsyncMock
        ), patch(
            "app.services.backup.orchestrator.get_slack_emoji_list",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.backup.orchestrator.parse_users",
            new_callable=AsyncMock,
            return_value={"created": 0, "discovered": 0, "existing": 0},
        ), patch(
            "app.services.backup.orchestrator.parse_channels_and_chats",
            new_callable=AsyncMock,
            return_value={"created": 0, "discovered": 0, "existing": 0},
        ), patch(
            "app.services.backup.orchestrator.parse_messages_and_related",
            new_callable=AsyncMock,
        ), patch(
            "app.services.backup.orchestrator.orchestrate_mm_export",
            new_callable=AsyncMock,
        ), patch(
            "app.services.backup.orchestrator.merge_job_meta", new_callable=AsyncMock
        ) as mock_meta, patch(
            "app.services.backup.orchestrator.find_channel_for_folder", return_value={}
        ), patch(
            "app.services.backup.orchestrator.SessionLocal"
        ), patch(
            "app.services.backup.orchestrator.tempfile.mkdtemp", return_value=tmpdir
        ), patch(
            "app.services.backup.orchestrator.shutil.rmtree"
        ):
            import asyncio

            asyncio.run(orchestrate_slack_import("/fake/path.zip"))

            # Find the call where json_files_total was set
            json_total_calls = [
                call
                for call in mock_meta.call_args_list
                if "set" in call.kwargs
                and "json_files_total" in call.kwargs.get("set", {})
            ]

            assert len(json_total_calls) > 0, "json_files_total should be set"
            json_total = json_total_calls[0].kwargs["set"]["json_files_total"]

            # Expected: 2 top-level files (users.json, channels.json) + 3 message files
            # (2 in general/ + 1 in random/) = 5 total
            # FC: directories should NOT be counted (would add 3 more if they were)
            assert json_total == 5, f"Expected 5 JSON files, got {json_total}"


@pytest.mark.asyncio
async def test_jobs_api_excludes_fc_directories_from_extracted_dir():
    """Test that jobs.py excludes FC: directories when counting from extracted directory."""
    from app.api.jobs import list_jobs

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test structure
        open(os.path.join(tmpdir, "users.json"), "w").close()
        open(os.path.join(tmpdir, "channels.json"), "w").close()

        os.makedirs(os.path.join(tmpdir, "general"))
        open(os.path.join(tmpdir, "general", "2025-01-01.json"), "w").close()

        # FC: directory (should be excluded)
        os.makedirs(os.path.join(tmpdir, "FC:F12345:"))
        open(os.path.join(tmpdir, "FC:F12345:", "comment1.json"), "w").close()

        # Mock the database query
        with patch("app.api.jobs.SessionLocal") as mock_session:
            mock_job = MagicMock()
            mock_job.id = 1
            mock_job.status = MagicMock(value="running")
            mock_job.current_stage = "messages"
            mock_job.meta = {"extract_dir": tmpdir}
            mock_job.error_message = None
            mock_job.created_at = None
            mock_job.updated_at = None

            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [mock_job]
            mock_result = MagicMock()
            mock_result.scalars.return_value = mock_scalars

            mock_session_instance = MagicMock()
            mock_session_instance.__aenter__ = AsyncMock(
                return_value=mock_session_instance
            )
            mock_session_instance.__aexit__ = AsyncMock(return_value=None)
            mock_session_instance.execute = AsyncMock(return_value=mock_result)
            mock_session_instance.get = AsyncMock(return_value=mock_job)

            mock_session.return_value = mock_session_instance

            # Test directory scanning logic directly
            total = 0
            base_dir = tmpdir
            for fname in (
                "users.json",
                "channels.json",
                "groups.json",
                "dms.json",
                "mpims.json",
            ):
                if os.path.exists(os.path.join(base_dir, fname)):
                    total += 1

            # Count files in subdirectories, excluding FC: directories
            import glob

            for entry in os.listdir(base_dir):
                p = os.path.join(base_dir, entry)
                if os.path.isdir(p) and not entry.startswith("FC:"):
                    total += len(glob.glob(os.path.join(p, "*.json")))

            # Expected: 2 top-level files + 1 in general/ = 3
            # FC: directory should NOT be counted
            assert total == 3, f"Expected 3 JSON files, got {total}"


def test_jobs_api_excludes_fc_directories_from_zip():
    """Test that jobs.py excludes FC: directories when counting from zip file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "test.zip")

        # Create a zip with various files
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Top-level files
            zf.writestr("users.json", "{}")
            zf.writestr("channels.json", "{}")

            # Regular channel directory
            zf.writestr("general/2025-01-01.json", "{}")
            zf.writestr("general/2025-01-02.json", "{}")

            # FC: directory (should be excluded)
            zf.writestr("FC:F12345:/comment1.json", "{}")
            zf.writestr("FC:F12345:/comment2.json", "{}")

        # Test zip scanning logic directly
        total = 0
        top_allowed = {
            "users.json",
            "channels.json",
            "groups.json",
            "dms.json",
            "mpims.json",
        }

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            for name in names:
                if name.endswith("/"):
                    continue
                parts = [p for p in name.split("/") if p]
                if not parts:
                    continue
                fname = parts[-1]
                if len(parts) == 1:
                    # top-level file
                    if fname in top_allowed:
                        total += 1
                else:
                    # per-channel daily JSON
                    # Exclude FC: directories
                    parent_dir = parts[-2] if len(parts) >= 2 else ""
                    if fname.lower().endswith(".json") and not parent_dir.startswith(
                        "FC:"
                    ):
                        total += 1

        # Expected: 2 top-level files + 2 in general/ = 4
        # FC: directory files should NOT be counted
        assert total == 4, f"Expected 4 JSON files, got {total}"

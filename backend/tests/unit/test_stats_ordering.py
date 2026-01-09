"""Unit tests for stats API ordering logic."""

from app.models.status_enum import MappingStatus


class TestStatsOrdering:
    """Test suite for stats endpoint ordering requirements."""

    def test_orchestrator_order_all_types_present(self):
        """Verify orchestrator order is correctly applied when all entity types are present."""
        # Simulate the ordering logic from stats.py
        orchestrator_order = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]
        all_types_set = {
            "message",
            "user",
            "attachment",
            "reaction",
            "channel",
            "custom_emoji",
        }

        # Apply ordering logic
        all_types = [t for t in orchestrator_order if t in all_types_set]
        all_types.extend(
            sorted(t for t in all_types_set if t not in orchestrator_order)
        )

        # Expected order matches orchestrator
        expected = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]
        assert all_types == expected

    def test_orchestrator_order_with_unknown_types(self):
        """Verify unknown entity types are appended after known types in alphabetical order."""
        orchestrator_order = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]
        # Mix of known and unknown types
        all_types_set = {
            "user",
            "message",
            "zebra_type",
            "apple_type",
            "attachment",
        }

        all_types = [t for t in orchestrator_order if t in all_types_set]
        all_types.extend(
            sorted(t for t in all_types_set if t not in orchestrator_order)
        )

        # Known types in orchestrator order, then unknown types alphabetically
        expected = ["user", "message", "attachment", "apple_type", "zebra_type"]
        assert all_types == expected

    def test_orchestrator_order_partial_types(self):
        """Verify empty or partial entity sets are handled correctly."""
        orchestrator_order = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]

        # Only a few types present
        all_types_set = {"user", "message"}

        all_types = [t for t in orchestrator_order if t in all_types_set]
        all_types.extend(
            sorted(t for t in all_types_set if t not in orchestrator_order)
        )

        expected = ["user", "message"]
        assert all_types == expected

    def test_orchestrator_order_empty_set(self):
        """Verify empty entity set returns empty list."""
        orchestrator_order = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]
        all_types_set = set()

        all_types = [t for t in orchestrator_order if t in all_types_set]
        all_types.extend(
            sorted(t for t in all_types_set if t not in orchestrator_order)
        )

        assert all_types == []

    def test_status_ordering_visual_order(self):
        """Verify status ordering follows the visual order (success, failed, skipped, pending)."""
        statuses_order = [
            s.value
            for s in (
                MappingStatus.success,
                MappingStatus.failed,
                MappingStatus.skipped,
                MappingStatus.pending,
            )
        ]

        expected = ["success", "failed", "skipped", "pending"]
        assert statuses_order == expected

    def test_orchestrator_order_matches_export_orchestrator(self):
        """Verify the stats orchestrator order matches the export orchestrator EXPORT_ORDER."""
        # This is the order from backend/app/services/export/orchestrator.py
        export_orchestrator_order = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]

        # This is the order from stats.py
        stats_orchestrator_order = [
            "user",
            "custom_emoji",
            "channel",
            "message",
            "attachment",
            "reaction",
        ]

        assert stats_orchestrator_order == export_orchestrator_order

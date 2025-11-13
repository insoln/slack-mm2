"""002_add_perf_indexes_reactions

Adds performance indexes to optimize reaction integrity checks and export queries.

This migration addresses slow NOT IN queries during the post-import integrity check
phase by adding:

1. Composite index on entities(entity_type, job_id, id) for faster filtering
2. Indexes on entity_relations for relation_type checks (reacted_by, reacted_to)

All indexes are created with CONCURRENTLY to avoid blocking production workloads.

Revision ID: 002_add_perf_indexes_reactions
Revises: 001_initial_full_schema
Create Date: 2025-11-13

"""

from alembic import op
from alembic import context

revision = "002_add_perf_indexes_reactions"
down_revision = "001_initial_full_schema"
branch_labels = None
depends_on = None


def upgrade():
    """Add performance indexes using CONCURRENTLY to avoid locks."""
    # PostgreSQL requires autocommit mode for CREATE INDEX CONCURRENTLY
    connection = op.get_bind()

    # Check if we're in a transaction (online mode)
    if not context.is_offline_mode():
        # CONCURRENTLY requires autocommit
        connection.execute("COMMIT")

        # Index 1: Composite index for entities filtering by type and job
        # Supports queries like: WHERE entity_type='reaction' AND job_id=X
        connection.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_entities_type_job_id
            ON entities(entity_type, job_id, id)
            """
        )

        # Index 2: entity_relations filtering by relation_type and from_entity_id
        # Supports: WHERE relation_type='reacted_to' AND from_entity_id IN (...)
        connection.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_er_reltype_from
            ON entity_relations(relation_type, from_entity_id)
            """
        )

        # Index 3: entity_relations filtering by relation_type and to_entity_id
        # Supports: WHERE relation_type='reacted_by' AND to_entity_id IN (...)
        connection.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_er_reltype_to
            ON entity_relations(relation_type, to_entity_id)
            """
        )

        # Optional: Additional index for export queries
        # Supports: WHERE entity_type='X' AND status='pending'
        connection.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_entities_type_status
            ON entities(entity_type, status)
            """
        )
    else:
        # Offline mode: create indexes without CONCURRENTLY
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entities_type_job_id
            ON entities(entity_type, job_id, id)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_er_reltype_from
            ON entity_relations(relation_type, from_entity_id)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_er_reltype_to
            ON entity_relations(relation_type, to_entity_id)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entities_type_status
            ON entities(entity_type, status)
            """
        )


def downgrade():
    """Remove the performance indexes."""
    op.execute("DROP INDEX IF EXISTS idx_entities_type_job_id")
    op.execute("DROP INDEX IF EXISTS idx_er_reltype_from")
    op.execute("DROP INDEX IF EXISTS idx_er_reltype_to")
    op.execute("DROP INDEX IF EXISTS idx_entities_type_status")

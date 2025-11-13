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
    """Add performance indexes using CONCURRENTLY to avoid locks.

    Implements optimization per PR #25 (reaction integrity checks). Uses Alembic's
    autocommit_block + postgresql_concurrently=True for minimal locking. Offline
    mode falls back to regular CREATE INDEX.
    """
    if not context.is_offline_mode():
        # Use Alembic's autocommit_block for PostgreSQL CONCURRENTLY
        with op.get_context().autocommit_block():
            op.create_index(
                "idx_entities_type_job_id",
                "entities",
                ["entity_type", "job_id", "id"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
            op.create_index(
                "idx_er_reltype_from",
                "entity_relations",
                ["relation_type", "from_entity_id"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
            op.create_index(
                "idx_er_reltype_to",
                "entity_relations",
                ["relation_type", "to_entity_id"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
            op.create_index(
                "idx_entities_type_status",
                "entities",
                ["entity_type", "status"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        # Offline (no DB connection) fallback without CONCURRENTLY
        op.create_index(
            "idx_entities_type_job_id",
            "entities",
            ["entity_type", "job_id", "id"],
            unique=False,
            if_not_exists=True,
        )
        op.create_index(
            "idx_er_reltype_from",
            "entity_relations",
            ["relation_type", "from_entity_id"],
            unique=False,
            if_not_exists=True,
        )
        op.create_index(
            "idx_er_reltype_to",
            "entity_relations",
            ["relation_type", "to_entity_id"],
            unique=False,
            if_not_exists=True,
        )
        op.create_index(
            "idx_entities_type_status",
            "entities",
            ["entity_type", "status"],
            unique=False,
            if_not_exists=True,
        )


def downgrade():
    """Remove the performance indexes.

    Use autocommit_block with concurrent drop where supported; otherwise raw SQL.
    """
    if not context.is_offline_mode():
        with op.get_context().autocommit_block():
            try:
                op.drop_index(
                    "idx_entities_type_job_id",
                    table_name="entities",
                    postgresql_concurrently=True,
                    if_exists=True,
                )
                op.drop_index(
                    "idx_er_reltype_from",
                    table_name="entity_relations",
                    postgresql_concurrently=True,
                    if_exists=True,
                )
                op.drop_index(
                    "idx_er_reltype_to",
                    table_name="entity_relations",
                    postgresql_concurrently=True,
                    if_exists=True,
                )
                op.drop_index(
                    "idx_entities_type_status",
                    table_name="entities",
                    postgresql_concurrently=True,
                    if_exists=True,
                )
            except Exception:
                # Fallback raw SQL if Alembic/PG version doesn't support concurrent drop options
                op.execute("DROP INDEX IF EXISTS idx_entities_type_job_id")
                op.execute("DROP INDEX IF EXISTS idx_er_reltype_from")
                op.execute("DROP INDEX IF EXISTS idx_er_reltype_to")
                op.execute("DROP INDEX IF EXISTS idx_entities_type_status")
    else:
        op.execute("DROP INDEX IF EXISTS idx_entities_type_job_id")
        op.execute("DROP INDEX IF EXISTS idx_er_reltype_from")
        op.execute("DROP INDEX IF EXISTS idx_er_reltype_to")
        op.execute("DROP INDEX IF EXISTS idx_entities_type_status")

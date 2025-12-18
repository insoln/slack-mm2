"""003_add_cascade_deletion_indexes

Adds standalone indexes on entity_relations.from_entity_id and to_entity_id to optimize
cascade deletions and FK constraint checks when filtering without relation_type.

Context:
  The existing composite indexes (relation_type, from_entity_id) and
  (relation_type, to_entity_id) do not efficiently support queries that filter
  only by from_entity_id or to_entity_id (common in CASCADE DELETE operations).
  PostgreSQL cannot use these composite indexes effectively for such queries.

Performance impact:
  - Cascade deletion of ~185k message job previously timed out
  - After adding these indexes: batch deletions (2k messages + pre-delete relations)
    complete in ~2.3s
  - Larger batches (20k messages) complete in ~5.5-5.8s without lock contention

Indexes created:
  - idx_er_from on entity_relations(from_entity_id)
  - idx_er_to on entity_relations(to_entity_id)

Both indexes are created CONCURRENTLY to avoid blocking production workloads.

Revision ID: 003_add_cascade_deletion_indexes
Revises: 002_add_perf_indexes_reactions
Create Date: 2025-12-18

"""

from alembic import op
from alembic import context

revision = "003_add_cascade_deletion_indexes"
down_revision = "002_add_perf_indexes_reactions"
branch_labels = None
depends_on = None


def upgrade():
    """Add cascade deletion performance indexes using CONCURRENTLY to avoid locks.

    Creates standalone indexes on from_entity_id and to_entity_id columns to
    support efficient cascade deletion queries. Uses Alembic's autocommit_block
    with postgresql_concurrently=True for minimal locking. Offline mode falls
    back to regular CREATE INDEX.
    """
    if not context.is_offline_mode():
        # Use Alembic's autocommit_block for PostgreSQL CONCURRENTLY
        with op.get_context().autocommit_block():
            op.create_index(
                "idx_er_from",
                "entity_relations",
                ["from_entity_id"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
            op.create_index(
                "idx_er_to",
                "entity_relations",
                ["to_entity_id"],
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        # Offline (no DB connection) fallback without CONCURRENTLY
        op.create_index(
            "idx_er_from",
            "entity_relations",
            ["from_entity_id"],
            unique=False,
            if_not_exists=True,
        )
        op.create_index(
            "idx_er_to",
            "entity_relations",
            ["to_entity_id"],
            unique=False,
            if_not_exists=True,
        )


def downgrade():
    """Remove the cascade deletion performance indexes.

    Use autocommit_block with concurrent drop where supported; otherwise raw SQL.
    """
    if not context.is_offline_mode():
        with op.get_context().autocommit_block():
            try:
                op.drop_index(
                    "idx_er_from",
                    table_name="entity_relations",
                    postgresql_concurrently=True,
                    if_exists=True,
                )
                op.drop_index(
                    "idx_er_to",
                    table_name="entity_relations",
                    postgresql_concurrently=True,
                    if_exists=True,
                )
            except Exception:
                # Fallback raw SQL if Alembic/PG version doesn't support concurrent drop options
                op.execute("DROP INDEX IF EXISTS idx_er_from")
                op.execute("DROP INDEX IF EXISTS idx_er_to")
    else:
        op.execute("DROP INDEX IF EXISTS idx_er_from")
        op.execute("DROP INDEX IF EXISTS idx_er_to")

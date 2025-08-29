"""performance indexes for entities and relations

Revision ID: 007_perf_indexes
Revises: 006_add_job_id_scoping
Create Date: 2025-08-28
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "007_perf_indexes"
down_revision = "006_add_job_id_scoping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use IF NOT EXISTS to be idempotent if indexes were created manually earlier
    op.execute(
        """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'i' AND c.relname = 'ix_entities_job_type_status'
        ) THEN
            CREATE INDEX ix_entities_job_type_status ON entities (job_id, entity_type, status);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'i' AND c.relname = 'ix_entities_job_slack'
        ) THEN
            CREATE INDEX ix_entities_job_slack ON entities (job_id, entity_type, slack_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'i' AND c.relname = 'ix_entities_type_slack'
        ) THEN
            CREATE INDEX ix_entities_type_slack ON entities (entity_type, slack_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'i' AND c.relname = 'ix_rel_type_from'
        ) THEN
            CREATE INDEX ix_rel_type_from ON entity_relations (relation_type, from_entity_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'i' AND c.relname = 'ix_rel_type_to'
        ) THEN
            CREATE INDEX ix_rel_type_to ON entity_relations (relation_type, to_entity_id);
        END IF;
    END$$;
    """
    )


def downgrade() -> None:
    op.execute(
        """
    DROP INDEX IF EXISTS ix_rel_type_to;
    DROP INDEX IF EXISTS ix_rel_type_from;
    DROP INDEX IF EXISTS ix_entities_type_slack;
    DROP INDEX IF EXISTS ix_entities_job_slack;
    DROP INDEX IF EXISTS ix_entities_job_type_status;
    """
    )

"""001_initial_full_schema

Single baseline migration collapsing previous history. Applies the final schema:

Tables:
  import_jobs
  entities
  entity_relations

Enums:
  mapping_status (pending, skipped, failed, success)
  job_status (queued, running, success, failed, canceled)

Key constraints / indexes:
  UNIQUE (entity_type, slack_id) on entities (global uniqueness)
  Functional / partial indexes:
    idx_entities_user_username (raw_data->>'username') WHERE entity_type='user'
    idx_reactions_message_ts ((raw_data->>'message_ts')) WHERE entity_type='reaction'
    idx_reactions_composite_id ((raw_data->>'composite_id')) WHERE entity_type='reaction'
  Performance indexes:
    ix_entities_job_type_status ON entities(job_id, entity_type, status)
    idx_entities_job_id ON entities(job_id)
    ix_rel_type_from ON entity_relations(relation_type, from_entity_id)
    ix_rel_type_to   ON entity_relations(relation_type, to_entity_id)
    idx_entity_relations_job_id ON entity_relations(job_id)
  Relation uniqueness:
    ux_entity_relations_unique ON entity_relations(from_entity_id, to_entity_id, relation_type)

Destructive note:
  This reset removes all prior incremental migrations. Existing deployments must
  either (a) recreate the database or (b) manually align their schema to match
  this baseline before marking Alembic as up-to-date (e.g. stamping).
"""

from alembic import op

revision = "001_initial_full_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():  # noqa: D401
    # Create enums if not exist
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'mapping_status') THEN
                CREATE TYPE mapping_status AS ENUM ('pending', 'skipped', 'failed', 'success');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_status') THEN
                CREATE TYPE job_status AS ENUM ('queued', 'running', 'success', 'failed', 'canceled');
            END IF;
        END$$;
        """
    )

    # import_jobs table
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS import_jobs (
            id BIGSERIAL PRIMARY KEY,
            status job_status NOT NULL DEFAULT 'queued',
            current_stage TEXT,
            meta JSONB,
            error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """
    )

    # entities table (global uniqueness on (entity_type, slack_id))
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id BIGSERIAL PRIMARY KEY,
            entity_type TEXT NOT NULL,
            slack_id TEXT NOT NULL,
            mattermost_id TEXT,
            raw_data JSONB,
            job_id BIGINT REFERENCES import_jobs(id) ON DELETE CASCADE,
            status mapping_status NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_type_slack
        ON entities(entity_type, slack_id);
        """
    )

    # entity_relations table + indexes
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_relations (
            id BIGSERIAL PRIMARY KEY,
            from_entity_id BIGINT REFERENCES entities(id) ON DELETE CASCADE,
            to_entity_id BIGINT REFERENCES entities(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL,
            job_id BIGINT REFERENCES import_jobs(id) ON DELETE CASCADE,
            raw_data JSONB,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_relations_unique
        ON entity_relations(from_entity_id, to_entity_id, relation_type);
        """
    )

    # Functional / partial indexes
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entities_user_username
        ON entities ((raw_data->>'username'))
        WHERE entity_type = 'user';

        CREATE INDEX IF NOT EXISTS idx_reactions_message_ts
        ON entities ((raw_data->>'message_ts'))
        WHERE entity_type = 'reaction';

        CREATE INDEX IF NOT EXISTS idx_reactions_composite_id
        ON entities ((raw_data->>'composite_id'))
        WHERE entity_type = 'reaction';
        """
    )

    # Performance indexes
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_entities_job_type_status ON entities (job_id, entity_type, status);
        CREATE INDEX IF NOT EXISTS idx_entities_job_id ON entities (job_id);
        CREATE INDEX IF NOT EXISTS ix_rel_type_from ON entity_relations (relation_type, from_entity_id);
        CREATE INDEX IF NOT EXISTS ix_rel_type_to   ON entity_relations (relation_type, to_entity_id);
        CREATE INDEX IF NOT EXISTS idx_entity_relations_job_id ON entity_relations (job_id);
        """
    )


def downgrade():  # noqa: D401, pragma: no cover
    # Drop tables (will cascade indexes). Keep enums to avoid issues with other DB objects.
    op.execute("DROP TABLE IF EXISTS entity_relations CASCADE;")
    op.execute("DROP TABLE IF EXISTS entities CASCADE;")
    op.execute("DROP TABLE IF EXISTS import_jobs CASCADE;")
    # Enums intentionally preserved.

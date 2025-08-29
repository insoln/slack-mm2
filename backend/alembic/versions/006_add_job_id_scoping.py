"""
006_add_job_id_scoping

Add job_id scoping to entities and entity_relations to support parallel import jobs.
 - entities.job_id -> FK import_jobs(id), nullable for legacy rows
 - entity_relations.job_id -> FK import_jobs(id), nullable
 - indexes to speed up queries
 - partial unique index on (entity_type, slack_id, job_id) when job_id IS NOT NULL
"""

from alembic import op

revision = "006_add_job_id_scoping"
down_revision = "004_import_jobs_post"
branch_labels = None
depends_on = None


def upgrade():
    # Add job_id columns
    op.execute(
        """
        ALTER TABLE entities
        ADD COLUMN IF NOT EXISTS job_id BIGINT REFERENCES import_jobs(id) ON DELETE CASCADE;

        ALTER TABLE entity_relations
        ADD COLUMN IF NOT EXISTS job_id BIGINT REFERENCES import_jobs(id) ON DELETE CASCADE;

        -- Helpful indexes
        CREATE INDEX IF NOT EXISTS idx_entities_job_id ON entities(job_id);
        CREATE INDEX IF NOT EXISTS idx_entity_relations_job_id ON entity_relations(job_id);

        -- Unique within a job: allow duplicates across jobs; ignore legacy NULL job_id rows
        CREATE UNIQUE INDEX IF NOT EXISTS ux_entities_type_slackid_job
        ON entities(entity_type, slack_id, job_id)
        WHERE job_id IS NOT NULL;
        """
    )


def downgrade():
    op.execute(
        """
        DROP INDEX IF EXISTS ux_entities_type_slackid_job;
        DROP INDEX IF EXISTS idx_entity_relations_job_id;
        DROP INDEX IF EXISTS idx_entities_job_id;
        ALTER TABLE entity_relations DROP COLUMN IF EXISTS job_id;
        ALTER TABLE entities DROP COLUMN IF EXISTS job_id;
        """
    )

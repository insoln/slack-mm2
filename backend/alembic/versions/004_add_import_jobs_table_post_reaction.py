"""
004_import_jobs_post

Create import_jobs table and job_status enum after merge, safe if already created.
"""

from alembic import op

revision = "004_import_jobs_post"
down_revision = "005_merge_heads_after_dual_003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_status') THEN
                CREATE TYPE job_status AS ENUM ('queued', 'running', 'success', 'failed', 'canceled');
            END IF;
        END$$;

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


def downgrade():
    op.execute("DROP TABLE IF EXISTS import_jobs;")
    # keep enum type

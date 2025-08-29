"""
003_add_import_jobs_table

Добавление таблицы import_jobs и enum job_status.
"""

from alembic import op

revision = "003_add_import_jobs_table"
down_revision = "002_add_username_index"
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
    # keep enum around to avoid breaking if other rows reference it

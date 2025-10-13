"""
003_add_entity_uniqueness_index

Добавляет уникальный индекс для защиты от дубликатов по (entity_type, slack_id, job_id).
Если job_id NULL (теоретически), отдельная комбинация также уникальна.
"""

from alembic import op

revision = "003_add_entity_uniqueness_index"
down_revision = "002_add_username_index"
branch_labels = None
depends_on = None


def upgrade():
    # На свежей БД колонка job_id добавляется значительно позже (см. 006_add_job_id_scoping).
    # Поэтому здесь создаём индекс ТОЛЬКО если колонка уже существует (например, при апгрейде существующей установки).
    # Для fresh install индекс создастся гарантированно в 010_merge_uniqueness_branch (после добавления job_id).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                 WHERE table_name = 'entities' AND column_name = 'job_id'
            ) THEN
                CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_type_slack_job
                ON entities (entity_type, slack_id, job_id);
            END IF;
        END$$;
        """
    )


def downgrade():
    op.execute(
        """
        DROP INDEX IF EXISTS uq_entities_type_slack_job;
        """
    )

"""
001_init_universal_schema

Создание универсальной схемы для Slack-MM2 Sync
"""

from alembic import op
import pathlib
import os

revision = "001_init_universal_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "../../infra/db/migrations/001_init_universal_schema.sql",
        )
    )
    with open(sql_path, encoding="utf-8") as f:
        op.execute(f.read())


def downgrade():
    op.execute("DROP TABLE IF EXISTS entity_relations CASCADE;")
    op.execute("DROP TABLE IF EXISTS entities CASCADE;")

"""
002_add_username_index

Добавление функционального индекса для поиска пользователей по username в raw_data.
"""

from alembic import op
import sqlalchemy as sa

revision = "002_add_username_index"
down_revision = "001_init_universal_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entities_user_username 
        ON entities ((raw_data->>'username')) 
        WHERE entity_type = 'user';
        """
    )


def downgrade():
    op.execute(
        """
        DROP INDEX IF EXISTS idx_entities_user_username;
        """
    )

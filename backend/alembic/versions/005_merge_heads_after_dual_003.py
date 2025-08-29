"""
005_merge_heads_after_dual_003

Merge heads 003_add_import_jobs_table and 003_reaction_fields into a single linear history.
"""

from alembic import op

revision = "005_merge_heads_after_dual_003"
down_revision = ("003_add_import_jobs_table", "003_reaction_fields")
branch_labels = None
depends_on = None


def upgrade():
    # merge point; nothing to do
    pass


def downgrade():
    # cannot un-merge easily
    pass

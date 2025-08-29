"""
009_merge_stub_into_head

Merge heads 008_merge_perf_into_main and 003_perf_indexes into a single head.
This resolves the leftover legacy stub branch so that "alembic upgrade head"
works without requiring branch-specific targeting.
"""

from alembic import op

revision = "009_merge_stub_into_head"
down_revision = ("008_merge_perf_into_main", "003_perf_indexes")
branch_labels = None
depends_on = None


def upgrade():
    # merge point; nothing to do
    pass


def downgrade():
    # cannot un-merge easily
    pass

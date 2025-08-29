"""
008_merge_perf_into_main

Merge heads 006_add_job_id_scoping and 007_perf_indexes into a single head.
"""

from alembic import op

revision = "008_merge_perf_into_main"
down_revision = ("006_add_job_id_scoping", "007_perf_indexes")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

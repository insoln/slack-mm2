"""
010_merge_uniqueness_branch

Merge remaining head 003_add_entity_uniqueness_index into main lineage (head 009_merge_stub_into_head).
Also normalise uniqueness indexes:
 - Keep global unique index uq_entities_type_slack_job (covers NULL and non-NULL job_id)
 - Drop legacy partial unique index ux_entities_type_slackid_job if both coexist (redundant)

Result: a single Alembic head so `alembic upgrade head` works without specifying branches.
Future migrations MUST set down_revision = "010_merge_uniqueness_branch".
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "010_merge_uniqueness_branch"
# Merge the two heads: main (after 009) and uniqueness side-branch 003_add_entity_uniqueness_index
# NOTE: 009_merge_stub_into_head already merged other divergent branches.
down_revision = ("009_merge_stub_into_head", "003_add_entity_uniqueness_index")
branch_labels = None
depends_on = None


def upgrade():
    # Safe normalisation: ensure desired global unique index exists, drop redundant partial one.
    op.execute(
        """
        -- Drop legacy partial unique index if it exists; full index supersedes it
        DROP INDEX IF EXISTS ux_entities_type_slackid_job;
        -- Recreate (idempotent) the full unique index used by application logic
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_type_slack_job
        ON entities (entity_type, slack_id, job_id);
        """
    )


def downgrade():  # pragma: no cover
    # We intentionally do not re-create the dropped partial index to avoid resurrecting dual index state.
    pass

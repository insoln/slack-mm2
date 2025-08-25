"""
003_reaction_fields_and_relations_uniq

Backfill reaction convenience fields (message_ts, emoji_name, composite_id),
add indexes for faster lookups, and deduplicate/enforce uniqueness of
entity_relations on (from_entity_id, to_entity_id, relation_type).
"""

from alembic import op

revision = "003_reaction_fields"
down_revision = "002_add_username_index"
branch_labels = None
depends_on = None


def upgrade():
    # Backfill reaction fields from slack_id pattern: "<ts>_<name>_<user>"
    op.execute(
        r"""
        UPDATE entities
        SET raw_data = jsonb_set(
            jsonb_set(
                jsonb_set(COALESCE(raw_data, '{}'::jsonb),
                    '{message_ts}', to_jsonb(split_part(slack_id, '_', 1))
                ),
                '{emoji_name}', to_jsonb(NULLIF(split_part(slack_id, '_', 2), ''))
            ),
            '{composite_id}', to_jsonb(split_part(slack_id, '_', 1) || '_' || split_part(slack_id, '_', 2))
        )
        WHERE entity_type = 'reaction'
          AND (
            raw_data->>'message_ts' IS NULL OR
            raw_data->>'emoji_name' IS NULL OR
            raw_data->>'composite_id' IS NULL
          );
        """
    )

    # Helpful indexes for reaction lookups by ts/composite_id
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reactions_message_ts
        ON entities ((raw_data->>'message_ts'))
        WHERE entity_type = 'reaction';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reactions_composite_id
        ON entities ((raw_data->>'composite_id'))
        WHERE entity_type = 'reaction';
        """
    )

    # Deduplicate entity_relations rows prior to adding uniqueness constraint
    op.execute(
        """
        DELETE FROM entity_relations a
        USING entity_relations b
        WHERE a.id > b.id
          AND a.from_entity_id = b.from_entity_id
          AND a.to_entity_id = b.to_entity_id
          AND a.relation_type = b.relation_type;
        """
    )

    # Enforce uniqueness of entity_relations by (from, to, type)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_relations_unique
        ON entity_relations(from_entity_id, to_entity_id, relation_type);
        """
    )


def downgrade():
    # Drop unique index (cannot easily un-backfill JSON fields)
    op.execute("DROP INDEX IF EXISTS ux_entity_relations_unique;")
    op.execute("DROP INDEX IF EXISTS idx_reactions_message_ts;")
    op.execute("DROP INDEX IF EXISTS idx_reactions_composite_id;")

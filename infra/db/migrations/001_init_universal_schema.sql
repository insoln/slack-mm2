-- 001_init_universal_schema.sql
-- Универсальная схема для Slack-MM2 Sync
-- status: pending (подлежит экспорту), skipped (не подлежит экспорту), failed (экспорт не удался), success (экспорт удался)

CREATE TYPE mapping_status AS ENUM ('pending', 'skipped', 'failed', 'success');

CREATE TABLE entities (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL, -- user, channel, message, file, emoji, ...
    slack_id TEXT NOT NULL,
    mattermost_id TEXT,
    raw_data JSONB,
    status mapping_status NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX idx_entities_type_slackid ON entities(entity_type, slack_id);

CREATE TABLE entity_relations (
    id BIGSERIAL PRIMARY KEY,
    from_entity_id BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    to_entity_id BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL, -- member_of, posted_in, attached_to, etc.
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_entity_relations_from_to_type ON entity_relations(from_entity_id, to_entity_id, relation_type); 
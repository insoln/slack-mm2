---
name: db-alembic-ops
description: Database + Alembic workflow for Slack-MM2 Sync (migrations, collapsed history, NEVER CANCEL guidance, and troubleshooting queries). Use when changing schema, debugging DB state, or dealing with migration failures.
license: Complete terms in LICENSE.txt
compatibility: Requires Postgres access; Docker Compose recommended for dev.
---

# Database & Alembic Operations

## When to use

- Running or debugging Alembic migrations
- Inspecting `entities`, `entity_relations`, `import_jobs` state
- Updating schema or indexes
- Investigating migration/startup failures

## Migrations

Canonical command (repo root):
- `alembic -c alembic.ini upgrade head`

Important:
- Migrations are also applied automatically when backend starts (lifespan hook). If backend appears “stuck”, it may be running migrations.
- **NEVER CANCEL** a migration command once started.

## Collapsed migration history

This repo uses a collapsed history model. For clean DBs, `upgrade head` is the expected flow.
If you’re dealing with a historical DB/schema mismatch, follow the guidance in `backend/README.md` / `docs/dev.md` rather than guessing.

## Common troubleshooting

- View backend logs (migration errors surface there):
  - `docker compose -f infra/docker-compose.dev.yml logs -f backend`

- Inspect DB quickly:
  - `docker compose -f infra/docker-compose.dev.yml exec db \
      psql -U slack-mm -d slack-mm -P pager=off -c "select * from import_jobs order by id desc limit 5;"`

- Inspect entities:
  - `docker compose -f infra/docker-compose.dev.yml exec db \
      psql -U slack-mm -d slack-mm -P pager=off -c "select id, entity_type, status from entities order by id desc limit 20;"`

## Related docs

- Migrations + DB policy: [docs/dev.md](../../../docs/dev.md)
- Schema notes: [backend/README.md](../../../backend/README.md)
- Infra DB notes: [infra/README.md](../../../infra/README.md)
- DB init/troubleshooting notes: [infra/db/README.md](../../../infra/db/README.md)

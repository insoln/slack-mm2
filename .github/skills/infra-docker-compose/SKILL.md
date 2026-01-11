---
name: infra-docker-compose
description: Docker Compose workflows for Slack-MM2 Sync (dev full-stack, prod stack, .env.dev/.env.prod, ports, log inspection). Use when starting/stopping services, reproducing bugs, or running integration flows.
license: Complete terms in LICENSE.txt
compatibility: Requires Docker and docker compose.
---

# Infra: Docker Compose Workflows

## When to use

- Spinning up the full development environment (backend+frontend+db+mattermost+test-files)
- Running end-to-end import/export manually
- Debugging compose startup, ports, container logs
- Running production compose locally

## Dev stack (recommended)

1. Ensure `infra/.env.dev` exists (dummy Slack tokens are fine for local dev).
  - For the canonical file contents, follow the dev docs: [docs/dev.md](../../../docs/dev.md)

2. Start full stack (expect first build to take a long time):
- `docker compose -f infra/docker-compose.dev.yml up --build -d`

Access points:
- Backend: `http://localhost:8000/healthcheck`
- Frontend: `http://localhost:5173`
- Mattermost: `http://localhost:8065`
- Test files: `http://localhost:9000`

## Stopping / cleaning

- Clean stop (recommended between runs):
  - `docker compose -f infra/docker-compose.dev.yml down --remove-orphans -v`

## Logs

- Backend logs:
  - `docker compose -f infra/docker-compose.dev.yml logs -f backend`
- Mattermost logs:
  - `docker compose -f infra/docker-compose.dev.yml logs -f mattermost`

## Prod stack

- Create `infra/.env.prod` with real Mattermost details
- Start:
  - `docker compose -f infra/docker-compose.prod.yml up --build -d`

## Notes

- Dev stack uses ephemeral storage (tmpfs): data is lost when containers stop.
- Frontend container uses “immutable sources + ephemeral deps” (node_modules not written to host).

## Related docs

- Infra overview: [infra/README.md](../../../infra/README.md)
- Dev workflow and ports: [docs/dev.md](../../../docs/dev.md)

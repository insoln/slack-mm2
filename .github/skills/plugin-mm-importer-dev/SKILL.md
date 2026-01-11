---
name: plugin-mm-importer-dev
description: Mattermost mm-importer plugin development workflow (SemVer bump, Docker multi-stage build, bundle output, deploy/enable via backend API). Use when changing plugin endpoints, attachment streaming, channel creation, or plugin packaging.
license: Complete terms in LICENSE.txt
compatibility: Requires Docker for the canonical build.
---

# Mattermost Plugin: mm-importer

## When to use

- Editing plugin server code under `infra/plugin/server/`
- Updating plugin API endpoints or behavior
- Changing attachment streaming (`/attachment_from_url`) or size validation
- Building and packaging the plugin bundle for deployment

## Key rules

- **Do not** use legacy build scripts. Use the multi-stage Docker build helper:
  - `bash infra/plugin/build-docker.sh`
- If you change plugin code, bump the version in `infra/plugin/plugin.json` following SemVer.

## Build (canonical)

From repo root:
- `bash infra/plugin/build-docker.sh`

Expected output:
- `infra/plugin/dist/mm-importer-<version>.tar.gz`

## Deploy / enable via backend

Backend management endpoints are exposed in two equivalent forms:
- `/plugin/*` (legacy, used by some scripts)
- `/api/plugin/*` (documented in the root README)

Common endpoints:
- `GET  /plugin/status` (or `GET /api/plugin/status`)
- `POST /plugin/ensure` (or `POST /api/plugin/ensure`) — idempotent deploy+enable
- `POST /plugin/deploy` (or `POST /api/plugin/deploy`)
- `POST /plugin/enable` (or `POST /api/plugin/enable`)
- `POST /plugin/reinstall` (or `POST /api/plugin/reinstall`)

Typical flow:
1. Ensure backend is healthy: `curl http://localhost:8000/healthcheck`
2. Ensure plugin installed+enabled: `curl -X POST http://localhost:8000/plugin/ensure`
3. Verify plugin responds (example): `http://localhost:8065/plugins/mm-importer/api/v1/hello`

## Related docs

- Plugin API + build/deploy strategy: [infra/plugin/README.md](../../../infra/plugin/README.md)
- Backend API routes overview: [backend/app/api/README.md](../../../backend/app/api/README.md)
- Backend plugin endpoint implementation: [backend/app/api/plugin.py](../../../backend/app/api/plugin.py)
- Public API examples: [README.md](../../../README.md)

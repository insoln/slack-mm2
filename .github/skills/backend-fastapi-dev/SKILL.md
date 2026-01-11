---
name: backend-fastapi-dev
description: FastAPI backend dev workflow for Slack-MM2 Sync (Python 3.14+, venv, Alembic migrations, black, pytest). Use when changing API endpoints, importer/exporter code, or debugging backend behavior.
license: Complete terms in LICENSE.txt
---

# Backend FastAPI Development

Use this skill when working on the Python backend in `backend/` (API, import/export pipeline, DB interactions).

## When to use

- Editing FastAPI routes under `backend/app/api/`
- Changing business logic under `backend/app/services/`
- Updating SQLAlchemy models under `backend/app/models/`
- Running `black` / `pytest`, or diagnosing failing backend tests
- Debugging `/healthcheck`, job progress, plugin management endpoints

## Local dev (outside Docker)

Prereqs:
- Python 3.14+
- A running Postgres (or use docker compose dev stack for DB/Mattermost)

Workflow:
1. Create/activate venv (repo root):
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Install deps:
   - `cd backend && pip install -r requirements.txt`
3. Apply migrations (repo root):
   - `alembic -c alembic.ini upgrade head`
4. Run the backend:
   - `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`

Health check:
- `curl http://localhost:8000/healthcheck` (also available as `/api/healthcheck`)
- Expected JSON (per integration test): `{"status": "ok"}`

## Recommended dev workflow (Docker full stack)

This repo’s default dev workflow is running the full compose stack (backend + frontend + db + mattermost + test-files).

- Create `infra/.env.dev` (dummy tokens are fine for local dev)
- Start stack:
  - `docker compose -f infra/docker-compose.dev.yml up --build -d`

Common endpoints:
- Backend: `http://localhost:8000/healthcheck`
- Frontend: `http://localhost:5173`

## Code quality

ALWAYS run before committing backend changes:
- Format: `cd backend && black app alembic tests`
- Tests: `cd backend && pytest --cov=app --cov-report=term-missing`

If you only need a quick signal:
- `cd backend && pytest tests/unit`

## Debugging checklist

- If backend doesn’t start, migrations may still be running in the lifespan hook (don’t assume Uvicorn is “hung”).
- Check logs:
  - `docker compose -f infra/docker-compose.dev.yml logs -f backend`
- If `/healthcheck` fails, look for:
  - DB connection errors
  - Alembic migration errors
  - Exceptions during FastAPI lifespan startup

## Related docs

- Developer workflow and testing: [docs/dev.md](../../../docs/dev.md)
- Backend overview + invariants: [backend/README.md](../../../backend/README.md)
- Healthcheck contract: [backend/tests/integration/test_api_healthcheck.py](../../../backend/tests/integration/test_api_healthcheck.py)

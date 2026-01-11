# Slack-MM2 Sync — Copilot Instructions (Lean)

All detailed procedures (commands, step-by-step workflows, invariants, test checklists) live in Agent Skills under `.github/skills/`. If you need to perform a workflow described in project docs, consult the corresponding skill first and follow the doc links inside the skill.

## Project overview

Slack-MM2 Sync is a monorepo for one-way synchronization from Slack to Mattermost.

Main components:
- Backend: Python/FastAPI app (import + export orchestration)
- Frontend: React/Vite UI
- Mattermost plugin: Go plugin bundle built via Docker
- Infrastructure: Docker Compose (dev/prod) and supporting manifests

## How to use skills

Before changing behavior or running an operational workflow, open the matching skill in `.github/skills/<skill-name>/SKILL.md` and follow its “Related docs” links.

Skill index:
- Backend dev workflow: `.github/skills/backend-fastapi-dev/SKILL.md`
- Import/export pipeline invariants: `.github/skills/backend-import-export-pipeline/SKILL.md`
- DB + Alembic operations: `.github/skills/db-alembic-ops/SKILL.md`
- Docker Compose workflows: `.github/skills/infra-docker-compose/SKILL.md`
- Plugin build/deploy: `.github/skills/plugin-mm-importer-dev/SKILL.md`
- Mini backup regression (deterministic E2E): `.github/skills/mini-backup-regression/SKILL.md`
- Job restart endpoint/workflow: `.github/skills/job-restart-feature/SKILL.md`
- Documentation rules: `.github/skills/docs-policy/SKILL.md`
- Frontend UI smoke/E2E (Playwright): `.github/skills/webapp-testing/SKILL.md`

## Repository-wide policy (not duplicated in skills)

Environment variables:
- Do not use env vars as feature flags for core logic.
- Do use env vars for external configuration (credentials, URLs, connection strings, timeouts).

## Canonical documentation

If you need a single starting point, use [docs/dev.md](../docs/dev.md). For doc-writing rules, use [docs/documentation-policy.md](../docs/documentation-policy.md).
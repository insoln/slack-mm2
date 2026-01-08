# Developer Documentation

Comprehensive guide for developers working on Slack-MM2 Sync.

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [Development Environment Setup](#development-environment-setup)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Building and Deployment](#building-and-deployment)
- [Database Operations](#database-operations)
- [CI/CD](#cicd)

## Architecture Overview

### Project Structure

Slack-MM2 Sync is a monorepo for one-way data synchronization from Slack to Mattermost:

- **`backend/`** — Python FastAPI backend implementing API, file processing, and data export
  - FastAPI application with async endpoints
  - SQLAlchemy models with universal entity schema
  - Service layers for backup processing and Mattermost export
  - Alembic database migrations
  - Unit and integration tests
  
- **`frontend/`** — React/Vite web interface for upload management and monitoring
  - Corporate panel UI with dark theme
  - Real-time upload progress tracking
  - Plugin management interface
  - Export monitoring with deterministic counters

- **`infra/`** — Infrastructure, Docker Compose configs, and Kubernetes manifests
  - `plugin/` — Mattermost plugin source code (Go) with multi-stage Docker build
  - `db/` — Database migrations and initialization scripts
  - `test-data/` — Canonical mini Slack backup for integration testing
  - `docker-compose.dev.yml` — Full development stack
  - `docker-compose.prod.yml` — Production configuration

### Import Architecture (Unified Single-Pass Pipeline)

The import process is implemented as a strictly deterministic, unidirectional, single-pass pipeline:

1. **extracting** – Upload and initial validation of archive (requires "flat" structure, no wrapper directory)
2. **users** – Parse and import users
3. **channels** – Parse and import channels / DMs / Group DMs
4. **messages** – Single pass through messages of EVERY channel, during which we simultaneously import:
   - Messages themselves
   - Reactions
   - Attachments (files)
   - Custom emoji references (for subsequent export)
   - Entity relations
5. **exporting** – Single export phase of fully assembled data to Mattermost
6. **done** – Final terminal status

There are NO multi-step/multi-cycle "catch-up" stages for reactions, attachments, etc. Everything is captured in one pass through messages, which:
- Eliminates race conditions on counters
- Simplifies reasoning about progress
- Guarantees reproducible results (same number of messages/attachments on same data)

### Deterministic Progress Counters

All progress counters are monotonically non-decreasing: they NEVER "roll back" when changing stages. Updates are implemented via atomic SQL function for JSONB merging (see below).

Main `meta` keys:

| Key | Value |
|-----|-------|
| `users_processed` | Number of imported users |
| `channels_processed` | Number of imported channels (incl. DMs/groups) |
| `messages_processed` | Number of imported messages |
| `reactions_processed` | Number of reactions |
| `attachments_processed` | Number of files/attachments |
| `emojis_processed` | Number of unique mentioned custom emojis |
| `totals`.*  | Final values captured during transition to `exporting`/`done` |
| `totals_frozen` | true after consolidation; denominator is stable |

### Atomic Metadata Merge (`merge_job_meta`)

Historical problem: lost updates (lost update) due to mixed ORM read-modify-write and independent UPDATE on JSONB. Solution: single SQL UPDATE with atomic field merging in `meta`.

Supported operations (dynamically constructed):
- **incr** – Atomic increment of numeric fields (`*_processed`)
- **set** – Set value (stage, service flags, etc.)
- **max** – Monotonic maximum (ensures non-decreasing counters even with conditional races)
- **nested** – Merge nested JSON objects (e.g., `durations_ms`, `totals`)
- **remove** – Remove keys (used sparingly, mainly for cleanup)

All parameters are cast to required types with explicit `::int` / `::jsonb` casts → eliminates `IndeterminateDatatypeError` and silent invalid values.

### Export Architecture

Full details in `backend/app/services/export/README.md`. Summary:

- **Orchestrator** (`services/export/orchestrator.py`) processes entities with global type barrier in order:
  `user → custom_emoji → channel → attachment → message → reaction`
- Each entity type has its own exporter (UserExporter, ChannelExporter, AttachmentExporter, MessageExporter, ReactionExporter, etc.)
- HTTP interaction with Mattermost (core + plugin) encapsulated in `MMApiMixin`
- All exporters inherit `ExporterBase` with `set_status(status, error=None)` method
- Supported statuses: `pending`, `success`, `failed`, `skipped`

## Development Environment Setup

### Prerequisites

- **Python**: Version 3.14+ (tested with 3.14)
- **Node.js**: Version 20+ (tested with 20.19.5, npm 10.8.2)
- **Go**: Version 1.22+ (tested with 1.24.7)
- **Docker**: Required for full development environment

### Quick Start (Docker Development Environment)

1. **Create required `.env.dev` file**:
   ```bash
   cd infra
   cat > .env.dev <<EOF
   SLACK_VERIFICATION_TOKEN=test_token
   SLACK_BOT_TOKEN=test_bot_token
   SLACK_SIGNING_SECRET=test_signing_secret
   EOF
   cd ..
   ```

2. **Start full development stack** (always full stack, no partial service startup):
   ```bash
   docker compose -f infra/docker-compose.dev.yml up --build -d
   ```
   
   **IMPORTANT**: Initial build may take 15+ minutes downloading images and building containers. Set timeout to 30+ minutes for first build. Subsequent builds are faster with caching.

3. **Access points**:
   - Backend API: http://localhost:8000/healthcheck
   - Frontend UI: http://localhost:5173
   - Mattermost: http://localhost:8065 (user: `admin`, password: `P@ssw0rd`)
   - Test files: http://localhost:9000
   - PostgreSQL: localhost:5432 (user/pass/db: slack-mm)

4. **Default credentials (Development)**:
   - Mattermost admin user: `admin` / `P@ssw0rd`
   - Mattermost admin token: `5x7rr788c7gwdnkdr9imb49ffo`
   - Mattermost team: `test` (ID: b7u9rycm43nip86mdiuqsxdcbe)
   - Database: user `slack-mm`, password `slack-mm`, database `slack-mm`

5. **Load test archive**:
   ```bash
   curl -F file=@infra/test-data/slack-mini-backup.zip http://localhost:8000/upload
   ```

### Local Development (Outside Docker)

For development outside Docker containers:

1. **Backend setup**:
   ```bash
   # Create and activate virtual environment
   python3 -m venv .venv
   source .venv/bin/activate
   
   # Install dependencies (~18 seconds)
   cd backend
   pip install -r requirements.txt
   
   # Run migrations
   cd ..
   alembic -c alembic.ini upgrade head
   ```

2. **Frontend setup**:
   ```bash
   cd frontend
   npm ci  # ~7 seconds
   npm run dev  # Starts on port 5173
   ```

3. **Plugin build** (Docker multi-stage):
   ```bash
   bash infra/plugin/build-docker.sh  # ~69 seconds
   ```
   Output: `infra/plugin/dist/mm-importer-X.Y.Z.tar.gz`

### Dev Environment Services

The `docker-compose.dev.yml` includes:
- **backend** — FastAPI application
- **frontend** — Vite dev server with hot reload
- **db** — PostgreSQL with ephemeral storage (tmpfs)
- **mattermost** — Mattermost instance with test user
- **test-files** — Simple HTTP server for test fixtures (port 9000)
- **plugin-autobuild** — One-shot service that builds plugin bundle if missing

**Frontend immutable + ephemeral deps strategy**:
- Source directory mounted read-only (`../frontend:/app:ro`)
- On container start, creates internal `/workspace`, runs `npm ci`, starts vite
- Symlinks `src` and `index.html` to `/app` sources for live reload
- Temporary files live only in `/workspace` and disappear after container stop

## Development Workflow

### Backend Development

#### Running Tests

**Unit tests** (~1.5 seconds for 2 tests):
```bash
cd backend
pytest tests/unit
```

**Unit tests with coverage** (~6 seconds):
```bash
pytest tests/unit --cov=app --cov-report=term-missing
```

**Integration tests** (~1 second, requires running services):
```bash
# Set Mattermost credentials
export MATTERMOST_API_TOKEN=5x7rr788c7gwdnkdr9imb49ffo
export MATTERMOST_API_URL=http://localhost:8065/api/v4/users/me

pytest tests/integration
```

**All tests with coverage** (~6 seconds):
```bash
pytest --cov=app --cov-report=term-missing
```

#### Code Formatting

**ALWAYS run before committing** (<1 second):
```bash
cd backend
black app alembic tests
```

#### Backend Structure
- `app/main.py` — FastAPI application entry point with lifespan management
- `app/api/` — REST API endpoints (upload, export, plugin, stats, progress, jobs)
- `app/models/` — SQLAlchemy database models
- `app/services/` — Business logic for backup processing and export to Mattermost
- `alembic/` — Database migration scripts
- `tests/unit/` — Unit tests (mock external dependencies)
- `tests/integration/` — Integration tests (require running services)

### Frontend Development

#### Building and Testing

**Linting** (<1 second):
```bash
cd frontend
npm run lint  # ALWAYS run before committing
```

**Build** (~1.7 seconds):
```bash
npm run build
```

**Development server**:
```bash
npm run dev  # Starts on port 5173
```

**Preview production build**:
```bash
npm run preview
```

#### Updating Dependencies

After changing `package.json`:
```bash
docker compose -f infra/docker-compose.dev.yml build frontend
docker compose -f infra/docker-compose.dev.yml up -d frontend --force-recreate
```

#### Frontend Structure
- `src/App.jsx` — Main application component with corporate panel layout
- `src/components/UI.jsx` — Reusable UI components (Header, Sidebar, Card, Button, StatusBadge)
- `src/components/ui.css` — Dark theme styling with CSS variables

### Mattermost Plugin Development

#### Quick Build (Multi-Stage Docker)

**Preferred method** (~69 seconds first build with cache warmup):
```bash
bash infra/plugin/build-docker.sh
```

**NEVER CANCEL** mid-way. Output: `infra/plugin/dist/mm-importer-X.Y.Z.tar.gz`

#### Plugin Structure
- `plugin.json` — Manifest (id/version)
- `server/` — Go server implementation
- `webapp/` — React webapp (if any UI)
- `build-docker.sh` — Multi-stage Docker build helper (authoritative path)

**Important**: Legacy helper scripts (`build-dev.sh`) and the old Makefile still exist in the repository but are **deprecated** and should not be used. For all builds, use only `build-docker.sh`.

#### Versioning (SemVer)

When making plugin changes, version in `plugin.json` **MUST** be incremented:
- **MAJOR**: Incompatible API changes (removing/renaming endpoints)
- **MINOR**: New backward-compatible functionality (new endpoints, optional fields)
- **PATCH**: Backward-compatible bug fixes, performance improvements

#### Useful References
- [Mattermost GitHub](https://github.com/mattermost/mattermost)
- [Mattermost API docs](https://developers.mattermost.com/api-documentation/)
- [Mattermost Plugin docs](https://developers.mattermost.com/integrate/plugins/components/server/)
- [Server API reference](https://developers.mattermost.com/integrate/reference/server/server-reference)

## Testing

### Mini Integration Test

The script `scripts/run_mini_backup_integration.sh` performs a deterministic full import/export check on mini dataset:

```bash
./scripts/run_mini_backup_integration.sh
```

It guarantees:
- Spins up full docker-compose stack
- Ensures Mattermost Importer plugin is deployed/enabled
- Loads mini archive (`infra/test-data/slack-mini-backup.zip`)
- Polls `/jobs` until success status and checks final counters
- Scans backend logs for errors and validates admin user binding
- Properly shuts down stack (even on error)

**Canonical mini-backup expected values** (final totals):
```
users=4
channels=7
messages=19
attachments=3
reactions=4
```

### Mini-Backup Policy

`infra/test-data/slack-mini-backup.zip` is the canonical minimal dataset for deterministic regression.

**Key rules**:
1. ZIP is source of truth. Unpacked folder must match byte-for-byte.
2. Flat structure inside archive (no root wrapper directory).
3. Changing content requires:
   - Regenerate archive: `python infra/test-data/build_mini_backup_zip.py`
   - Update expected counters in `scripts/run_mini_backup_integration.sh`
   - Update README sections
4. No temporary edits for debugging: use separate archive outside repo.
5. `url_private` files must start with one of `IMPORT_URL_PREFIXES` (default: `https://files.slack.com,http://test-files:9000`)
6. Deterministic content: no timestamp-dependent generation.

**Checklist for updating mini-backup**:
1. Edit files in unpacked `infra/test-data/slack-mini-backup/`
2. Generate: `python infra/test-data/build_mini_backup_zip.py`
3. Run: `./scripts/run_mini_backup_integration.sh` — ensure early success
4. Update README + script + commit zip
5. Wait for green CI

### Early Success Semantics

The integration script considers run successful as soon as:
- Stage transitions to `exporting` AND
- Final counters match the canonical values

Waiting for `done` is NOT required for mini-backup regression — export may continue side operations (e.g., packaging), but data is already fully identified with targets.

### Test Files Service

In dev compose there's a `test-files` service (port 9000) — simple HTTP server serving `infra/test-data/`. Mini Slack backup references `url_private` attachments at `http://test-files:9000/...`, eliminating network delays and making checks fast and repeatable.

## Database Operations

### Migrations

**Automatic Application**: Migrations are automatically applied when backend starts (via `alembic upgrade head` in the lifespan hook). This happens in both development and production environments.

**Manual Application** (for local development outside Docker):
```bash
alembic -c alembic.ini upgrade head
```

**NEVER CANCEL**: Migration commands may take several minutes depending on data size.

Configuration:
- Migration path: `backend/alembic/` (relative to project root)
- Configuration: `alembic.ini` in project root
- Connection: Uses `DATABASE_URL` environment variable
- Auto-execution: Triggered in `backend/app/main.py` lifespan hook (unless `PYTEST_RUN=1`)

### Collapsed Migration History

Current schema uses collapsed history. To apply on clean database:
```bash
alembic -c alembic.ini upgrade head
```

Or simply start the backend — migrations will run automatically.

Updating existing installation with historical data not supported automatically — requires either:
1. Create new DB and run `upgrade head` (or start backend), then re-import Slack archive; or
2. Manually bring schema to final state (see `001_initial_full_schema` content) and run `alembic stamp 001_initial_full_schema`

Key schema features:
- Global entity uniqueness: unique index `uq_entities_type_slack` (without job_id)
- `import_jobs` table with JSONB `meta` field for progress
- `entity_relations` table with uniqueness `(from_entity_id, to_entity_id, relation_type)`
- Functional/partial indexes for username and reaction lookup
- Performance indexes on `(job_id, entity_type, status)` and separate `job_id` for statistics

### Database Troubleshooting

Inspect entities:
```bash
docker compose -f infra/docker-compose.dev.yml exec db \
  psql -U slack-mm -d slack-mm -P pager=off \
  -c "select * from entities limit 100;"
```

## Building and Deployment

### Build Times (Set Appropriate Timeouts)

- Python dependency install: ~18 seconds (timeout: 60s)
- Frontend dependency install: ~7 seconds (timeout: 30s)
- Frontend build: ~1.7 seconds (timeout: 30s)
- Plugin build: ~69 seconds (timeout: 120s)
- Docker development environment: 15+ minutes first time (timeout: 30+ minutes)

### Production Build

See main [README.md](../README.md) for production deployment instructions and [infra/README.md](../infra/README.md) for infrastructure details.

## CI/CD

### Pre-commit Hooks

The repo provides optional pre-commit hook that enforces Python code formatting using `black`.

**Enable** (one-time):
```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit
```

**Behavior**:
- Runs `black --check` on `backend/app`, `backend/alembic`, and `backend/tests`
- If formatting issues found, auto-formats, aborts commit, asks to review & re-stage
- Keeps CI green by preventing unformatted code

**Disable** (if needed):
```bash
git config --unset core.hooksPath
```

### Pre-commit Configuration

To automatically check style and basic errors before commit:

**Install and activate** (one-time):
```bash
pip install -r backend/requirements.txt  # contains pre-commit
pre-commit install
```

**What runs on `git commit`**:
- `black` — auto-formatting Python
- `end-of-file-fixer`, `trailing-whitespace`, `detect-private-key`, `check-added-large-files`
- Optional fast unit test hook (`pytest-unit-fast`) — skip with: `SKIP=pytest-unit-fast git commit -m "msg"`

**Manual run** for whole repo (recommended after large refactor):
```bash
pre-commit run --all-files
```

### Validation Before Committing

**ALWAYS run before committing**:

Backend:
```bash
cd backend
black app alembic tests
pytest --cov=app --cov-report=term-missing
```

Frontend:
```bash
cd frontend
npm run lint
npm run build
```

## Environment Variable Policy

We intentionally minimize the number of environment variables.

**Principles**:
1. **Only necessary environment configuration**: Service paths (URLs), access tokens, timeout/pool sizes, performance parameters required for deployment in different environments (dev/stage/prod)
2. **No feature-flags via env**: Application functionality should behave identically for same code version. New features enabled immediately (or via explicit data version logic / entity presence), not via hidden toggles
3. **CI/Prod repeatability**: Everything affecting logic branching is fixed in code or repository config files (migrations, manifests), not in random environment variables
4. **Acceptable exceptions**: 
   - Secrets and parameters impossible to store in Git (passwords, tokens)
   - Values objectively different between installations (domain names, external URLs)
   - **Operational emergency toggles**: Temporary flags to disable problematic subsystems in production emergencies (must be documented as exceptional, temporary measure)
5. **If you think you need a new env variable "to quickly disable/enable something"**, first rethink architecture (can we make code idempotent/safe always) or fix behavior via config in repository

**Consequence**: When reviewing any new env variable, must explicitly describe why it's impossible without it (not reproducible or requires secret) and how absence of "hidden mode" is guaranteed.

### Allowed Environment Variables

**Backend/Export**:
- `MM_URL` — Mattermost base URL (e.g., http://mattermost:8065)
- `MM_TOKEN` — Admin or system token
- `MM_TEAM` — Mattermost team name
- `EXPORT_WORKERS` — Number of export workers
- `ATTACHMENT_WORKERS` — File upload workers (defaults to EXPORT_WORKERS)
- `EXPORT_CHANNEL_CONCURRENCY` — Max parallel channels during message publishing
- `ATTACHMENT_URL_TIMEOUT_SECONDS` — Extended timeout for large attachment streaming (default 600s)
- `SKIP_ATTACHMENT_EXPORT` — **Emergency operational toggle** (1/true) to skip attachment export stage. Use only as temporary measure in production emergencies; manually reset affected entity statuses for re-export after issue is resolved
- `PLUGIN_BUNDLE_URL` — Optional remote plugin bundle URL
- Database pool settings: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`
- HTTP pool settings: `MM_MAX_KEEPALIVE`, `MM_MAX_CONNECTIONS`, `MM_HTTP2`

**Slack tokens** (dev only):
- `SLACK_VERIFICATION_TOKEN`
- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`

**Import configuration** (fixed constants, not feature flags):
- `IMPORT_URL_PREFIXES` — CSV of allowed `url_private` prefixes for attachments (default: `https://files.slack.com`)
- Test dataset adds `http://test-files:9000`

## Branching Policy

All changes ONLY through separate branches. Direct commits to `master` are prohibited.

**Typical flow steps**:
1. Update master: `git fetch origin && git checkout master && git pull --rebase`
2. Create branch: `git checkout -b feature/<brief>` (or `fix/`, `chore/`, `docs/`)
3. Make changes — small logically connected commits, run formatters and tests before push
4. Sync branch before PR: `git fetch origin && git rebase origin/master`
5. Push: `git push -u origin feature/<brief>`
6. Open Pull Request → wait for CI → review → merge (squash or fast-forward)
7. Delete branch (locally and in origin)

**Hard rules**:
- No force push to `master`
- `master` always in working state (tests green, migrations valid)
- Hotfix: `hotfix/<issue>` + PR, even when urgent
- Large refactorings — split into series of PRs

**Reasons**: Cleaner history, fewer conflicts, predictable CI signal, ability to safely rebase.

## Logging

- Logging configuration in `app/logging_config.py`
- Log format: time, level, logger name, message
- Use `from app.logging_config import backend_logger` and `backend_logger.info(...)`
- Integrated with Uvicorn and supports async tasks
- In dev environment, Uvicorn access logs enabled with `UVICORN_LOG_LEVEL=INFO`
- View logs: `docker compose -f infra/docker-compose.dev.yml logs -f backend`

## Additional Documentation

- [Backend README](../backend/README.md) — Backend-specific details
- [Frontend README](../frontend/README.md) — Frontend-specific details
- [Infrastructure README](../infra/README.md) — Infrastructure and deployment
- [Plugin README](../infra/plugin/README.md) — Mattermost plugin development
- [Database README](../infra/db/README.md) — Database schema and migrations
- [Documentation Policy](documentation-policy.md) — When and how to add docs
- [Job Restart Feature](job-restart-feature.md) — Job restart mechanism
- [Bot Migration](bot-migration.md) — Bot user migration details
- [Manual Testing Bot Export](manual-testing-bot-export.md) — Bot export testing

## Troubleshooting

### Backend Healthcheck Hanging

**Symptom**: Multiple lines `[WAIT] health attempt=N code=000 body={"status":"ok"}` and no transition to archive loading.

**What `code=000` means**: `curl` couldn't establish connection (port not listening or temporary refusal). The `/tmp/health.json` file might contain result from previous successful attempt, so body can look "healthy".

**Probable causes**:
1. Migrations still running (alembic runs in lifespan before uvicorn starts)
2. Mattermost connection during plugin auto-ensure (network timeouts) slowing startup
3. Port 8000 occupied by another local process
4. Migration error / exception in lifespan — uvicorn didn't start

**Diagnostics**:
```bash
docker compose -f infra/docker-compose.dev.yml logs -n 200 backend
docker compose -f infra/docker-compose.dev.yml ps
curl -v http://localhost:8000/healthcheck || true
ss -tnlp | grep :8000 || true
```

**Optimizations** (already implemented): Health file zeroed before each attempt, added `--connect-timeout` and `--max-time` timeouts — prevents reuse of stale body.

**Potential improvements** (roadmap): Move plugin ensure to deferred background task after server starts so healthcheck passes faster; add early log output after N failed attempts.

### Dev Services "Sticking"

**Symptoms**: Services don't respond to SIGTERM properly during `docker compose down`.

**Mitigations** (already in place):
- `init: true`, `stop_signal: SIGINT`, appropriate `stop_grace_period` in dev compose
- Frontend runs direct `npx vite --host` (not via `npm run dev`) so PID 1 belongs to node/vite

**Recommendations**:
- Before re-running: `docker compose -f infra/docker-compose.dev.yml down --remove-orphans`
- After sleep/Docker restart/WSL issues: restart Docker daemon or `wsl.exe --shutdown` (from Windows) to clean namespaces/cgroups
- If port occupied by orphaned process: kill process and re-start services

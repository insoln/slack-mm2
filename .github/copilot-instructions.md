# Slack-MM2 Sync Development Instructions

Always follow these instructions first before gathering additional context. Only fallback to search or bash commands when you encounter unexpected information that does not match the information provided here.

## Project Overview
Slack-MM2 Sync is a monorepo for one-way data synchronization from Slack to Mattermost. The project includes:
- **Backend**: Python FastAPI application for API, file processing, and data export
- **Frontend**: React/Vite web interface for upload management and monitoring 
- **Mattermost Plugin**: Go-based plugin for importing data into Mattermost
- **Infrastructure**: Docker Compose configurations and Kubernetes manifests

## Working Effectively

### Branching Policy (MANDATORY)
ALL changes MUST be implemented in a separate feature branch – never commit directly to `master`.

Required workflow:
1. Pull latest `origin/master`.
2. Create a branch: `git checkout -b feature/<short-topic>` (or `fix/`, `chore/`, `docs/`).
3. Make and commit changes (small, logically grouped commits; run formatters / linters / tests before each push).
4. Rebase onto `origin/master` before opening / updating a PR: `git fetch origin && git rebase origin/master`.
5. Push with tracking: `git push -u origin feature/<short-topic>`.
6. Open Pull Request → wait for checks → review → squash or fast-forward merge.
7. Delete the branch after merge (remote + local).

Hard rules:
- No force pushes to `master`.
- `master` must remain in a deployable / green state (tests pass, migrations valid).
- If an emergency hotfix is required: still create `hotfix/<issue>` branch, PR, and only then merge.
- Large refactors: coordinate early; prefer incremental PRs.

Rationale: Guarantees reproducible review history, enables safe rewrites (rebases) off the mainline, reduces merge conflicts, and keeps CI signal clean.

### Initial Setup and Dependencies
- **Python**: Version 3.11+ required (project tested with Python 3.12)
- **Node.js**: Version 20+ required (tested with Node.js 20.19.5, npm 10.8.2)
- **Go**: Version 1.22+ required (tested with Go 1.24.7)
- **Docker**: Required for full development environment

### Backend Development (Python FastAPI)
- **Install dependencies**: `cd backend && pip install -r requirements.txt` -- takes ~18 seconds
- **Code formatting**: `black app alembic tests` -- takes <1 second. ALWAYS run before committing
- **Unit tests**: `pytest tests/unit` -- takes ~1.5 seconds for 2 tests  
- **Unit tests with coverage**: `pytest tests/unit --cov=app --cov-report=term-missing` -- takes ~6 seconds
- **Integration tests**: `pytest tests/integration` -- takes ~1 second (requires external services for full functionality)
- **All tests with coverage**: `pytest --cov=app --cov-report=term-missing` -- takes ~6 seconds
- **Database migrations**: `alembic -c alembic.ini upgrade head` (from project root)
- **NEVER CANCEL**: Migration commands may take several minutes depending on data size

#### Backend Structure
- `app/main.py` - FastAPI application entry point with lifespan management
- `app/api/` - REST API endpoints (upload, export, plugin, stats, progress, jobs)
- `app/models/` - SQLAlchemy database models
- `app/services/` - Business logic for backup processing and export to Mattermost
- `alembic/` - Database migration scripts
- `tests/unit/` - Unit tests (mock external dependencies)
- `tests/integration/` - Integration tests (require running services)

#### Environment Variables for Backend Testing
For integration tests that connect to Mattermost API:
- `MATTERMOST_API_TOKEN=5x7rr788c7gwdnkdr9imb49ffo` (dev environment default)
- `MATTERMOST_API_URL=http://localhost:8065/api/v4/users/me`
#### Import Configuration (Single-Pass Importer)
Current unified single-pass importer (messages + reactions + attachments + emojis) uses fixed constants for predictable behavior:
* `IMPORT_URL_PREFIXES` — CSV of allowed `url_private` prefixes for attachments (default: `https://files.slack.com`). Test dataset adds `http://test-files:9000`.

**Fixed behavior (no longer configurable via environment variables):**
* Channel processing: Fixed to sequential (concurrency=1) to prevent database race conditions.
* Stage duration recording: Controlled by DEBUG logging level instead of environment variables.
* Meta update frequency: Fixed to 2-second intervals and batch_size message counts.
Deprecated / removed: `IMPORT_CHANNEL_CONCURRENCY`, `IMPORT_RECORD_STAGE_DURATIONS`, `IMPORT_META_UPDATE_INTERVAL_SEC`, `IMPORT_META_UPDATE_EVERY`, batching flags, orjson fast-path flags, single-pass feature toggles, reaction bulk flags.
### Frontend Development (React/Vite)
- **Install dependencies**: `cd frontend && npm ci` -- takes ~7 seconds
- **Linting**: `npm run lint` -- takes <1 second. ALWAYS run before committing  
- **Build**: `npm run build` -- takes ~1.7 seconds
- **Development server**: `npm run dev` (starts on port 5173)
- **Preview production build**: `npm run preview`

#### Frontend Structure
- `src/App.jsx` - Main application component with corporate panel layout
- `src/components/UI.jsx` - Reusable UI components (Header, Sidebar, Card, Button, StatusBadge)
- `src/components/ui.css` - Dark theme styling with CSS variables

### Mattermost Plugin Development (Go)
- **Quick build**: `cd infra/plugin && bash build-dev.sh` -- takes ~69 seconds. NEVER CANCEL
- **NEVER CANCEL**: Plugin build includes Go dependency downloads, npm install, and webpack compilation
- **Output**: Creates `dist/mm-importer-0.3.0.tar.gz` bundle ready for Mattermost upload
- **Plugin ID**: `mm-importer` (version 0.3.0)
- **Requirements**: Go 1.22+, Node.js for webapp build

#### Plugin Structure  
- `plugin.json` - Plugin manifest with ID, version, and server executable paths
- `server/` - Go server-side plugin code
- `webapp/` - React webapp for plugin UI (if applicable)
- `build-dev.sh` - Portable build script for development

#### Plugin Makefile Issues
The Makefile requires `build/setup.mk` which is missing from this repository. Use `build-dev.sh` instead of make commands.

### Docker Development Environment

#### Creating Required .env File
Before using Docker Compose, create `infra/.env`:
```bash
cd infra
cat > .env << EOF
SLACK_VERIFICATION_TOKEN=test_token
SLACK_BOT_TOKEN=test_bot_token  
SLACK_SIGNING_SECRET=test_signing_secret
EOF
```

#### Development Environment (docker-compose.dev.yml)
- **Start all services**: `cd infra && docker compose -f docker-compose.dev.yml up --build`
- **NEVER CANCEL**: Initial build may take 15+ minutes downloading images and building containers
- **CRITICAL**: Set timeout to 30+ minutes for first build. Subsequent builds are faster with caching
- **Services included**: backend, frontend, Mattermost, PostgreSQL, plugin builder
- **Access points**:
  - Backend API: http://localhost:8000  
  - Frontend UI: http://localhost:5173
  - Mattermost: http://localhost:8065
  - PostgreSQL: localhost:5432 (user/pass/db: slack-mm)

#### Network Issues
Docker builds may fail with SSL certificate errors when downloading Python packages or Node modules. This is a known limitation in some network environments. Individual component builds work correctly outside Docker.

#### Default Credentials (Development)
- **Mattermost admin token**: `5x7rr788c7gwdnkdr9imb49ffo`
- **Mattermost team**: `test` (ID: b7u9rycm43nip86mdiuqsxdcbe)
- **Database**: user `slack-mm`, password `slack-mm`, database `slack-mm`

### Database Operations
- **Migrations**: Run from project root with `alembic -c alembic.ini upgrade head`
- **Migration path**: `backend/alembic/` (relative to project root)  
- **Configuration**: `alembic.ini` in project root
- **Connection**: Uses DATABASE_URL environment variable

## Validation and Testing

### Manual Validation Scenarios
After making changes, ALWAYS test these scenarios:

#### Backend API Testing
1. **Health check**: `curl http://localhost:8000/healthcheck` should return `{"status": "healthy"}`
2. **File upload**: Test POST to `/upload` with a sample file
3. **Export status**: Check GET `/export/status` returns current export state
4. **Plugin management**: Test `/plugin/status`, `/plugin/deploy`, `/plugin/enable` endpoints

#### Frontend UI Testing  
1. **Load application**: Navigate to http://localhost:5173
2. **Upload interface**: Verify file upload form is functional
3. **Plugin management**: Check plugin status display and control buttons
4. **Export monitoring**: Verify export progress and status updates

#### Integration Testing
1. **Database connectivity**: Ensure migrations run successfully
2. **Mattermost integration**: Verify plugin deployment and enablement
3. **End-to-end workflow**: Upload → Process → Export → Verify in Mattermost

### CI/CD Validation
Before committing, ALWAYS run:
- **Backend**: `cd backend && black app alembic tests && pytest --cov=app --cov-report=term-missing`
- **Frontend**: `cd frontend && npm run lint && npm run build`

## Common Tasks and Timing Expectations

### Build Times (Set appropriate timeouts)
- Python dependency install: ~18 seconds (timeout: 60s)
- Frontend dependency install: ~7 seconds (timeout: 30s)  
- Frontend build: ~1.7 seconds (timeout: 30s)
- Plugin build: ~69 seconds (timeout: 120s)
- Docker development environment: 15+ minutes first time (timeout: 30+ minutes)
- **NEVER CANCEL** any build process - always wait for completion

### Testing Times
- Backend unit tests: ~1.5 seconds (timeout: 30s)
- Backend tests with coverage: ~6 seconds (timeout: 60s)
- Frontend linting: <1 second (timeout: 30s)

### Known Working Commands
```bash
# Backend development
cd backend
pip install -r requirements.txt
black app alembic tests  
pytest --cov=app --cov-report=term-missing

# Frontend development  
cd frontend
npm ci
npm run lint
npm run build

# Plugin development
cd infra/plugin
bash build-dev.sh

# Database migrations
cd /path/to/project/root
alembic -c alembic.ini upgrade head

# Docker development
cd infra
echo "SLACK_VERIFICATION_TOKEN=test" > .env
echo "SLACK_BOT_TOKEN=test" >> .env  
echo "SLACK_SIGNING_SECRET=test" >> .env
docker compose -f docker-compose.dev.yml up --build
```

## Important Notes

### What Works Reliably
- ✅ Backend: Dependencies, tests, linting, local development
- ✅ Frontend: Dependencies, build, linting, local development  
- ✅ Plugin: Go build process via build-dev.sh script
- ✅ Database: Local PostgreSQL via Docker container
- ✅ Individual service testing and development

### Known Limitations  
- ❌ Docker full environment build may fail with SSL certificate issues in some network environments
- ❌ Plugin Makefile requires missing `build/setup.mk` - use `build-dev.sh` instead
- ⚠️ Integration tests require running Mattermost and database services
- ⚠️ Plugin requires Mattermost server for full testing

### Critical Reminders
- **NEVER CANCEL** builds or long-running commands - they may take 15+ minutes
- **ALWAYS** run linting before committing (black for Python, ESLint for JavaScript)
- **ALWAYS** test manual validation scenarios after making changes
- **SET TIMEOUTS** of 30+ minutes for Docker builds, 2+ minutes for plugin builds
- Use the individual component build processes when Docker environment issues occur
- **NEVER COMMIT DIRECTLY TO `master`** – always create a feature branch and go through a PR.

## File Locations

### Key Configuration Files
- `backend/requirements.txt` - Python dependencies
- `frontend/package.json` - Node.js dependencies and scripts
- `infra/plugin/plugin.json` - Mattermost plugin manifest
- `alembic.ini` - Database migration configuration (project root)
- `infra/docker-compose.dev.yml` - Development environment configuration

### Important Directories
- `backend/app/` - FastAPI application source
- `backend/tests/` - Python test suites
- `frontend/src/` - React application source  
- `infra/plugin/` - Mattermost plugin source
- `backend/alembic/` - Database migration scripts
- `infra/` - Infrastructure and deployment configurations
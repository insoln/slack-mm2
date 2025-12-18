# Slack-MM2 Sync Development Instructions

Always follow these instructions first before gathering additional context. Only fallback to search or direct commands when you encounter unexpected information that does not match the information provided here.

## Project Overview
Slack-MM2 Sync is a monorepo for one-way data synchronization from Slack to Mattermost. The project includes:
- **Backend**: Python FastAPI application for API, file processing, and data export
- **Frontend**: React/Vite web interface for upload management and monitoring 
- **Mattermost Plugin**: Go-based plugin for importing data into Mattermost
- **Infrastructure**: Docker Compose configurations and Kubernetes manifests

## Working Effectively

### Initial Setup and Dependencies
- **Python**: Version 3.14+ required (project tested with Python 3.14)
- **Node.js**: Version 20+ required (tested with Node.js 20.19.5, npm 10.8.2)
- **Go**: Version 1.22+ required (tested with Go 1.24.7)
- **Docker**: Required for full development environment

### Backend Development (Python FastAPI)
- **Virtual Environment**: ALWAYS work within a Python virtual environment. Create it in the project root: `python3 -m venv .venv` and activate: `source .venv/bin/activate`.
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
* `ATTACHMENT_URL_TIMEOUT_SECONDS` — extended timeout (seconds) for large attachment streaming via plugin endpoint `/attachment_from_url` (default 600). Increase for large videos; decrease to detect stalls more aggressively.

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
- **Quick build (multi-stage Docker)**: `bash infra/plugin/build-docker.sh` (≈69s first build with cache warmup). NEVER CANCEL mid-way.
- **Output**: `infra/plugin/dist/mm-importer-X.Y.Z.tar.gz` ready for upload / publication
- **Plugin ID**: `mm-importer` (version reflected in `plugin.json`)
- **Requirements**: Docker (preferred). Direct host Go/Node setup no longer supported (legacy scripts removed).
- **Mattermost GitHub**: https://github.com/mattermost/mattermost (plugin API examples)
- **Mattermost API docs**: https://developers.mattermost.com/api-documentation/
- **Mattermost Plugin docs**: https://developers.mattermost.com/integrate/plugins/components/server/
- **Server API reference**: https://developers.mattermost.com/integrate/reference/server/server-reference

#### Plugin Structure  
- `plugin.json` - Manifest (id/version)
- `server/` - Go server implementation
- `webapp/` - React webapp (if any UI)
- `build-docker.sh` - Multi-stage Docker build helper (authoritative path)

#### Deprecated Artifacts Removed
Legacy `build-dev.sh` and the old Makefile have been removed in favor of the reproducible Docker multi-stage build. Use only `build-docker.sh`.

### Environment Variable Policy
- **DO NOT** use environment variables as feature flags to control application logic (e.g., enabling/disabling a code path). All core features should be enabled and work out-of-the-box.
- **DO** use environment variables for external configuration that changes between environments, such as:
  - API tokens, secrets, and credentials
  - Database connection strings (e.g., `DATABASE_URL`)
  - External service URLs (e.g., `MATTERMOST_API_URL`)
  - Ports and hostnames
  - Long-running external operation timeouts (e.g., `ATTACHMENT_URL_TIMEOUT_SECONDS` for large Slack attachment streaming via plugin; default 600s)
- Debugging or performance-tuning options should not be managed by environment variables in production code. If such functionality is needed, it should be exposed via dedicated debug endpoints or logging configurations.

### Docker Development Environment

#### Creating Required .env.dev File
Before using Docker Compose, create `infra/.env.dev`:
```bash
cd infra
cat > .env.dev << EOF
SLACK_VERIFICATION_TOKEN=test_token
SLACK_BOT_TOKEN=test_bot_token  
SLACK_SIGNING_SECRET=test_signing_secret
EOF
```
For production compose runs use `infra/.env.prod` with real Mattermost credentials.

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

#### Default Credentials (Development)
- **Mattermost admin user**: `admin` / `P@ssw0rd`
- **Mattermost admin token**: `5x7rr788c7gwdnkdr9imb49ffo`
- **Mattermost team**: `test` (ID: b7u9rycm43nip86mdiuqsxdcbe)
- **Database**: user `slack-mm`, password `slack-mm`, database `slack-mm`

### Database Operations
- **Migrations**: Run from project root with `alembic -c alembic.ini upgrade head`
- **Migration path**: `backend/alembic/` (relative to project root)  
- **Configuration**: `alembic.ini` in project root
- **Connection**: Uses DATABASE_URL environment variable

### Debugging Tips
- Use something like `docker compose -f infra/docker-compose.dev.yml exec db psql -U slack-mm -d slack-mm -P pager=off -c "select * from entities limit 100;"` to troubleshoot database entities.

### Legacy features
- If you encounter legacy code or features, document them and consider refactoring or removing them in future updates.
- If you make a feture legacy, please document it clearly in the code comments.

### Documentation Policy
- Follow `docs/documentation-policy.md` whenever you add or update feature-specific Markdown files. Keep component READMEs concise and link out to deeper docs for complex subsystems.

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
When you finished your work, and especially before committing, ALWAYS run:
- **Backend**: `cd backend && black app alembic tests && pytest --cov=app --cov-report=term-missing`
- **Frontend**: `cd frontend && npm run lint && npm run build`

## Common Tasks and Timing Expectations

### Build Times (Set appropriate timeouts)
- Python dependency install: ~18 seconds (timeout: 60s)
- Frontend dependency install: ~7 seconds (timeout: 30s)  
- Frontend build: ~1.7 seconds (timeout: 30s)
- Plugin build: ~69 seconds (timeout: 120s)
- Docker development environment: 15+ minutes first time (timeout: 30+ minutes)

### CLI commands
- Set reasonable timeouts when running commands if possible.
- Always limit expected output by mumber of lines as output may be large. Try to limit to 20 lines or less where possible, but it depends on the command. Try to filter output to only what is necessary where possible.

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
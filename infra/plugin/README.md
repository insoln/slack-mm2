# MM-Importer Plugin

A Mattermost plugin for importing messages and metadata from external sources as any user, preserving threading and user metadata.

## Features

- Import messages as any user
- Preserve threading via `root_id`
- Custom timestamps for historical data
- Create/get channels with normalized names
- Add channel members in bulk
- Create/resolve DM and Group DM channels
- Import reactions
- **Automatic mark-as-read**: Imported posts are automatically marked as read for all channel members to prevent false notifications during bulk imports

## API Endpoints (implemented here)

- POST `/plugins/mm-importer/api/v1/import` — создать пост от имени любого пользователя
- POST `/plugins/mm-importer/api/v1/reaction` — добавить реакцию к посту
- POST `/plugins/mm-importer/api/v1/channel` — создать/получить канал (нормализация имени)
- POST `/plugins/mm-importer/api/v1/channel/members` — добавить участников (bulk)
- POST `/plugins/mm-importer/api/v1/channel/archive` — архивировать канал
- POST `/plugins/mm-importer/api/v1/dm` — создать/получить личный канал (2 пользователя)
- POST `/plugins/mm-importer/api/v1/gdm` — создать/получить групповой DM

### Channel name normalization
- lower-case
- пробелы/точки/подчёркивания → `-`
- только ASCII буквы/цифры/`-`
- сжатие повторяющихся дефисов
- длина 2..64 символа

### Quick examples

Create or get channel:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/channel" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"team_id":"<TEAM_ID>","name":"general","display_name":"General","type":"O"}'
```

Add members:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/channel/members" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"channel_id":"<CH_ID>","user_ids":["<U1>","<U2>"]}'
```

Create/resolve group DM:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/gdm" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_ids":["<U1>","<U2>","<U3>"]}'
```

Import message:

```bash
curl -X POST "http://localhost:8065/plugins/mm-importer/api/v1/import" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_id":"<U>","channel_id":"<CH>","message":"hello"}'
```

## Deployment in this repo

The backend (FastAPI) exposes management endpoints (now all under the `/api` prefix):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/plugin/status`   | GET  | Report discovered local bundle, remote availability, installed/enabled state |
| `/api/plugin/ensure`   | POST | Idempotent: if bundle present (local or fetched remotely) deploy + enable; if only installed but disabled → enable |
| `/api/plugin/deploy`   | POST | Force (re)deployment from current bundle; does not auto-enable |
| `/api/plugin/enable`   | POST | Explicit enable request (used internally by ensure) |
| `/api/plugin/reinstall`| POST | Uninstall (if present) then install from current bundle and enable |

Startup no longer performs an implicit build or deploy (legacy behavior removed). Instead, compose ordering plus the autonomous bundle builder (see below) guarantees a bundle exists before backend attempts an ensure.

### Versioning and Releases

When making any changes to the plugin, the version number in `plugin.json` **MUST** be incremented according to the following Semantic Versioning (SemVer) guidelines:

The version format is `MAJOR.MINOR.PATCH`.

- **MAJOR** version should be incremented for incompatible API changes. This includes:
    - Removing or renaming existing API endpoints.
    - Changing request or response formats in a non-backward-compatible way.
    - Significant refactoring that alters core plugin behavior.

- **MINOR** version should be incremented for adding new functionality in a backward-compatible manner. This includes:
    - Adding new API endpoints.
    - Adding new optional fields to existing API requests or responses.
    - Introducing new features that do not break existing integrations.

- **PATCH** version should be incremented for backward-compatible bug fixes. This includes:
    - Fixing incorrect behavior.
    - Performance improvements.
    - Security patches.

After updating the version, a new plugin bundle must be built and deployed for the changes to take effect. Release-by-release notes live in [`CHANGELOG.md`](./CHANGELOG.md).

### Build (Multi-Stage Docker) — Preferred

We now provide a reproducible multi-stage Docker build that isolates Node/Go toolchains and keeps build caches ephemeral. This avoids polluting the working tree with `node_modules` owned by root.

Quick build (produces `infra/plugin/dist/mm-importer-<version>.tar.gz`):

```bash
DOCKER_BUILDKIT=1 docker build \
  -f infra/plugin/Dockerfile \
  --target package \
  --output type=local,dest=infra/plugin/dist \
  infra/plugin
```

Or use the helper script:

```bash
bash infra/plugin/build-docker.sh
```

Result:
```
infra/plugin/dist/
  mm-importer-<version>.tar.gz
```

No `node_modules` is written to the host; all intermediate layers are cached by Docker.

### Legacy Removal Summary (2025-10)

Removed artifacts / behaviors:

- `infra/plugin/build-dev.sh` and `infra/plugin/Makefile` (host-coupled, produced root-owned `node_modules`)
- Backend startup auto-build / auto-deploy logic (side effects during app init, violated least surprise)
- Direct mounting of build output over source directory (risk of masking tree, hard to debug)

Reasons:

1. Determinism: Multi-stage Docker build and isolated autobuild service provide reproducible artifacts.
2. Clean host tree: No accidental root-owned `node_modules` or partial build outputs.
3. Principle of Least Privilege: Backend no longer needs toolchains or write access to plugin source.
4. Clear lifecycle: Build → (optionally publish remotely) → ensure (deploy+enable) via explicit calls.

Retained for now:

- `build-docker.sh` as the canonical manual packaging helper.

All references in docs and UI now point to the new flow.

### Ephemeral & Remote Bundle Strategy

Goals achieved:

* Keep plugin source read-only in running containers.
* Allow fully offline local development (bundle built once, reused).
* Support remote distribution (e.g. CDN) without baking bundle into backend image.

Components:

1. **Autonomous Conditional Builder (`plugin-autobuild` service)**
  - Runs once at compose up.
  - Uses a lightweight Alpine Go image; installs Node only if `webapp/package.json` exists.
  - Produces `mm-importer-<version>.tar.gz` and a SHA256 file in `infra/test-data/plugin-bundles/`.
  - Idempotent: skips build if the correct tarball already exists and (re)creates `latest.tar.gz` symlink.
  - Static binary build (`CGO_ENABLED=0`) ensures compatibility with minimal Mattermost runtime image.

2. **Bundle Mounting**
  - Backend mounts `infra/test-data/plugin-bundles` at `/plugin-bundles:ro`.
  - Discovery logic searches primary source dist path (legacy) then `/plugin-bundles`.
  - Prevents masking the source tree while giving backend access to artifacts.

3. **Remote Fetch (Optional)**
  - If `PLUGIN_BUNDLE_URL` is set and no local bundle is found, backend attempts a HEAD to confirm availability then opportunistic download to `/tmp` (ephemeral) or local dist fallback.
  - Download failures return a graceful `needs_bundle` status; they do not crash the backend.

4. **Integrity Aids**
  - SHA256 file generated alongside tarball (currently informational; future enhancement: verification before install).
  - Atomic update of `latest.tar.gz` symlink prevents transient broken links.

5. **Enable Flow Hardening**
  - `reinstall` endpoint uninstalls + installs + enables.
  - Polls Mattermost API until plugin appears enabled (short bounded wait) to avoid race conditions.

6. **Static Binary Rationale**
  - Mattermost container image used in dev lacks dynamic loader/libs expected by a dynamically linked Go binary; static linking guarantees runtime availability.

Environment Variable Policy Alignment:

Only external configuration is expressed via env vars (`MM_URL`, `MM_TOKEN`, optional `PLUGIN_BUNDLE_URL`). No feature flags or branching import logic rely on env vars.

### Dev Publish Flow (test-files)

The `test-files` service (simple Python HTTP server on :9000) serves both test fixture data and built plugin bundles under `http://localhost:9000/plugin-bundles/` (mounted from `infra/test-data/plugin-bundles`). This allows simulating remote distribution by setting:

```
PLUGIN_BUNDLE_URL=http://test-files:9000/plugin-bundles/latest.tar.gz
```

Backend status will surface `remote_bundle_available=true` if the HEAD probe succeeds.

### How do I build the plugin with unminified JavaScript?
Setting the `MM_DEBUG` environment variable will invoke the debug builds. The simplest way to do this is to simply include this variable in your calls to `make` (e.g. `make dist MM_DEBUG=1`).

### Additional design docs
- [`MARK_AS_READ.md`](./MARK_AS_READ.md) — rationale, sequence diagrams, SQL batching, and operational guidance for the auto mark-as-read flow.

## Testing

### Mark-as-Read Functionality

The plugin includes functionality to automatically mark imported posts as read for all channel members. To test this:

1. Set up test environment variables:
```bash
export MATTERMOST_URL="http://localhost:8065"
export MATTERMOST_TOKEN="your-admin-token"
export TEST_CHANNEL_ID="your-channel-id"
export TEST_USER_ID="your-user-id"
```

2. Run the test script:
```bash
./infra/plugin/test-mark-as-read.sh
```

The script will:
- Create a test post via the plugin import endpoint
- Verify that the `LastViewedAt` timestamp is updated
- Verify that mention counts are reset to 0
- Clean up the test post

For more details on the mark-as-read implementation, see [MARK_AS_READ.md](./MARK_AS_READ.md).

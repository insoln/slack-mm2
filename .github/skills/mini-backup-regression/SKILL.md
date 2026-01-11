---
name: mini-backup-regression
description: Deterministic mini Slack backup regression workflow (docker compose, plugin ensure, upload, early success counters, log scan). Use when changing importer/exporter logic or verifying end-to-end behavior.
license: Complete terms in LICENSE.txt
compatibility: Requires Docker and docker compose.
---

# Mini Backup Regression (E2E)

This skill describes the canonical deterministic end-to-end regression check using the repo’s mini Slack backup dataset.

## When to use

- After changing importer/exporter logic, progress counters, or pipeline stages
- After changing plugin behavior that affects export
- When diagnosing “works on my machine” vs CI differences

## Canonical command

From repo root:
- `./scripts/run_mini_backup_integration.sh`

The script:
- Brings up compose services
- Ensures the Mattermost plugin is deployed/enabled
- Uploads `infra/test-data/slack-mini-backup.zip`
- Polls `/jobs` and asserts deterministic counters
- Scans logs for errors
- Tears down the stack

## Early success semantics

The regression considers the run successful as soon as:
- stage is `exporting`, and
- final counters match expected values.

Waiting for `done` is not required for the mini-backup check.

## Expected counters (canonical dataset)

Defaults (may be overridden by env vars in the script):
- users=4
- channels=7
- messages=19
- attachments=3
- reactions=4

## Common failures & fixes

- Plugin not enabled → ensure via backend: `POST /plugin/ensure` (also available as `/api/plugin/ensure`)
- Attachment URLs blocked → ensure `IMPORT_URL_PREFIXES` includes `http://test-files:9000`
- Backend not healthy → check backend logs; migrations may still be running

## Updating the mini dataset (only when intentional)

If you must change the mini backup content, follow the policy in `docs/dev.md`:
- Edit unpacked `infra/test-data/slack-mini-backup/`
- Regenerate zip: `python infra/test-data/build_mini_backup_zip.py`
- Update expected counters in `scripts/run_mini_backup_integration.sh`

## Related docs

- Canonical regression script: [scripts/run_mini_backup_integration.sh](../../../scripts/run_mini_backup_integration.sh)
- Mini dataset location: [infra/test-data/slack-mini-backup/](../../../infra/test-data/slack-mini-backup/)
- Mini-backup policy and rules: [docs/dev.md](../../../docs/dev.md)

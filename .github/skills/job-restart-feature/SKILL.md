---
name: job-restart-feature
description: Job restart workflow for Slack-MM2 Sync (POST /api/jobs/{job_id}/restart), including backend behavior, UI conditions, and test expectations. Use when implementing or debugging retry of failed/skipped exports.
license: Complete terms in LICENSE.txt
---

# Job Restart Feature

## When to use

- Implementing or debugging “retry failed/skipped entities” flows
- Adjusting export orchestrator behavior for restarted jobs
- Updating frontend job card UX around restart
- Writing/maintaining unit/integration tests for restart

## API contract

Endpoint:
- `POST /jobs/{job_id}/restart` (also available as `/api/jobs/{job_id}/restart`)

Behavior (high level):
- Only completed jobs can be restarted
- Resets `failed`/`skipped` entities back to `pending`
- Clears their error messages
- Triggers export orchestrator for that job
- Successful entities are not reset

## UI behavior

- Restart button appears only when:
  - job is completed (`success` or `failed`), and
  - there are retryable entities (`failed` or `skipped`)

## Testing expectations

- Unit tests cover: not found, invalid status, no retryables, success path
- Integration tests validate end-to-end restart + export trigger

## Source of truth

- Feature doc: [docs/job-restart-feature.md](../../../docs/job-restart-feature.md)
- Endpoint implementation: [backend/app/api/jobs.py](../../../backend/app/api/jobs.py)
- Integration coverage: [backend/tests/integration/test_job_restart.py](../../../backend/tests/integration/test_job_restart.py)

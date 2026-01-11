---
name: backend-import-export-pipeline
description: Slack-MM2 Sync import/export pipeline rules: single-pass deterministic importer, monotonic progress counters, merge_job_meta semantics, and exporter dependency order. Use when modifying importer/exporter stages or progress behavior.
license: Complete terms in LICENSE.txt
---

# Import/Export Pipeline (Backend)

This skill encodes the project’s core invariants for the unified single-pass pipeline and deterministic progress tracking.

## When to use

- Changing import stages, progress reporting, or `ImportJob.meta`
- Adding new counters (`*_processed`, `*_exported`) or changing counter semantics
- Modifying exporter orchestration order or entity dependency logic
- Debugging progress bars / “early success” behavior used by integration checks

## Canonical pipeline (single pass)

Stages:
- `extracting → users → channels → messages → ready_export → exporting → done`

Key rule:
- Reactions/attachments/custom-emoji references are captured during the single pass through messages (no separate reaction/attachment stages).

## Deterministic progress invariants

- All ingestion counters (`*_processed`) are monotonically non-decreasing.
- When transitioning to `exporting` / `done`, the backend freezes denominators in `meta.totals` and sets `totals_frozen=true`.
- Frontend export progress should use `*_exported` against `meta.totals.*`.

## Atomic meta updates (`merge_job_meta`)

To avoid lost updates on JSONB `meta`, updates must be atomic (single SQL UPDATE) using the project’s merge builder semantics:
- `incr`: atomic increment
- `max`: monotonic maximum (safety)
- `set`: set exact values (stage, flags)
- `nested`: merge nested objects (e.g., `totals`, `durations_ms`)
- `remove`: remove keys (rare)

Rule of thumb:
- Do NOT implement ORM “read-modify-write” loops for `meta`.

## Export ordering

Global type barrier order:
- `user → custom_emoji → channel → message → attachment → reaction`

Meaning:
- A later type must not start exporting until previous type has no remaining `pending` entities across jobs in `exporting`.

## If you add a new counter or entity type

Checklist:
- Update the importer to increment the new `*_processed` counter only after the entity meets base invariants.
- Update export logic and/or exported counters if the frontend needs export progress for that type.
- Update docs where counters are described.
- Update the mini-backup regression expected values if it changes deterministic totals.

## Related docs

- Pipeline + dev workflow notes: [docs/dev.md](../../../docs/dev.md)
- Backend overview: [backend/README.md](../../../backend/README.md)
- Import stages + counters implementation: [backend/app/services/backup/orchestrator.py](../../../backend/app/services/backup/orchestrator.py)
- Atomic meta updates implementation: [backend/app/services/backup/meta_utils.py](../../../backend/app/services/backup/meta_utils.py)
- Export module overview: [backend/app/services/export/README.md](../../../backend/app/services/export/README.md)
- Export ordering implementation: [backend/app/services/export/orchestrator.py](../../../backend/app/services/export/orchestrator.py)

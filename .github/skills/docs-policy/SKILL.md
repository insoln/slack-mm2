---
name: docs-policy
description: Documentation policy for Slack-MM2 Sync. Use when adding/updating Markdown docs so they stay discoverable, focused, and non-duplicative across backend/frontend/infra/plugin.
license: Complete terms in LICENSE.txt
---

# Documentation Policy

## When to use

- Adding a new feature doc in `docs/` or component directories
- Updating behavior that is already documented
- Reviewing a PR that includes docs changes

## Core rules

- Prefer updating an existing component `README.md` over creating a new doc.
- Create a dedicated doc only when it captures:
  - Cross-component behavior
  - Operational nuance / manual steps
  - Non-trivial reasoning (algorithms, SQL patterns, race-condition mitigations)
  - Recovery / rollback guidance
- Place the doc next to the code it describes (backend docs in `backend/`, plugin docs in `infra/plugin/`, etc.).
- Keep docs reproducible today; do not include secrets.

## Checklist (author/reviewer)

- README links to the new doc
- Doc scopes itself clearly (feature/subsystem)
- Steps are accurate and reproducible
- Diagrams/scripts match current behavior
- No credentials or customer data

## Source of truth

- [docs/documentation-policy.md](../../../docs/documentation-policy.md)

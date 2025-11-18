# Documentation Policy

This policy defines when and how to add feature-specific Markdown files inside this repo. The goals are to keep `README` files lean, capture tacit design knowledge, and make future maintenance easier. By default, each directory SHOULD only contain a single `README.md` that covers quick-start and basic context. Create additional docs only when the "absolute necessity" criteria below apply, and remove redundant content from READMEs to avoid duplication.

## What to include in a doc

1. **Cross-component behavior** – The change touches multiple services (e.g., backend ↔ plugin ↔ Mattermost) and the interaction is non-obvious.
2. **Operational nuance** – There are manual steps, scripts, or verification flows that engineers must repeat outside automated tests.
3. **Non-trivial reasoning** – The solution requires background (algorithms, SQL patterns, performance tradeoffs, race-condition mitigations) that is hard to infer from code.
4. **Recovery / rollback guidance** – There are failure modes or cleanup steps that would be risky to lose.

Think about updating first the existing `README` for that component instead of introducing new files. When a dedicated doc is justified, keep it strictly focused on the material that cannot fit in the README without causing information overload.

## Where to place docs

- Store the doc next to the code it describes (e.g., plugin-related docs go in `infra/plugin/`, backend docs under `backend/`, etc.).
- Use descriptive filenames.
- Keep diagrams/text in Markdown/ASCII when possible so diffs remain reviewable without external tooling.

## Maintenance expectations

- Treat docs as part of the definition of done. When you modify the behavior they describe, update the doc in the same PR.
- If a feature becomes legacy or is removed, either delete the doc or mark it clearly as deprecated.
- During code review, confirm the documentation checklist below has been followed.

## Documentation checklist for reviewers/authors

1. Does the README mention or link to the new doc so teammates can find it?
2. Does the doc clearly scope itself (feature, subsystem, owner)?
3. Are instructions accurate and reproducible today?
4. Were diagrams or scripts updated to reflect the latest behavior?
5. Are secret values, credentials, or customer data absent from the doc?

Adhering to this policy keeps top-level READMEs approachable while still preserving the “why” and “how” that experienced maintainers rely on.
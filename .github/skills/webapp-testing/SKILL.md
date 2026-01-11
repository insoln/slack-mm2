---
name: webapp-testing
description: Toolkit for interacting with and testing local web applications using Playwright. Use when verifying frontend behavior (Vite/React UI), reproducing UI bugs, or building minimal E2E/smoke scripts.
license: Complete terms in LICENSE.txt
---

# Web Application Testing (Playwright)

Use this skill to create small, purpose-built Playwright scripts for verifying the frontend.

## When to use

- Smoke testing the React/Vite UI after changes
- Reproducing UI regressions (selectors, rendering, API polling)
- Capturing screenshots and browser console logs

## Recommended approach

1. Ensure the app is running (typically via dev compose):
   - `docker compose -f infra/docker-compose.dev.yml up -d`
   - Frontend: `http://localhost:5173`
2. Use reconnaissance-then-action:
   - Navigate
   - Wait for a stable load state
   - Inspect DOM / take a screenshot
   - Then implement precise actions/assertions

Note:
- This repo doesn’t vendor Playwright; install it in your chosen environment per Playwright’s official docs.

## Minimal Playwright script (Python)

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")

    page.screenshot(path="/tmp/ui.png", full_page=True)

    # Example: print console logs (optional)
    # page.on("console", lambda msg: print(msg.type, msg.text))

    browser.close()
```

## Common pitfalls

- Don’t inspect the DOM before `networkidle` on dynamic apps.
- Prefer stable selectors (`role=`, `text=`, test IDs) over brittle CSS chains.

## Related docs

- Frontend overview: [frontend/README.md](../../../frontend/README.md)
- Dev environment and ports: [docs/dev.md](../../../docs/dev.md)

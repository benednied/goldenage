# Browser and accessibility gate

The `browser-accessibility` CI job runs the critical flows in a real headless
Chromium browser against a fresh local SQLite database for each test. The seed
contains only synthetic data (`browser@example.test`, an example case, and one
due activity); it never reads production mail, credentials, or files.

Run the gate locally after installing the browser-only tools into the existing
uv environment:

```bash
uv sync --frozen --extra dev
uv pip install --python .venv/bin/python -r tests/browser/requirements.txt
./.venv/bin/python -m playwright install --with-deps --only-shell chromium
PLAYWRIGHT_ARTIFACT_DIR=test-results \
  uv run --no-sync pytest -q tests/browser
```

The test suite covers:

- login, keyboard order, visible focus, labels, language, and the invalid-login
  error state;
- worklist and case detail at desktop and narrow viewports;
- a server-side resolution error after an HTMX request;
- a successful HTMX mutation, including the status announcement and focus
  remaining in the updated workspace; and
- axe-core checks on initial, error, detail, narrow, and post-mutation states.

On a failure, the suite writes a PNG screenshot, Playwright trace, and the
sanitized local server log to `test-results/`. CI uploads that directory for
14 days. The fixture creates a separate temporary database per test, and the
test data is discarded with the temporary directory.

## Manual accessibility review

Automated checks cannot establish that the product is understandable or usable
for every assistive technology. For UI changes, review both a representative
desktop viewport and a 390px-wide viewport:

- tab through login, worklist, detail, intake, and settings in a meaningful DOM
  order; confirm every stop has a visible focus indicator and no decorative
  element receives focus;
- confirm labels, instructions, and validation text are announced with the
  associated control, including failed login and resolution forms;
- confirm status/error announcements are understandable, do not repeat stale
  actions, and move focus to the updated HTMX region without trapping it;
- use browser zoom at 200% and 400% and confirm content reflows without
  horizontal scrolling or loss of controls;
- verify the page language with a screen reader and check headings/landmarks;
- test keyboard-only recovery after a failed form and after an HTMX swap; and
- inspect reduced-motion behavior and contrast in the actual browser, since
  axe does not validate the full visual or interaction experience.

Record the browser, viewport, zoom, assistive technology, date, and any
exception in the pull request. A rule exception must name the affected element,
reason, owner, and follow-up issue; do not disable an axe rule globally.

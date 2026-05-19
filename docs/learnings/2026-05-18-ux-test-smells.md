# UX test smells from the intake review

The failing pattern was not missing line coverage. The suite had 100% coverage, but several
tests asserted that a response contained a few expected strings after a form post. A senior
reviewer reading those tests would not have seen the real UX contracts: privacy boundaries,
keyboard order, pending states, or whether stale actions remained visible.

## What went off the rails

- Tests proved that mail import could render candidates, but not that the default demo runtime
  avoided touching a real desktop mailbox. Any adapter that can read local user data should have a
  test for the disabled/default state and a separate test for the explicitly configured state.
- Tests checked that imported mail produced an intake panel, but not that the imported item became
  the next thing in DOM order. CSS reordering hid a keyboard and assistive-technology mismatch.
- Tests checked for action buttons, but not for pending feedback. Slow HTMX posts need visible
  status text or indicators that can be found by assistive technology.
- Tests checked that upload accepted a file, but not that file selection was an explicit user
  decision. Auto-submitting on `change` or `drop` makes validation errors and accidental uploads
  feel abrupt.
- Tests checked page content, but not responsive invariants. Fixed multi-column layouts need a
  test that a mobile breakpoint exists for the same selector.
- Tests checked auth redirects and form outcomes, but not focus priority. A decorative video pane
  was keyboard-focusable before the sign-in form.

## How to find similar patterns later

Search for tests that only assert fragments after state-changing requests:

```sh
rg 'assert .* in .*\\.text|assert .* not in .*\\.text' tests
```

For each match, ask what user contract the text is standing in for. If the contract is ordering,
privacy, loading feedback, destructive action, accessibility, or responsive layout, add a direct
assertion for that property.

Search templates for HTMX posts without indicators:

```sh
rg 'hx-post|hx-get' src/goldenage/web/templates
rg 'hx-indicator|role="status"|aria-live' src/goldenage/web/templates src/goldenage/web/static
```

Every slow or external integration action should have a status indicator and should avoid leaving
stale duplicate actions visible after completion.

Search for visual reordering and hidden controls:

```sh
rg 'order:|position: absolute|clip: rect|aria-hidden|role="button"|tabindex' src/goldenage/web
```

Each hit deserves a keyboard-order check. If visual order differs from DOM order, prefer fixing the
markup order before relying on CSS.

Search for implicit local integrations:

```sh
rg 'auto|osascript|subprocess|platform\\.system|shutil\\.which|COM|desktop' src/goldenage
```

Any integration that can read local files, mail, contacts, or system state should be disabled by
default unless the user explicitly opts in with an environment variable or fixture.

Search for responsive layouts with fixed minimum columns:

```sh
rg 'grid-template-columns|minmax\\(' src/goldenage/web/static
```

For every fixed minimum multi-column layout, require a breakpoint that collapses it before the
minimum width plus page padding exceeds common mobile widths.

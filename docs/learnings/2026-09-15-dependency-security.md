# Dependency security update (SEC-01)

Issue: https://github.com/benednied/goldenage/issues/2

Checked on 2026-09-15 against origin/master at 3657756. This branch is independent
of the artifact storage change in PR #23 and does not depend on its Windows storage verification.
No application handlers, schema, or environment variables change.

## Resolution

| Package | Before | After |
| --- | --- | --- |
| FastAPI | 0.135.3 | 0.141.1 |
| Starlette | 1.0.0 | 1.6.0 |
| python-multipart | 0.0.26 | 0.0.32 |
| Click | 8.3.2 | 8.5.0 |
| IDNA | 3.11 | 3.19 |
| pytest | 8.4.2 | 9.1.1 |

The initial security update changed these six versions. At the maintainer’s
request, `uv lock --upgrade` then refreshed all remaining locked packages to the
latest compatible stable releases available on PyPI on 2026-09-15. This includes
Uvicorn 0.53.0, Psycopg 3.3.5, Pydantic 2.13.5, Ruff 0.16.7, and ty 0.0.81.
`uv pip list --outdated` reports only pydantic-core: 2.49.0 is available, but
Pydantic 2.13.5 requires exactly 2.46.5. That required version is retained.
The universal lock also advances pywin32 311 to 312 and tzdata 2026.1 to 2026.4;
Windows-only dependencies were resolved but not installed or natively tested.

 FastAPI 0.141.1 declares
`starlette>=0.46.0`; the chosen Starlette satisfies it without a resolver override.
All three web packages declare Python >=3.10 and were installed on Python 3.14.4.
Direct minimum versions preserve the tested FastAPI and multipart baseline. A
Starlette constraint is explicit because FastAPI still permits vulnerable releases.
The pytest upper bound moves to <10 to allow its security fix, unavailable in 8.x.
Click and IDNA remain transitive dependencies, updated in the committed lockfile.

## Advisory review and reachable APIs

The installed runtime and dev environment was scanned with **pip-audit 2.10.1**,
using its default **PyPI vulnerability service**. The initial scan returned 23
records, representing 12 distinct advisories across five packages (some records
were duplicates). The fresh final environment reports zero known vulnerabilities.
The [machine-readable evidence](evidence/2026-09-15-dependency-audit.json) includes
all initial IDs, aliases, fixed versions, and the final installed-package report.
No advisory was ignored or classified as a false positive. GoldenAge itself is a
local unpublished package and the scanner cannot audit it against PyPI. The
Windows-only pywin32 dependency is not installed on macOS and was not scanned;
its native behavior was not validated by this scan.

| Advisory | Affected version range / fix | Application exposure |
| --- | --- | --- |
| [GHSA-5rvq-cxj2-64vf](https://github.com/advisories/GHSA-5rvq-cxj2-64vf) | multipart <0.0.30 / 0.0.30 | URL-encoded `Form` inputs reach QuerystringParser, including login. |
| [GHSA-6jv3-5f52-599m](https://github.com/advisories/GHSA-6jv3-5f52-599m) | multipart <0.0.30 / 0.0.30 | The same parser previously treated semicolons as separators. |
| [GHSA-pp6c-gr5w-3c5g](https://github.com/advisories/GHSA-pp6c-gr5w-3c5g) | multipart <0.0.27 / 0.0.27 | Artifact/profile uploads reach multipart header parsing. |
| [GHSA-v9pg-7xvm-68hf](https://github.com/advisories/GHSA-v9pg-7xvm-68hf) | multipart <0.0.31 / 0.0.31 | No direct `python_multipart.parse_form` calls; Starlette feeds parsers from ASGI streams. |
| [GHSA-82w8-qh3p-5jfq](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq) | Starlette >=0.4.1,<1.3.1 / 1.3.1 | URL-encoded forms previously bypassed field-count and field-size limits. |
| [GHSA-86qp-5c8j-p5mr](https://github.com/advisories/GHSA-86qp-5c8j-p5mr) | Starlette 1.0.0 flagged / 1.0.1 | Framework URL reconstruction consumes Host and path values. |
| [GHSA-jp82-jpqv-5vv3](https://github.com/advisories/GHSA-jp82-jpqv-5vv3) | Starlette 1.0.0 flagged / 1.3.0 | Framework URL reconstruction uses request paths. |
| [GHSA-wqp7-x3pw-xc5r](https://github.com/advisories/GHSA-wqp7-x3pw-xc5r) | Starlette 1.0.0 flagged / 1.1.0 | StaticFiles serves static/profile directories; upstream Windows UNC fix included, not natively tested here. |
| [GHSA-x746-7m8f-x49c](https://github.com/advisories/GHSA-x746-7m8f-x49c) | Starlette 1.0.0 flagged / 1.1.0 | No application HTTPEndpoint subclass; FastAPI function routes used. |
| [GHSA-47fr-3ffg-hgmw](https://github.com/advisories/GHSA-47fr-3ffg-hgmw) | Click <=8.3.2 / 8.3.3 | Uvicorn CLI dependency; no application `click.edit()` call. |
| [GHSA-65pc-fj4g-8rjx](https://github.com/advisories/GHSA-65pc-fj4g-8rjx) | IDNA <3.15 / 3.15 | Transitive networking/HTTP-client dependency. |
| [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g) | pytest <9.0.3 / 9.0.3 | Test suite uses temporary-directory fixtures on macOS/Unix. |

The source review confirms form and upload exposure. It does not claim exploit
reproduction for every advisory. The dependency fix does not provide a total
request/file byte cap or content validation; those remain SEC-03 (#4). Scheduled
scanning remains SEC-06.

## Verification

Baseline: 93 tests passed. Updated: 99 tests passed. Six added cases verify the
1000-field boundary, the 1 MiB field boundary, literal semicolons in login
passwords, and rejection of multipart bodies without a boundary. Existing tests
cover successful artifact uploads, unsupported/empty uploads, onboarding,
profile-image rejection, and valid/invalid credentials.

A new environment at `/tmp/goldenage-sec01-latest-fresh` was created with:

```sh
UV_PROJECT_ENVIRONMENT=/tmp/goldenage-sec01-latest-fresh uv sync --frozen --extra dev
UV_PROJECT_ENVIRONMENT=/tmp/goldenage-sec01-latest-fresh uv run --frozen --extra dev ruff check .
UV_PROJECT_ENVIRONMENT=/tmp/goldenage-sec01-latest-fresh uv run --frozen --extra dev ruff format --check .
UV_PROJECT_ENVIRONMENT=/tmp/goldenage-sec01-latest-fresh uv run --frozen --extra dev ty check
UV_PROJECT_ENVIRONMENT=/tmp/goldenage-sec01-latest-fresh uv run --frozen --extra dev pytest -q
uvx pip-audit==2.10.1 --path /tmp/goldenage-sec01-latest-fresh/lib/python3.14/site-packages --format json
```

All commands exited successfully. The obsolete unused-ignore suppression was
removed for the newer ty release, and type checking is clean. Pytest still emits
lifecycle and upstream httpx/AnyIO deprecation warnings. These do not prevent
execution and require no handler changes for this upgrade.

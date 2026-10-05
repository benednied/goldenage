# Continuous integration and branch protection

The `CI` workflow runs for every pull request and for pushes to `master`, the
default branch reported by the ENG-01 audit. Its stable jobs are named
`quality` and `postgres-integration`. The first runs the fast checks and excludes
the PostgreSQL marker; the second starts the pinned PostgreSQL 17 service and
must run the real integration suite. Both jobs use Python 3.14 and install the
dependency graph from `uv.lock`.

```bash
uv sync --frozen --extra dev
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync ty check
uv run --no-sync pytest -q -m "not postgres_integration"
```

The `postgres-integration` job uses temporary CI credentials, a health check,
and `GOLDENAGE_TEST_POSTGRES_DSN`. The test fixture creates and drops a unique
schema per test, so no application tables are shared between tests. A missing
or unreachable database is a test failure, not a skip.

The workflow has read-only repository permissions, a 15-minute timeout, and
cancels superseded runs for the same pull request or branch. It does not need
secrets. `actions/checkout` and `actions/setup-python` are pinned to immutable
commit SHAs; their trailing version comments identify the reviewed release.
`uv` is installed with the explicit version `0.6.14`.

## Handling a failed check

Open the failed job, reproduce the named command locally, correct the failure,
and push the correction. A new push cancels the obsolete pull-request run.

## GitHub administrator handoff

Repository administration is intentionally not encoded in this checkout.
After a pull request has produced successful `quality` and
`postgres-integration` runs, an administrator
must configure the existing protection mechanism for the actual default branch
(prefer a ruleset when none already exists; otherwise extend the existing
ruleset or classic branch protection rather than creating competing rules):

1. Require a pull request before merging and require both the `quality` and
   `postgres-integration` status checks. Require the real reviewer count
   supported by the maintainer team (one when at least two maintainers can
   review; otherwise no mandatory review until a second eligible reviewer
   exists).
2. Apply the rule to the actual default branch. This workflow currently assumes
   `master` from the ENG-01 audit; update `.github/workflows/ci.yml` first if
   GitHub reports a different default branch.
3. Disallow force pushes and branch deletion. Limit bypasses to the smallest
   practical maintainer/emergency group and record its members in the GitHub
   ruleset description.
4. Create a disposable test pull request with a reversible failure (for example
   an unused import), confirm that `quality` fails and merging is blocked, then
   remove the failure and confirm the green pull request is mergeable.
5. Verify the final configuration and check name with GitHub CLI:

   ```bash
   gh repo view --json defaultBranchRef
   gh api repos/OWNER/REPO/rulesets
   gh api repos/OWNER/REPO/branches/DEFAULT/protection
   ```

The PostgreSQL job is deliberately separate from the fast quality job so local
unit-test runs remain quick while database regressions block merging.

The quality job also runs `python -m goldenage.migration_validation` when the
migration validator is present. This permits the CI and migration-convention PRs
to land in either order while enforcing the check as soon as both are integrated.

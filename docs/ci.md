# Continuous integration and branch protection

The `CI` workflow runs for every pull request and for pushes to `master`, the
default branch reported by the ENG-01 audit. Its required job is named
`quality`. It uses Python 3.14, installs the dependency graph from `uv.lock`,
and runs only these existing checks:

```bash
uv sync --frozen --extra dev
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync ty check
uv run --no-sync pytest -q
```

The workflow has read-only repository permissions, a 15-minute timeout, and
cancels superseded runs for the same pull request or branch. It does not need
secrets. `actions/checkout` and `actions/setup-python` are pinned to immutable
commit SHAs; their trailing version comments identify the reviewed release.
`uv` is installed with the explicit version `0.6.14`.

## Handling a failed check

Open the failed `quality` job, reproduce the named command locally, correct the
failure, and push the correction. A new push cancels the obsolete pull-request
run. Do not add future checks (coverage, PostgreSQL, packaging, or security) to
branch protection until their jobs have run successfully and their exact GitHub
check names are known.

## GitHub administrator handoff

Repository administration is intentionally not encoded in this checkout.
After a pull request has produced a successful `quality` run, an administrator
must configure the existing protection mechanism for the actual default branch
(prefer a ruleset when none already exists; otherwise extend the existing
ruleset or classic branch protection rather than creating competing rules):

1. Require a pull request before merging and require the `quality` status
   check. Require the real reviewer count supported by the maintainer team
   (one when at least two maintainers can review; otherwise no mandatory review
   until a second eligible reviewer exists).
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

The checkout is deliberately limited to the four baseline checks. Coverage,
database, distribution, and security gates remain separate follow-up work.

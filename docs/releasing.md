# Releasing GoldenAge

This document is the release runbook. It deliberately creates a GitHub draft
release only; publishing to PyPI or another package registry is not configured.
The release owner is the maintainer who approves the version-bump pull request,
creates the immutable tag, and reviews the draft before publishing it.

## Version and tag policy

`pyproject.toml` contains the one authoritative application/package version in
`[project].version`. The version in `uv.lock` is generated package metadata and
must be refreshed, but it is not edited by hand or treated as a second source
of truth. The workflow checks the built wheel and source distribution as well.

Use this release scheme:

- Versions are `MAJOR.MINOR.PATCH`. While the project is below `1.0.0`, a
  breaking API, configuration, or migration change increments `MINOR`; a
  compatible feature also increments `MINOR`; a compatible bug or security fix
  increments `PATCH`.
- Once `1.0.0` is reached, breaking changes increment `MAJOR`, compatible
  features increment `MINOR`, and fixes increment `PATCH`.
- Pre-releases append `aN`, `bN`, or `rcN`, for example `0.2.0rc1`. They are
  ordered before the corresponding final version and use the same migration
  and release-note requirements. Do not reuse a pre-release number.
- Every release tag is exactly `v<version>`, such as `v0.2.0rc1`. The tag,
  `pyproject.toml`, lock metadata, and both distribution metadata records must
  agree. Existing tags are immutable and must never be moved.

Before starting, compare the checkout with GitHub rather than assuming that an
audit snapshot is current:

```bash
git fetch --tags origin
git tag --list 'v*' --sort=-version:refname
gh release list --limit 20
```

The current checkout declares `0.1.0`; the first release is still considered
unreleased until its tag and draft have been reviewed.

## What belongs in the changelog

Maintain [`CHANGELOG.md`](../CHANGELOG.md) under `Unreleased`. For each release,
the preparation pull request must move the entries into `## [version] - YYYY-MM-DD`
and include the applicable headings:

- **Added**, **Changed**, **Fixed**, and **Security** for user-visible behavior
  and security fixes.
- **Configuration** for new, removed, renamed, or defaulted environment
  variables, including whether a restart is required.
- **Schema and upgrade notes** for PostgreSQL/SQLite migrations, ordering,
  data backfills, compatibility windows, and rollback limits.

The release owner must not include credentials, tokens, private data, local
`.env` files, build directories, or generated application data in the commit or
the distributions. Release notes must call out any manual action before or
after deployment.

## Release-ready commit

A release is ready only when all of the following are true:

1. The version bump and dated changelog section are merged on the repository's
   actual default branch. The workflow discovers that branch from GitHub; the
   existing CI documentation currently assumes `master`.
2. The release owner creates an annotated tag on that exact merged commit. Do
   not tag a feature branch, and do not force-push or move a tag. Repository
   administrators should protect `v*` tags with a ruleset or tag protection.
3. The locked Python 3.14 toolchain passes the `quality` checks for that exact
   commit: Ruff lint, Ruff format verification, ty, migration validation when
   present, and pytest. A failed check prevents the draft release.
4. Wheel and SDist builds pass metadata validation and install/import smoke
   tests in clean virtual environments. The generated `SHA256SUMS` file covers
   both files.
5. The release notes identify the version, tag, source commit, toolchain,
   migration/configuration guidance, and the checksums. The draft is reviewed
   before anyone publishes it.

## Standard release procedure

1. Confirm tags/releases and the default branch using the commands above.
2. In one pull request, update `[project].version` in `pyproject.toml`, run
   `uv lock` so `uv.lock` reflects it, and move the relevant `CHANGELOG.md`
   entries into a dated version section. Include migration and upgrade notes.
3. Run the local checks and package smoke tests:

   ```bash
   uv sync --frozen --extra dev
   uv run --no-sync ruff check .
   uv run --no-sync ruff format --check .
   uv run --no-sync ty check
   uv run --no-sync python -m goldenage.migration_validation
   uv run --no-sync pytest -q
   uv build --wheel --sdist --out-dir dist
   python scripts/check_distribution.py dist VERSION
   ```

   Replace `VERSION` with the exact value from `pyproject.toml`. The hosted
   workflow repeats these checks, so a local-only result is not a release.
4. After the pull request is merged, resolve the merge commit and create the
   tag without changing the commit:

   ```bash
   git switch master
   git pull --ff-only origin master
   git tag -a vVERSION -m "Release vVERSION" HEAD
   git push origin vVERSION
   ```

   Replace `master` if GitHub reports another default branch and replace both
   `VERSION` instances. The tag push starts the release workflow.
5. Review the workflow's GitHub draft. Confirm its target commit is the merged
   default-branch commit, its version/tag agree, both distributions install,
   `SHA256SUMS` verifies, and the upgrade notes are complete. Publish the draft
   manually only after that review.

The workflow can also be started through **Actions → Release draft → Run
workflow** with an existing `v...` tag. This is an explicit recovery or
pre-release path, not a way to build an arbitrary branch: the workflow still
requires the tag to resolve to a commit contained in the default branch.

## Failure and repeat behavior

- A malformed tag, a tag/version mismatch, stale lock metadata, a commit not
  contained in the default branch, a missing changelog section, a failed
  required check, or a failed distribution smoke test stops the workflow before
  a draft is created.
- The workflow refuses to run when a GitHub release already exists for the tag,
  including an existing draft. It never uses `--clobber` and never overwrites
  published assets. Correct a failed draft manually under maintainer review or
  use a new version/tag; do not move the original tag.
- A rerun before release creation is safe. A rerun after partial draft creation
  stops at the existing-release guard and requires the release owner to inspect
  the draft and decide whether to remove that draft through GitHub before a
  carefully reviewed retry.
- No registry credentials are requested or used. Adding a registry publish
  step requires a separate decision covering trusted publishing, permissions,
  and rollback.

## Controlled pre-release walkthrough

To exercise the process without publishing a final version, prepare a normal
release pull request with a new version such as `0.2.0rc1`, a matching dated
changelog section, and the required upgrade notes. Merge it, create
`v0.2.0rc1` on the merge commit, and let the workflow produce a draft. Validate
the draft and then delete the draft if it was only a rehearsal. Never reuse the
tag or silently turn that tag into a final release; prepare `0.2.0` as a new
version and commit instead.

## Administrator setup

GitHub repository administration is intentionally not encoded in this checkout.
Before the first real release, an administrator should:

- protect `master` (or the actual default branch) and require the existing
  `quality` check before merge;
- protect `v*` tags against deletion and force updates;
- grant Actions the minimum repository permission needed for the draft workflow
  (`contents: write` only in its release job); and
- verify that the workflow can create a draft without granting package-registry
  or environment secrets.

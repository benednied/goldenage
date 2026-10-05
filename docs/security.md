# Security operations (SEC-06)

This record describes the controls that are committed to the repository and the
settings that only a GitHub repository administrator can enable. It was reviewed
against this checkout on 2026-10-05. A local checkout cannot prove the state of
GitHub's Security and quality settings, so unverified controls are explicitly
marked as such.

## Repository controls

| Control | Repository evidence | Enforcement |
| --- | --- | --- |
| Python dependency updates | `.github/dependabot.yml`, `package-ecosystem: uv`, root `pyproject.toml` and `uv.lock` | Weekly Dependabot PRs, maximum five open Python update PRs, grouped patch/minor updates |
| GitHub Actions updates | `.github/dependabot.yml`, `package-ecosystem: github-actions` | Weekly PRs, maximum three open action update PRs, grouped updates |
| Lockfile consistency | `.github/workflows/ci.yml` uses `uv sync --frozen` | A Python PR with a stale or missing lockfile fails CI |
| Advisory scanning | `.github/workflows/dependency-security.yml` | OSV-Scanner scans `uv.lock` on PRs, pushes, Mondays, and manual dispatch; every unapproved finding fails |
| Exception policy | `osv-scanner.toml`, `scripts/validate_advisory_exceptions.py` | Expired or undocumented exceptions fail the policy job; owner, evidence, and `ignoreUntil` are required |
| Secret scanning | `.github/workflows/secret-scan.yml` | Gitleaks receives full history (`fetch-depth: 0`) on PRs, default-branch pushes, Mondays, and manual dispatch; output channels that could repeat secrets are disabled |
| Private report form | `.github/VULNERABILITY_REPORT.yml`, `SECURITY.md` | GitHub uses the form when private vulnerability reporting is enabled |

The scanners fail closed. The OSV workflow currently blocks any finding that is
not in the reviewed exception list, not only High/Critical findings. This is
intentional because it prevents a low or unclassified advisory from being
silently accepted while it is waiting for triage.

## GitHub administrator verification

The following settings are not represented by files in a checkout. At the time of
this review they were **not verifiable here** because no authenticated GitHub
administration/API session was available; this must not be read as “enabled”. An
administrator should verify each item in the repository's **Settings → Security
and quality → Advanced Security** page and record the result in the pull request
or release notes:

| GitHub control | Required state | Status from this checkout |
| --- | --- | --- |
| Dependency graph | Enabled | Not verifiable |
| Dependabot alerts | Enabled | Not verifiable |
| Dependabot security updates | Enabled | Not verifiable |
| Secret scanning | Enabled | Not verifiable |
| Push protection | Enabled | Not verifiable |
| Private vulnerability reporting | Enabled; test the “Report a vulnerability” button | Not verifiable |
| Default Actions token | Read-only by default; no write token for fork PRs | Workflow files request least privilege, repository default unverified |
| Ruleset/branch protection | Protect the actual default branch; require successful quality, dependency, and secret checks after first green runs | Not verifiable |

Recommended administrator checks (do not paste alert contents or secret values
into tickets or logs):

```bash
gh repo view benednied/goldenage --json defaultBranchRef,visibility
gh api repos/benednied/goldenage --jq '.security_and_analysis'
gh api repos/benednied/goldenage/rulesets
gh api repos/benednied/goldenage/branches/DEFAULT/protection
```

Replace `DEFAULT` with the branch reported by the first command. The security
settings UI remains authoritative when an endpoint is unavailable because of
plan, visibility, or permission differences.

## Initial scan record

The committed tree contains no intentionally supplied credentials. The prior
dependency audit recorded a clean final `pip-audit` environment on 2026-09-15;
the new scheduled OSV scan is the durable lockfile check going forward.

The external Git directory for this pinned worker checkout resolves to a
controller-managed mirror that is not readable in this execution environment.
Consequently, a complete local Git-history scan could not be independently run in
this worker, and no claim of a clean historical scan is made here. The first
successful `secret-scan` run on the default branch must be treated as the history
baseline. Any real finding must be handled privately: revoke/rotate the
credential first, preserve only redacted evidence, then decide whether history
rewriting is necessary.

## Exception process

Do not add an advisory ID to `osv-scanner.toml` merely to make CI green. An
exception is only for a demonstrated false positive or a documented non-applicable
case. The `reason` must contain `owner=<GitHub handle>`,
`evidence=<redacted link or file>`, and the words `false positive`; the expiry
date must be in the future. Re-check every exception before its expiry and remove
it when the advisory is fixed or the evidence changes.

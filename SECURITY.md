# Security policy

## Supported versions

GoldenAge has no published release line yet. Until a release policy is announced,
security fixes are made only on the current default branch and the most recent
tagged release, if one exists. Older commits and untagged forks are not supported.

| Version | Supported |
| --- | --- |
| Current default branch | Yes |
| Latest tagged release, if any | Yes |
| Older tags or commits | No |

## Private reporting

Please do not open a public issue, discussion, pull request, or paste a suspected
secret into a log. Use GitHub's private vulnerability reporting form from the
repository's **Security and quality → Advisories → Report a vulnerability** page:

[Open the private advisory reporting page](https://github.com/benednied/goldenage/security/advisories)

The repository administrator must enable private vulnerability reporting for that
button to be available. If it is not visible, stop and contact a repository
maintainer through an already-private GitHub channel; do not use a public issue to
request a security contact. The checked-in [private report form](.github/VULNERABILITY_REPORT.yml)
defines the minimum safe information to provide.

When reporting, use synthetic values and redact secrets. Include the affected
commit or version, impact, safe reproduction steps, and any evidence that does not
expose credentials or personal data. If a credential may have been exposed, say
which kind of credential was affected without including its value.

## Triage and response

- We acknowledge a report within 3 business days.
- We provide an initial severity and reproducibility assessment within 10 business days.
- We send a status update at least every 10 business days while the report is open.
- Target remediation windows are 7 days for Critical, 30 days for High, 60 days for Moderate, and the next planned release for Low findings. These are targets, not guarantees.
- We coordinate disclosure timing with the reporter and publish a security advisory only after a fix or mitigation is available.
- Suspected exposed credentials are revoked or rotated before history cleanup is planned.

Reports that contain live secrets will be handled as exposure incidents; do not
repeat the value in follow-up messages. Maintainers may ask for a redacted hash,
timestamp, affected location, or provider alert instead.

## Dependency and secret controls

Dependency updates are managed by [Dependabot](.github/dependabot.yml). The
committed `uv.lock` is scanned by the scheduled OSV-Scanner workflow and by the
default-branch/PR workflow. Any unapproved advisory blocks the check; therefore
High and Critical findings cannot pass. Secret scanning runs with Gitleaks against
the full fetched history and pull requests with comments, artifacts, summaries,
and secret values disabled.

GitHub-hosted controls such as Dependabot alerts, security updates, secret
scanning, push protection, and private vulnerability reporting require repository
administration and are tracked in [the security operations record](docs/security.md).

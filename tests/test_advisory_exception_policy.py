from datetime import date
from pathlib import Path

from goldenage.security.advisory_exceptions import validation_errors


def test_empty_exception_policy_is_valid() -> None:
    assert validation_errors(Path("osv-scanner.toml"), today=date(2026, 10, 5)) == []


def test_expired_synthetic_exception_fails_closed(tmp_path) -> None:
    config = tmp_path / "osv-scanner.toml"
    config.write_text(
        """
[[IgnoredVulns]]
id = "GHSA-0000-0000-0000"
ignoreUntil = "2026-10-04"
reason = "owner=@synthetic; evidence=https://example.invalid/review; false positive test"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validation_errors(config, today=date(2026, 10, 5))

    assert errors == ["IgnoredVulns entry 1 expired on 2026-10-04"]


def test_exception_requires_owner_evidence_and_false_positive_reason(tmp_path) -> None:
    config = tmp_path / "osv-scanner.toml"
    config.write_text(
        """
[[IgnoredVulns]]
id = "GHSA-0000-0000-0000"
ignoreUntil = "2026-12-31"
reason = "temporary review"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validation_errors(config, today=date(2026, 10, 5))

    assert errors == [
        "IgnoredVulns entry 1 reason must include owner=",
        "IgnoredVulns entry 1 reason must include evidence=",
        "IgnoredVulns entry 1 reason must explain the false positive",
    ]

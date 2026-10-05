"""Validate the metadata required for OSV-Scanner advisory exceptions."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

_ADVISORY_ID = re.compile(r"(?:CVE|GHSA|OSV|PYSEC)-[A-Za-z0-9][A-Za-z0-9.-]*")
_REQUIRED_REASON_MARKERS = ("owner=", "evidence=")


def validation_errors(path: Path, *, today: date | None = None) -> list[str]:
    """Return policy violations in an OSV-Scanner configuration file."""

    today = today or date.today()
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError:
        return [f"missing advisory configuration: {path}"]
    except tomllib.TOMLDecodeError as error:
        return [f"invalid TOML in {path}: {error}"]

    entries = config.get("IgnoredVulns", [])
    if not isinstance(entries, list):
        return ["IgnoredVulns must be an array of tables"]

    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            errors.append(f"IgnoredVulns entry {index} must be a table")
            continue

        advisory_id = entry.get("id")
        if not isinstance(advisory_id, str) or not _ADVISORY_ID.fullmatch(advisory_id):
            errors.append(f"IgnoredVulns entry {index} has an invalid id")

        expiry = entry.get("ignoreUntil")
        if not isinstance(expiry, str):
            errors.append(f"IgnoredVulns entry {index} needs ignoreUntil=YYYY-MM-DD")
        else:
            try:
                expiry_date = date.fromisoformat(expiry)
            except ValueError:
                errors.append(f"IgnoredVulns entry {index} has an invalid ignoreUntil date")
            else:
                if expiry_date < today:
                    errors.append(f"IgnoredVulns entry {index} expired on {expiry}")

        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"IgnoredVulns entry {index} needs a non-empty reason")
            continue
        for marker in _REQUIRED_REASON_MARKERS:
            if marker not in reason:
                errors.append(f"IgnoredVulns entry {index} reason must include {marker}")
        if "false positive" not in reason.lower():
            errors.append(f"IgnoredVulns entry {index} reason must explain the false positive")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("osv-scanner.toml"),
        help="OSV-Scanner TOML configuration (default: osv-scanner.toml)",
    )
    args = parser.parse_args()
    errors = validation_errors(args.path)
    if errors:
        for error in errors:
            print(f"security policy error: {error}", file=sys.stderr)
        return 1
    print(f"advisory exception policy valid: {args.path}")
    return 0

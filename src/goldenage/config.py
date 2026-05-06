"""Application configuration."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

_GENERATED_AUTH_SECRET = secrets.token_urlsafe(32)


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration."""

    database_url: str | None
    local_first_mode: str | None
    sqlite_path: Path | None
    artifact_dir: Path
    profile_dir: Path
    local_timezone: str
    mail_client_mode: str | None
    mail_fixture_path: Path | None
    outlook_scan_per_folder_limit: int
    outlook_sync_enabled: bool
    outlook_account_name: str | None
    outlook_poll_seconds: int
    apple_mail_client_mode: str | None
    apple_mail_fixture_path: Path | None
    auth_secret: str
    auth_cookie_secure: bool

    @property
    def use_local_first_sqlite(self) -> bool:
        """Return whether SQLite-backed local-first mode is active."""
        return (self.local_first_mode or "").lower() in {"sqlite", "sqlite3"}


def load_settings() -> Settings:
    """Load settings from environment."""
    if os.environ.get("GOLDENAGE_DISABLE_DOTENV") != "1":
        _load_dotenv(Path.cwd() / ".env")
    artifact_dir = Path(os.environ.get("GOLDENAGE_ARTIFACT_DIR", "var/artifacts"))
    local_first_mode = os.environ.get("GOLDENAGE_LOCAL_FIRST_MODE") or os.environ.get(
        "GOLDENAGE_LOCAL_FIRST_DB"
    )
    raw_sqlite_path = os.environ.get("GOLDENAGE_SQLITE_PATH")
    raw_mail_fixture_path = os.environ.get("GOLDENAGE_MAIL_FIXTURE_PATH") or os.environ.get(
        "GOLDENAGE_APPLE_MAIL_FIXTURE_PATH"
    )
    return Settings(
        database_url=os.environ.get("DATABASE_URL"),
        local_first_mode=local_first_mode,
        sqlite_path=Path(raw_sqlite_path) if raw_sqlite_path else None,
        artifact_dir=artifact_dir,
        profile_dir=artifact_dir / "profiles",
        local_timezone=os.environ.get("GOLDENAGE_LOCAL_TIMEZONE", "Europe/Berlin"),
        mail_client_mode=os.environ.get("GOLDENAGE_MAIL_CLIENT_MODE")
        or os.environ.get("GOLDENAGE_APPLE_MAIL_CLIENT_MODE"),
        mail_fixture_path=Path(raw_mail_fixture_path) if raw_mail_fixture_path else None,
        outlook_scan_per_folder_limit=_env_int("GOLDENAGE_OUTLOOK_SCAN_PER_FOLDER_LIMIT", 250),
        outlook_sync_enabled=_env_flag("GOLDENAGE_OUTLOOK_SYNC_ENABLED"),
        outlook_account_name=os.environ.get("GOLDENAGE_OUTLOOK_ACCOUNT"),
        outlook_poll_seconds=_env_int("GOLDENAGE_OUTLOOK_POLL_SECONDS", 30),
        apple_mail_client_mode=os.environ.get("GOLDENAGE_APPLE_MAIL_CLIENT_MODE"),
        apple_mail_fixture_path=(
            Path(raw_path)
            if (raw_path := os.environ.get("GOLDENAGE_APPLE_MAIL_FIXTURE_PATH"))
            else None
        ),
        auth_secret=os.environ.get("GOLDENAGE_AUTH_SECRET", _GENERATED_AUTH_SECRET),
        auth_cookie_secure=_env_flag("GOLDENAGE_AUTH_COOKIE_SECURE"),
    )


def _env_flag(name: str) -> bool:
    """Return whether an environment flag is enabled."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Return a positive integer environment value."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(value, 1)


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs from a local .env file if present."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)

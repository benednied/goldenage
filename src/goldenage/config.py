"""Application configuration."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

_DEVELOPMENT_AUTH_SECRET = secrets.token_urlsafe(32)
_DEFAULT_AUTH_SESSION_MAX_AGE = 8 * 60 * 60
_VALID_ENVIRONMENTS = {"development", "production"}


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
    environment: str = "development"
    auth_cookie_domain: str | None = None
    auth_cookie_path: str = "/"
    auth_cookie_samesite: str = "lax"
    auth_session_max_age: int = _DEFAULT_AUTH_SESSION_MAX_AGE

    @property
    def use_local_first_sqlite(self) -> bool:
        """Return whether SQLite-backed local-first mode is active."""
        return (self.local_first_mode or "").lower() in {"sqlite", "sqlite3"}


def load_settings() -> Settings:
    """Load settings from environment."""
    if os.environ.get("GOLDENAGE_DISABLE_DOTENV") != "1":
        _load_dotenv(Path.cwd() / ".env")
    environment = _environment()
    auth_secret = _auth_secret(environment)
    auth_cookie_samesite = os.environ.get("GOLDENAGE_AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if auth_cookie_samesite not in {"lax", "strict", "none"}:
        raise RuntimeError("GOLDENAGE_AUTH_COOKIE_SAMESITE must be one of: lax, strict, none.")
    auth_cookie_path = os.environ.get("GOLDENAGE_AUTH_COOKIE_PATH", "/").strip() or "/"
    if not auth_cookie_path.startswith("/"):
        raise RuntimeError("GOLDENAGE_AUTH_COOKIE_PATH must start with '/'.")
    auth_cookie_domain = os.environ.get("GOLDENAGE_AUTH_COOKIE_DOMAIN") or None
    if auth_cookie_domain is not None:
        auth_cookie_domain = auth_cookie_domain.strip() or None
        if auth_cookie_domain and any(character.isspace() for character in auth_cookie_domain):
            raise RuntimeError("GOLDENAGE_AUTH_COOKIE_DOMAIN must not contain whitespace.")
    auth_cookie_secure = environment == "production" or _env_flag("GOLDENAGE_AUTH_COOKIE_SECURE")
    if auth_cookie_samesite == "none" and not auth_cookie_secure:
        raise RuntimeError(
            "GOLDENAGE_AUTH_COOKIE_SAMESITE=none requires secure authentication cookies."
        )
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
        auth_secret=auth_secret,
        auth_cookie_secure=auth_cookie_secure,
        environment=environment,
        auth_cookie_domain=auth_cookie_domain,
        auth_cookie_path=auth_cookie_path,
        auth_cookie_samesite=auth_cookie_samesite,
        auth_session_max_age=_env_int(
            "GOLDENAGE_AUTH_SESSION_MAX_AGE",
            _DEFAULT_AUTH_SESSION_MAX_AGE,
        ),
    )


def _environment() -> str:
    """Return the explicitly selected runtime environment."""
    value = (
        (
            os.environ.get("GOLDENAGE_ENVIRONMENT")
            or os.environ.get("GOLDENAGE_ENV")
            or "development"
        )
        .strip()
        .lower()
    )
    if value not in _VALID_ENVIRONMENTS:
        choices = ", ".join(sorted(_VALID_ENVIRONMENTS))
        raise RuntimeError(f"GOLDENAGE_ENVIRONMENT must be one of: {choices}.")
    return value


def _auth_secret(environment: str) -> str:
    """Return the configured auth secret, with a development-only fallback."""
    configured = os.environ.get("GOLDENAGE_AUTH_SECRET")
    if configured is None or not configured.strip():
        if environment == "production":
            raise RuntimeError(
                "GOLDENAGE_AUTH_SECRET must be set to a persistent random value in production."
            )
        return _DEVELOPMENT_AUTH_SECRET

    secret = configured.strip()
    if environment == "production" and not _is_suitable_production_secret(secret):
        raise RuntimeError(
            "GOLDENAGE_AUTH_SECRET must contain at least 32 characters in production."
        )
    return secret


def _is_suitable_production_secret(value: str) -> bool:
    """Return whether a production secret is sufficiently difficult to guess."""
    lowered = value.lower()
    placeholders = {
        "change-me",
        "changeme",
        "generate-a-random-secret-at-least-32-characters-long",
        "replace-with-a-long-random-local-secret",
        "replace-with-a-long-random-production-secret",
    }
    if len(value) < 32 or lowered in placeholders:
        return False
    # A repeated or otherwise trivially structured value is not a useful
    # signing key even when it happens to meet the length requirement.
    return len(set(value)) >= 10


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

"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration."""

    database_url: str | None
    local_first_mode: str | None
    sqlite_path: Path | None
    artifact_dir: Path
    profile_dir: Path
    local_timezone: str

    @property
    def use_local_first_sqlite(self) -> bool:
        """Return whether SQLite-backed local-first mode is active."""
        return (self.local_first_mode or "").lower() in {"sqlite", "sqlite3"}


def load_settings() -> Settings:
    """Load settings from environment."""
    if os.environ.get("GOLDENAGE_DISABLE_DOTENV") != "1":
        _load_dotenv(Path.cwd() / ".env")
    artifact_dir = Path(os.environ.get("GOLDENAGE_ARTIFACT_DIR", "var/artifacts"))
    local_first_mode = (
        os.environ.get("GOLDENAGE_LOCAL_FIRST_MODE")
        or os.environ.get("GOLDENAGE_LOCAL_FIRST_DB")
    )
    raw_sqlite_path = os.environ.get("GOLDENAGE_SQLITE_PATH")
    return Settings(
        database_url=os.environ.get("DATABASE_URL"),
        local_first_mode=local_first_mode,
        sqlite_path=Path(raw_sqlite_path) if raw_sqlite_path else None,
        artifact_dir=artifact_dir,
        profile_dir=artifact_dir / "profiles",
        local_timezone=os.environ.get("GOLDENAGE_LOCAL_TIMEZONE", "Europe/Berlin"),
    )


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

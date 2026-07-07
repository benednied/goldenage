from pathlib import Path

from goldenage import config


def test_load_settings_reads_environment_aliases_and_typed_values(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_DB", "sqlite")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "goldenage.sqlite3"))
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GOLDENAGE_LOCAL_TIMEZONE", "UTC")
    monkeypatch.setenv("GOLDENAGE_APPLE_MAIL_CLIENT_MODE", "fixture")
    monkeypatch.setenv("GOLDENAGE_APPLE_MAIL_FIXTURE_PATH", str(tmp_path / "mail.json"))
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_SCAN_PER_FOLDER_LIMIT", "0")
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_SYNC_ENABLED", "yes")
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_ACCOUNT", "Mailbox")
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_POLL_SECONDS", "invalid")
    monkeypatch.setenv("GOLDENAGE_GISELA_HTTP_URL", " https://gisela.example.test/ ")
    monkeypatch.setenv("GOLDENAGE_ELIZABETHAN_HTTP_URL", "https://elizabethan.example.test")
    monkeypatch.setenv("GOLDENAGE_AGENT_HTTP_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("GOLDENAGE_AUTH_SECRET", "secret")
    monkeypatch.setenv("GOLDENAGE_AUTH_COOKIE_SECURE", "on")

    settings = config.load_settings()

    assert settings.database_url == "postgresql://example"
    assert settings.use_local_first_sqlite is True
    assert settings.sqlite_path == tmp_path / "goldenage.sqlite3"
    assert settings.artifact_dir == tmp_path / "artifacts"
    assert settings.profile_dir == tmp_path / "artifacts" / "profiles"
    assert settings.local_timezone == "UTC"
    assert settings.mail_client_mode == "fixture"
    assert settings.mail_fixture_path == tmp_path / "mail.json"
    assert settings.outlook_scan_per_folder_limit == 1
    assert settings.outlook_sync_enabled is True
    assert settings.outlook_account_name == "Mailbox"
    assert settings.outlook_poll_seconds == 30
    assert settings.apple_mail_client_mode == "fixture"
    assert settings.apple_mail_fixture_path == tmp_path / "mail.json"
    assert settings.gisela_http_url == "https://gisela.example.test"
    assert settings.elizabethan_http_url == "https://elizabethan.example.test"
    assert settings.agent_http_timeout_seconds == 2.5
    assert settings.auth_secret == "secret"
    assert settings.auth_cookie_secure is True


def test_load_settings_loads_dotenv_without_overriding_existing_environment(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOLDENAGE_DISABLE_DOTENV", raising=False)
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", "from-env")
    Path(".env").write_text(
        "\n".join(
            [
                "# ignored",
                "GOLDENAGE_ARTIFACT_DIR=from-dotenv",
                "GOLDENAGE_LOCAL_FIRST_MODE='sqlite3'",
                'GOLDENAGE_MAIL_FIXTURE_PATH="mail.json"',
                "MALFORMED",
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = config.load_settings()

    assert settings.artifact_dir == Path("from-env")
    assert settings.local_first_mode == "sqlite3"
    assert settings.mail_fixture_path == Path("mail.json")


def test_env_helpers_handle_defaults_invalid_values_and_flags(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_INT", raising=False)
    monkeypatch.setenv("INVALID_INT", "abc")
    monkeypatch.setenv("NEGATIVE_INT", "-4")
    monkeypatch.setenv("TRUE_FLAG", " true ")
    monkeypatch.setenv("FALSE_FLAG", "no")

    assert config._env_int("MISSING_INT", 7) == 7
    assert config._env_int("INVALID_INT", 7) == 7
    assert config._env_int("NEGATIVE_INT", 7) == 1
    assert config._env_float("MISSING_FLOAT", 7.5) == 7.5
    assert config._env_flag("TRUE_FLAG") is True
    assert config._env_flag("FALSE_FLAG") is False
    config._load_dotenv(Path("missing.env"))

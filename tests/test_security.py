from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from fastapi.responses import RedirectResponse
from starlette.requests import Request

from goldenage.config import Settings
from goldenage.web import app as web_app


def _settings(tmp_path: Path, *, max_age: int = 300, secure: bool = True) -> Settings:
    artifact_dir = tmp_path / "artifacts"
    return Settings(
        database_url=None,
        local_first_mode="sqlite3",
        sqlite_path=tmp_path / "goldenage.sqlite3",
        artifact_dir=artifact_dir,
        profile_dir=artifact_dir / "profiles",
        local_timezone="UTC",
        mail_client_mode=None,
        mail_fixture_path=None,
        outlook_scan_per_folder_limit=25,
        outlook_sync_enabled=False,
        outlook_account_name=None,
        outlook_poll_seconds=30,
        apple_mail_client_mode=None,
        apple_mail_fixture_path=None,
        auth_secret="shared-test-secret",
        auth_cookie_secure=secure,
        auth_cookie_domain="example.test",
        auth_cookie_path="/app",
        auth_cookie_samesite="strict",
        auth_session_max_age=max_age,
    )


def test_auth_cookie_has_expiry_and_consistent_security_attributes(tmp_path) -> None:
    settings = _settings(tmp_path)
    response = RedirectResponse(url="/")
    account_id = UUID("11111111-1111-1111-1111-111111111111")

    web_app._set_auth_cookie(
        response,
        account_id,
        password_hash="password-hash",
        settings=settings,
    )

    header = response.headers["set-cookie"].lower()
    assert "max-age=300" in header
    assert "domain=example.test" in header
    assert "path=/app" in header
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=strict" in header


def test_auth_sessions_expire_and_bind_to_the_password_hash(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, max_age=10)
    account_id = UUID("11111111-1111-1111-1111-111111111111")
    cookie = web_app._signed_account_id(
        account_id,
        password_hash="old-password-hash",
        settings=settings,
        now=100,
    )
    request = cast(Request, SimpleNamespace(cookies={web_app.AUTH_COOKIE_NAME: cookie}))

    monkeypatch.setattr(web_app.time_module, "time", lambda: 109)
    assert (
        web_app._authenticated_account_id(
            request,
            settings=settings,
            password_hash="old-password-hash",
        )
        == account_id
    )
    assert (
        web_app._authenticated_account_id(
            request,
            settings=settings,
            password_hash="new-password-hash",
        )
        is None
    )

    monkeypatch.setattr(web_app.time_module, "time", lambda: 110)
    assert web_app._authenticated_account_id(request, settings=settings) is None


def test_csrf_token_is_bound_to_the_authentication_cookie(tmp_path) -> None:
    settings = _settings(tmp_path)
    account_id = UUID("11111111-1111-1111-1111-111111111111")
    auth_cookie = web_app._signed_account_id(account_id, settings=settings)
    request = cast(Request, SimpleNamespace(cookies={web_app.AUTH_COOKIE_NAME: auth_cookie}))
    token = web_app._new_csrf_token(settings=settings, auth_cookie_value=auth_cookie)

    assert web_app._valid_csrf_token(request, token, settings=settings)
    assert not web_app._valid_csrf_token(
        cast(Request, SimpleNamespace(cookies={web_app.AUTH_COOKIE_NAME: "other-session"})),
        token,
        settings=settings,
    )

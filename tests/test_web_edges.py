import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from goldenage.adapters.agent_http import HttpElizabethanSearchClient, HttpGiselaClient
from goldenage.adapters.demo import HeuristicElizabethanSearchClient, HeuristicGiselaClient
from goldenage.config import Settings
from goldenage.domain.models import CaseFile, LocalUserAccount, UserContext
from goldenage.domain.rules import ResolutionError
from goldenage.web import app as web_app


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    monkeypatch.delenv("GOLDENAGE_MAIL_FIXTURE_PATH", raising=False)
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_SYNC_ENABLED", "0")


def test_root_login_onboarding_and_ldap_redirect_branches(monkeypatch, tmp_path) -> None:
    demo_client = TestClient(web_app.create_app(), follow_redirects=False)
    assert demo_client.get("/").headers["location"] == "/worklist"
    assert demo_client.get("/login").headers["location"] == "/worklist"
    assert demo_client.get("/onboarding").headers["location"] == "/worklist"
    assert demo_client.post("/login/local").headers["location"] == "/worklist"
    assert demo_client.post("/onboarding").headers["location"] == "/worklist"
    ldap_response = demo_client.post("/login/ldap")
    assert ldap_response.status_code == 400
    assert "LDAP sign-in is not configured yet." in ldap_response.text

    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "local.sqlite3"))
    local_client = TestClient(web_app.create_app(), follow_redirects=False)
    assert local_client.get("/").headers["location"] == "/onboarding"
    assert local_client.get("/login").headers["location"] == "/onboarding"

    bad_onboarding = local_client.post(
        "/onboarding",
        data={"display_name": "", "email": "", "password": ""},
    )
    assert bad_onboarding.status_code == 400
    assert "Display name, email, and password are required." in bad_onboarding.text
    bad_picture = local_client.post(
        "/onboarding",
        data={
            "display_name": "Fixture User",
            "email": "user.fixture@example.test",
            "password": "secret-passphrase",
        },
        files={"profile_picture": ("profile.txt", b"text", "text/plain")},
    )
    assert bad_picture.status_code == 400
    assert "Profile picture uploads must be image files." in bad_picture.text


def test_static_ux_contracts_keep_focus_order_and_responsive_layout() -> None:
    template = Path("src/goldenage/web/templates/partials/intake_panel.html").read_text(
        encoding="utf-8"
    )
    style = Path("src/goldenage/web/static/style.css").read_text(encoding="utf-8")
    intake_script = Path("src/goldenage/web/static/intake.js").read_text(encoding="utf-8")
    login_template = Path("src/goldenage/web/templates/login.html").read_text(encoding="utf-8")

    assert template.index("intake-active") < template.index("intake-dropzone")
    assert "panel-intake > .intake-active" not in style
    assert "requestSubmit" not in intake_script
    assert "Upload mail" in template
    assert ".busy-indicator" in style
    assert ".settings-layout {\n    grid-template-columns: 1fr;" in style
    assert 'role="button"' not in login_template
    assert "login-sound-toggle" in login_template


def test_startup_and_shutdown_delegate_to_outlook_worker(monkeypatch) -> None:
    calls: list[str] = []

    class FakeSource:
        def __init__(self, settings, on_message) -> None:
            del settings, on_message

    class FakeWorker:
        def __init__(self, source) -> None:
            del source

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr(web_app, "WindowsOutlookMailboxSource", FakeSource)
    monkeypatch.setattr(web_app, "OutlookMailboxWorker", FakeWorker)
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_SYNC_ENABLED", "1")
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_ACCOUNT", "Mailbox")

    with TestClient(web_app.create_app()):
        assert calls == ["start"]

    assert calls == ["start", "stop"]


def test_web_error_routes_for_auth_mail_upload_resolution_and_not_found(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "local.sqlite3"))
    mail_fixture = tmp_path / "mail-fixture.json"
    mail_fixture.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GOLDENAGE_MAIL_FIXTURE_PATH", str(mail_fixture))
    client = TestClient(web_app.create_app(), follow_redirects=False)

    assert client.get("/settings").headers["location"] == "/onboarding"
    client.post(
        "/onboarding",
        data={
            "display_name": "Fixture User",
            "email": "user.fixture@example.test",
            "password": "secret-passphrase",
        },
    )
    unauthenticated = TestClient(web_app.create_app(), follow_redirects=False)
    assert unauthenticated.get("/settings").headers["location"] == "/login"

    settings_bad_date = client.post(
        "/settings/mail",
        data={"sent_after": "not-a-date"},
    )
    assert settings_bad_date.status_code == 200
    assert "Invalid isoformat string" in settings_bad_date.text

    search_bad_date = client.post(
        "/mail/desktop-mail/search",
        data={"sent_after": "not-a-date"},
    )
    assert search_bad_date.status_code == 200
    assert "Invalid isoformat string" in search_bad_date.text

    empty_upload = client.post(
        "/artifacts/upload",
        files={"file": ("empty.msg", b"", "application/vnd.ms-outlook")},
    )
    assert empty_upload.status_code == 400
    assert "Select a file before uploading." in empty_upload.text

    assert client.get(f"/cases/{uuid4()}/artifacts/{uuid4()}/panel").status_code == 404
    assert (
        client.post(
            f"/activities/{uuid4()}/resolve",
            data={"skip_follow_up": "on"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/mail/desktop-mail/import",
            data={"candidate_id": "missing"},
        ).status_code
        == 404
    )


def test_protected_routes_redirect_when_local_user_is_not_authenticated(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "local.sqlite3"))
    setup_client = TestClient(web_app.create_app(), follow_redirects=False)
    setup_client.post(
        "/onboarding",
        data={
            "display_name": "Fixture User",
            "email": "user.fixture@example.test",
            "password": "secret-passphrase",
        },
    )
    client = TestClient(web_app.create_app(), follow_redirects=False)
    case_id = uuid4()
    artifact_id = uuid4()
    activity_id = uuid4()
    conversation_id = uuid4()

    assert client.post("/settings/mail").headers["location"] == "/login"
    assert client.post("/mail/desktop-mail/search").headers["location"] == "/login"
    assert (
        client.post(
            "/mail/desktop-mail/import",
            data={"candidate_id": "missing"},
        ).headers["location"]
        == "/login"
    )
    assert client.post("/settings/password").headers["location"] == "/login"
    assert client.get(f"/cases/{case_id}/panel").headers["location"] == "/login"
    assert (
        client.get(f"/cases/{case_id}/artifacts/{artifact_id}/panel").headers["location"]
        == "/login"
    )
    assert client.post(f"/activities/{activity_id}/resolve").headers["location"] == "/login"
    assert (
        client.post(
            "/artifacts/upload",
            files={"file": ("mail.msg", b"body", "application/vnd.ms-outlook")},
        ).headers["location"]
        == "/login"
    )
    assert (
        client.post(f"/artifacts/{artifact_id}/suggestion/reject").headers["location"] == "/login"
    )
    assert client.get(f"/intake/conversations/{conversation_id}").headers["location"] == "/login"
    assert (
        client.post(
            "/cases/search",
            data={"artifact_id": str(artifact_id), "query": "Acme"},
        ).headers["location"]
        == "/login"
    )
    assert (
        client.post(
            f"/artifacts/{artifact_id}/assign",
            data={"case_id": str(case_id), "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
        ).headers["location"]
        == "/login"
    )
    assert (
        client.post(
            f"/artifacts/{artifact_id}/create-case",
            data={"title": "Case", "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
        ).headers["location"]
        == "/login"
    )


def test_local_first_authenticated_redirects_and_missing_repository_errors(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "local.sqlite3"))
    client = TestClient(web_app.create_app(), follow_redirects=False)
    client.post(
        "/onboarding",
        data={
            "display_name": "Fixture User",
            "email": "user.fixture@example.test",
            "password": "secret-passphrase",
        },
    )
    assert client.get("/login").headers["location"] == "/worklist"
    assert client.get("/onboarding").headers["location"] == "/worklist"
    assert (
        client.post(
            "/onboarding",
            data={
                "display_name": "Other",
                "email": "other@example.com",
                "password": "secret-passphrase",
            },
        ).headers["location"]
        == "/worklist"
    )

    class DummyService:
        pass

    context = web_app.AppContext(
        settings=_settings(tmp_path),
        service=DummyService(),  # ty:ignore[invalid-argument-type]
        default_user=None,
        local_user_repository=None,
    )
    monkeypatch.setattr(web_app, "_build_context", lambda settings: context)
    broken_client = TestClient(web_app.create_app(), follow_redirects=False)
    assert broken_client.post("/login/local").status_code == 500
    assert (
        broken_client.post(
            "/onboarding",
            data={
                "display_name": "Fixture User",
                "email": "user.fixture@example.test",
                "password": "secret-passphrase",
            },
        ).status_code
        == 500
    )


def test_password_change_error_messages(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "local.sqlite3"))
    client = TestClient(web_app.create_app())
    client.post(
        "/onboarding",
        data={
            "display_name": "Fixture User",
            "email": "user.fixture@example.test",
            "password": "secret-passphrase",
        },
    )

    wrong_current = client.post(
        "/settings/password",
        data={
            "current_password": "wrong",
            "new_password": "new-passphrase",
            "confirm_password": "new-passphrase",
        },
    )
    short_password = client.post(
        "/settings/password",
        data={
            "current_password": "secret-passphrase",
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    mismatch = client.post(
        "/settings/password",
        data={
            "current_password": "secret-passphrase",
            "new_password": "new-passphrase",
            "confirm_password": "different",
        },
    )

    assert "Current password is incorrect." in wrong_current.text
    assert "New password must be at least 8 characters." in short_password.text
    assert "New passwords do not match." in mismatch.text


def test_route_success_and_resolution_error_panels(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(tmp_path / "local.sqlite3"))
    client = TestClient(web_app.create_app(), follow_redirects=False)
    client.post(
        "/onboarding",
        data={
            "display_name": "Fixture User",
            "email": "user.fixture@example.test",
            "password": "secret-passphrase",
        },
    )
    context = client.app.state.context  # ty:ignore[unresolved-attribute]
    case_id = uuid4()
    artifact_id = uuid4()
    activity_id = uuid4()
    user_id = uuid4()
    detail = SimpleNamespace(
        case_file=CaseFile(
            id=case_id,
            title="Acme",
            company=None,
            primary_contact=None,
            status="open",
            last_activity_at=datetime(2026, 4, 12, tzinfo=UTC),
        ),
        active_activity=None,
        open_activities=(),
        recent_artifacts=(),
        selected_artifact=None,
        selected_mail_metadata=None,
        selected_mail_message=None,
        selected_conversation=None,
        selected_conversation_artifacts=(),
    )
    context.service.get_case_detail = lambda **kwargs: detail
    context.service.get_case_detail_for_activity = lambda **kwargs: detail
    context.service.get_today_worklist = lambda **kwargs: ()
    context.service.get_mail_import_state = lambda **kwargs: web_app.IntakeState()
    context.service.get_recent_intake = lambda **kwargs: web_app.IntakeState()
    context.service.resolve_activity = lambda **kwargs: detail
    context.service.assign_artifact_to_case = lambda **kwargs: detail
    context.service.create_case_for_artifact = lambda **kwargs: detail
    context.service.import_mail_candidate = lambda **kwargs: (_ for _ in ()).throw(
        ResolutionError("bad import")
    )
    context.service.get_intake_state = lambda **kwargs: web_app.IntakeState(
        artifact=SimpleNamespace(id=artifact_id),  # ty:ignore[invalid-argument-type]
        search_mode=True,
    )

    assert client.get(f"/cases/{case_id}/panel").status_code == 200
    assert client.get(f"/cases/{case_id}/artifacts/{artifact_id}/panel").status_code == 200
    assert (
        client.post(
            f"/activities/{activity_id}/resolve",
            data={"skip_follow_up": "on"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/artifacts/{artifact_id}/assign",
            data={
                "case_id": str(case_id),
                "next_step": "Call",
                "next_due_at": "2026-04-12T09:30",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/artifacts/{artifact_id}/create-case",
            data={"title": "Case", "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/mail/desktop-mail/import",
            data={"candidate_id": "candidate"},
        ).status_code
        == 200
    )

    context.service.resolve_activity = lambda **kwargs: (_ for _ in ()).throw(
        ResolutionError("next step required")
    )
    assert (
        client.post(
            f"/activities/{activity_id}/resolve",
            data={"skip_follow_up": "off"},
        ).status_code
        == 400
    )
    context.service.assign_artifact_to_case = lambda **kwargs: (_ for _ in ()).throw(
        ResolutionError("date required")
    )
    assign_error = client.post(
        f"/artifacts/{artifact_id}/assign",
        data={"case_id": str(case_id), "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
    )
    assert assign_error.status_code == 400
    assert assign_error.headers["HX-Retarget"] == "#intake-panel"
    context.service.create_case_for_artifact = lambda **kwargs: (_ for _ in ()).throw(
        ResolutionError("title required")
    )
    create_error = client.post(
        f"/artifacts/{artifact_id}/create-case",
        data={"title": "Case", "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
    )
    assert create_error.status_code == 400
    context.service.assign_artifact_to_case = lambda **kwargs: (_ for _ in ()).throw(
        web_app.NotFoundError("Artifact not found.")
    )
    assert (
        client.post(
            f"/artifacts/{artifact_id}/assign",
            data={"case_id": str(case_id), "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
        ).status_code
        == 404
    )
    context.service.create_case_for_artifact = lambda **kwargs: (_ for _ in ()).throw(
        web_app.NotFoundError("Artifact not found.")
    )
    assert (
        client.post(
            f"/artifacts/{artifact_id}/create-case",
            data={"title": "Case", "next_step": "Call", "next_due_at": "2026-04-12T09:30"},
        ).status_code
        == 404
    )

    account = LocalUserAccount(
        id=user_id,
        email="missing@example.com",
        display_name="Missing",
        password_hash=web_app._hash_password("secret-passphrase"),
        profile_image_path=None,
    )
    context.local_user_repository.get_user_by_email = lambda email: None
    assert (
        "Local user account not found."
        in client.post(
            "/settings/password",
            data={
                "current_password": "secret-passphrase",
                "new_password": "new-passphrase",
                "confirm_password": "new-passphrase",
            },
        ).text
    )
    context.local_user_repository.get_user_by_email = lambda email: account


def test_private_web_helpers_cover_auth_datetime_profile_and_context_edges(tmp_path) -> None:
    settings = _settings(tmp_path)
    account_id = UUID("11111111-1111-1111-1111-111111111111")
    signed = web_app._signed_account_id(account_id, settings=settings)
    request = SimpleNamespace(cookies={web_app.AUTH_COOKIE_NAME: signed})
    assert web_app._authenticated_account_id(request, settings=settings) == account_id  # ty:ignore[invalid-argument-type]
    assert (
        web_app._authenticated_account_id(
            SimpleNamespace(cookies={web_app.AUTH_COOKIE_NAME: "bad"}),  # ty:ignore[invalid-argument-type]
            settings=settings,
        )
        is None
    )
    assert (
        web_app._authenticated_account_id(
            SimpleNamespace(cookies={web_app.AUTH_COOKIE_NAME: f"{account_id}.bad"}),  # ty:ignore[invalid-argument-type]
            settings=settings,
        )
        is None
    )
    assert (
        web_app._authenticated_account_id(
            SimpleNamespace(
                cookies={
                    web_app.AUTH_COOKIE_NAME: f"not-a-uuid.{web_app._auth_signature('not-a-uuid', settings=settings)}"
                }
            ),  # ty:ignore[invalid-argument-type]
            settings=settings,
        )
        is None
    )

    assert web_app._verify_password("pw", "bad-hash") is False
    assert web_app._verify_password("pw", "other$1$salt$digest") is False
    password_hash = web_app._hash_password("pw")
    assert web_app._verify_password("pw", password_hash) is True
    assert web_app._verify_password("wrong", password_hash) is False

    assert (
        web_app._mailbox_file_name(SimpleNamespace(subject="RE: Bad / Name?"))  # ty:ignore[invalid-argument-type]
        == "RE_ Bad _ Name_.txt"
    )
    assert web_app._mailbox_file_name(SimpleNamespace(subject="   ")) == "outlook-message.txt"  # ty:ignore[invalid-argument-type]
    assert (
        web_app._profile_image_url(UserContext(id=account_id, email="a", display_name="A")) is None
    )
    assert (
        web_app._profile_image_url(
            UserContext(id=account_id, email="a", display_name="A", profile_image_path="p.png")
        )
        == "/profiles/p.png"
    )
    context = web_app.AppContext(
        settings=settings,
        service=SimpleNamespace(get_recent_intake=lambda **kwargs: web_app.IntakeState()),  # ty:ignore[invalid-argument-type]
        default_user=UserContext(id=account_id, email="a", display_name="A"),
        local_user_repository=None,
    )
    assert web_app._current_user(context, request=None) == context.default_user
    local_account = SimpleNamespace(to_user_context=lambda: context.default_user)
    assert (
        web_app._current_user(
            replace(
                context,
                local_user_repository=SimpleNamespace(get_first_user=lambda: local_account),
            ),
            request=None,
        )
        == context.default_user
    )
    assert (
        web_app._current_user_for_repositories(
            default_user=None,
            local_user_repository=SimpleNamespace(get_first_user=lambda: None),  # ty:ignore[invalid-argument-type]
        )
        is None
    )
    account = SimpleNamespace(to_user_context=lambda: context.default_user)
    assert (
        web_app._current_user_for_repositories(
            default_user=None,
            local_user_repository=SimpleNamespace(get_first_user=lambda: account),  # ty:ignore[invalid-argument-type]
        )
        == context.default_user
    )
    with pytest.raises(HTTPException, match="503"):
        web_app._require_current_user(
            replace(context, default_user=None),
            request=None,
        )
    assert (
        web_app._redirect_to_login_or_onboarding_if_needed(
            SimpleNamespace(cookies={}),  # ty:ignore[invalid-argument-type]
            replace(context, local_user_repository=None, settings=_settings(tmp_path)),
        ).headers["location"]  # ty:ignore[unresolved-attribute]
        == "/onboarding"
    )
    assert (
        web_app._load_case_detail(
            case_id=str(uuid4()),
            context=SimpleNamespace(
                service=SimpleNamespace(
                    get_case_detail=lambda **kwargs: (_ for _ in ()).throw(
                        web_app.NotFoundError("missing")
                    )
                )
            ),  # ty:ignore[invalid-argument-type]
            now=datetime(2026, 4, 12, tzinfo=UTC),
            user=context.default_user,  # ty:ignore[invalid-argument-type]
        )
        is None
    )
    assert web_app._format_datetime(None, "UTC") == "n/a"
    assert web_app._format_form_datetime(None, "UTC") == ""
    assert web_app._parse_form_datetime("", "UTC") is None
    assert web_app._parse_form_datetime("2026-04-12T09:30", "Europe/Berlin") == datetime(
        2026, 4, 12, 7, 30, tzinfo=UTC
    )
    assert web_app._require_form_datetime("2026-04-12T09:30+00:00", "UTC") == datetime(
        2026, 4, 12, 9, 30, tzinfo=UTC
    )
    with pytest.raises(ResolutionError, match="due date"):
        web_app._require_form_datetime("", "UTC")

    response = RedirectResponse(url="/")
    secure_settings = _settings(tmp_path, auth_cookie_secure=True)
    web_app._set_auth_cookie(response, account_id, settings=secure_settings)
    assert "secure" in response.headers["set-cookie"].lower()


def test_store_profile_picture_edges(tmp_path) -> None:
    settings = _settings(tmp_path)

    assert (
        asyncio.run(web_app._store_profile_picture(profile_picture=None, settings=settings)) is None
    )
    empty = UploadFile(filename="profile.png", file=SimpleNamespace(read=lambda size=-1: b""))  # ty:ignore[invalid-argument-type]
    empty.headers = {"content-type": "image/png"}  # ty:ignore[invalid-assignment]
    assert (
        asyncio.run(web_app._store_profile_picture(profile_picture=empty, settings=settings))
        is None
    )


def test_build_context_postgres_and_required_sqlite_path(monkeypatch, tmp_path) -> None:
    class FakeRepository:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

    monkeypatch.setattr(web_app, "PostgresCaseRepository", FakeRepository)
    monkeypatch.setattr(web_app, "PostgresActivityRepository", FakeRepository)
    monkeypatch.setattr(web_app, "PostgresArtifactRepository", FakeRepository)
    monkeypatch.setattr(web_app, "PostgresAuditRepository", FakeRepository)
    monkeypatch.setattr(web_app, "build_desktop_mail_import_client", lambda **kwargs: None)
    context = web_app._build_context(
        _settings(tmp_path, database_url="postgresql://example", local_first_mode=None)
    )
    assert context.default_user is not None
    assert context.local_user_repository is None

    missing_sqlite_settings = replace(_settings(tmp_path), sqlite_path=None)
    with pytest.raises(RuntimeError, match="GOLDENAGE_SQLITE_PATH"):
        web_app._build_context(missing_sqlite_settings)


def test_agent_clients_switch_to_http_when_urls_are_configured(tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        gisela_http_url="https://gisela.example.test",
        elizabethan_http_url="https://elizabethan.example.test",
        agent_http_timeout_seconds=3.0,
    )

    assert isinstance(web_app._build_gisela_client(settings), HttpGiselaClient)
    assert isinstance(web_app._build_elizabethan_client(settings), HttpElizabethanSearchClient)

    default_settings = _settings(tmp_path)
    assert isinstance(web_app._build_gisela_client(default_settings), HeuristicGiselaClient)
    assert isinstance(
        web_app._build_elizabethan_client(default_settings),
        HeuristicElizabethanSearchClient,
    )


def test_outlook_ingest_callback_skips_when_no_repository_user(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeSource:
        def __init__(self, settings, on_message) -> None:
            del settings
            captured["on_message"] = on_message

    class FakeWorker:
        def __init__(self, source) -> None:
            del source

    monkeypatch.setattr(web_app, "WindowsOutlookMailboxSource", FakeSource)
    monkeypatch.setattr(web_app, "OutlookMailboxWorker", FakeWorker)
    monkeypatch.setattr(web_app, "build_desktop_mail_import_client", lambda **kwargs: None)
    context = web_app._build_context(
        replace(
            _settings(tmp_path),
            outlook_sync_enabled=True,
            outlook_account_name="Mailbox",
        )
    )

    context.service.ingest_mail = lambda **kwargs: (_ for _ in ()).throw(AssertionError("skipped"))  # ty:ignore[invalid-assignment]
    captured["on_message"](SimpleNamespace())  # ty:ignore[call-non-callable]


def _settings(
    tmp_path: Path,
    *,
    database_url: str | None = None,
    local_first_mode: str | None = "sqlite3",
    sqlite_path: Path | None = None,
    auth_cookie_secure: bool = False,
) -> Settings:
    if sqlite_path is None and local_first_mode:
        sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    return Settings(
        database_url=database_url,
        local_first_mode=local_first_mode,
        sqlite_path=sqlite_path,
        artifact_dir=artifact_dir,
        profile_dir=artifact_dir / "profiles",
        local_timezone="Europe/Berlin",
        mail_client_mode=None,
        mail_fixture_path=None,
        outlook_scan_per_folder_limit=25,
        outlook_sync_enabled=False,
        outlook_account_name=None,
        outlook_poll_seconds=30,
        apple_mail_client_mode=None,
        apple_mail_fixture_path=None,
        gisela_http_url=None,
        elizabethan_http_url=None,
        agent_http_timeout_seconds=10.0,
        auth_secret="secret",
        auth_cookie_secure=auth_cookie_secure,
    )

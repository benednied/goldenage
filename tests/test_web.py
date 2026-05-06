import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from goldenage.adapters.demo import OutlookMsgExtractor
from goldenage.adapters.outlook_mailbox import OutlookMailboxMessage
from goldenage.domain.models import ExtractedArtifactData, MailParticipant
from goldenage.web.app import create_app


@pytest.fixture(autouse=True)
def disable_outlook_sync_by_default(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_SYNC_ENABLED", "0")
    monkeypatch.delenv("GOLDENAGE_OUTLOOK_ACCOUNT", raising=False)


def test_worklist_page_renders(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    response = client.get("/worklist")

    assert response.status_code == 200
    assert "GoldenAge" in response.text
    assert "Due activities" in response.text
    assert 'rel="icon"' in response.text
    assert "golden_age_favicon_48.ico" in response.text


def test_favicon_route_serves_icon(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/x-icon")
    assert response.content


def test_windows_outlook_callback_ingests_mailbox_message(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeOutlookSource:
        def __init__(self, settings, on_message) -> None:
            del settings
            captured["on_message"] = on_message

        def watch_forever(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr("goldenage.web.app.WindowsOutlookMailboxSource", FakeOutlookSource)
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_SYNC_ENABLED", "1")
    monkeypatch.setenv("GOLDENAGE_OUTLOOK_ACCOUNT", "Mailbox - bened@example.com")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    app = create_app()

    captured["on_message"](
        OutlookMailboxMessage(
            account_name="Mailbox - bened@example.com",
            folder_key="Inbox",
            message_key="abc123",
            conversation_key="conv-42",
            internet_message_id="<abc123@example.com>",
            subject="RE: Acme contract renewal",
            sender_name="Max Mustermann",
            sender_email="max@acme.example",
            recipients=(MailParticipant(name="Alex Example", email="alex@example.com"),),
            body_text="Please review the renewal changes.",
            sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
            received_at=datetime(2026, 4, 12, 9, 31, tzinfo=UTC),
            direction="inbound",
        )
    )

    context = app.state.context
    user = context.default_user
    assert user is not None
    recent = context.service.get_recent_intake(user=user, limit=1)
    assert recent.recent_conversations[0].latest_subject == "RE: Acme contract renewal"


def test_upload_reject_and_search_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: _sample_extracted_data(
            subject="Acme contract renewal"
        ),
    )
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    upload_response = client.post(
        "/artifacts/upload",
        files={"file": ("acme.msg", b"fake msg bytes", "application/vnd.ms-outlook")},
    )
    assert upload_response.status_code == 200
    assert "Confirm suggestion" in upload_response.text
    assert "Recent conversations" in upload_response.text

    import re

    artifact_id = re.search(r"/artifacts/([0-9a-f-]+)/assign", upload_response.text).group(1)

    reject_response = client.post(f"/artifacts/{artifact_id}/suggestion/reject")
    assert reject_response.status_code == 200
    assert "Search cases" in reject_response.text

    search_response = client.post(
        "/cases/search",
        data={
            "artifact_id": artifact_id,
            "query": "Acme contract Mustermann",
        },
    )
    assert search_response.status_code == 200
    assert "Assign to this case" in search_response.text


def test_clicking_case_artifact_displays_msg_contents(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: _sample_extracted_data(
            subject="Acme contract renewal"
        ),
    )
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    upload_response = client.post(
        "/artifacts/upload",
        files={"file": ("acme.msg", b"fake msg bytes", "application/vnd.ms-outlook")},
    )
    assert upload_response.status_code == 200

    import re

    artifact_id = re.search(r"/artifacts/([0-9a-f-]+)/assign", upload_response.text).group(1)
    case_id = re.search(r'name="case_id" value="([0-9a-f-]+)"', upload_response.text).group(1)

    assign_response = client.post(
        f"/artifacts/{artifact_id}/assign",
        data={
            "case_id": case_id,
            "next_step": "Review the mail",
            "next_due_at": "2026-04-13T09:00",
        },
    )
    assert assign_response.status_code == 200
    assert "acme.msg" in assign_response.text

    detail_response = client.get(f"/cases/{case_id}/artifacts/{artifact_id}/panel")
    assert detail_response.status_code == 200
    assert "Message content" in detail_response.text
    assert "Conversation:" in detail_response.text
    assert "Acme contract renewal" in detail_response.text
    assert "Max Mustermann" in detail_response.text
    assert "body text" in detail_response.text


def test_recent_conversation_can_be_opened_from_intake_panel(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: _sample_extracted_data(
            subject="Acme contract renewal"
        ),
    )
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    upload_response = client.post(
        "/artifacts/upload",
        files={"file": ("acme.msg", b"fake msg bytes", "application/vnd.ms-outlook")},
    )
    assert upload_response.status_code == 200

    import re

    conversation_id = re.search(
        r"/intake/conversations/([0-9a-f-]+)",
        upload_response.text,
    ).group(1)

    response = client.get(f"/intake/conversations/{conversation_id}")
    assert response.status_code == 200
    assert "Thread with 1 mail" in response.text


def test_non_msg_upload_shows_not_supported_and_logs_to_console(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/artifacts/upload",
        files={"file": ("acme.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 400
    assert "not supported in this mvp for now" in response.text
    captured = capsys.readouterr()
    assert "not supported in this mvp for now" in captured.out


def test_local_first_sqlite_onboarding_creates_first_user(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(artifact_dir))
    client = TestClient(create_app())

    first_response = client.get("/worklist")
    assert first_response.status_code == 200
    assert "welcome to the golden age" in first_response.text
    assert "Create local workspace user" in first_response.text
    assert sqlite_path.exists()

    onboard_response = client.post(
        "/onboarding",
        data={
            "display_name": "Bened Example",
            "email": "bened@example.com",
            "password": "secret-passphrase",
        },
        files={"profile_picture": ("profile.png", b"fake-image", "image/png")},
    )
    assert onboard_response.status_code == 200
    assert "welcome to the golden age" in onboard_response.text
    assert "Bened Example" in onboard_response.text
    assert "bened@example.com" in onboard_response.text
    assert "Alex Example" not in onboard_response.text
    assert "/profiles/" in onboard_response.text

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            "SELECT email, display_name, profile_image_path FROM app_user"
        ).fetchone()

    assert row is not None
    assert row[0] == "bened@example.com"
    assert row[1] == "Bened Example"
    assert row[2] is not None
    assert (artifact_dir / "profiles" / row[2]).exists()


def test_local_first_settings_and_logout_flow(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(artifact_dir))
    client = TestClient(create_app())
    client.post(
        "/onboarding",
        data={
            "display_name": "Bened Example",
            "email": "bened@example.com",
            "password": "secret-passphrase",
        },
    )

    settings_response = client.get("/settings")
    assert settings_response.status_code == 200
    assert "Settings" in settings_response.text
    assert "Import defaults" in settings_response.text

    mail_response = client.post(
        "/settings/mail",
        data={
            "account_name": "bened@example.com",
            "mailbox_name": "Inbox",
            "sender_filter": "acme.example",
            "subject_filter": "Renewal",
            "sent_after": "2026-04-12T09:30",
            "result_limit": "7",
            "unread_only": "on",
        },
    )
    assert mail_response.status_code == 200
    assert "Mail defaults saved." in mail_response.text
    assert 'value="bened@example.com"' in mail_response.text
    assert 'value="Inbox"' in mail_response.text
    assert 'value="2026-04-12T09:30"' in mail_response.text

    logout_response = client.post("/logout")
    assert logout_response.status_code == 200
    assert "Sign in to GoldenAge" in logout_response.text

    worklist_response = client.get("/worklist")
    assert worklist_response.status_code == 200
    assert "Sign in to GoldenAge" in worklist_response.text

    login_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "secret-passphrase"},
    )
    assert login_response.status_code == 200
    assert "Bened Example" in login_response.text


def test_local_first_password_change_updates_login_credentials(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(artifact_dir))
    client = TestClient(create_app())
    client.post(
        "/onboarding",
        data={
            "display_name": "Bened Example",
            "email": "bened@example.com",
            "password": "secret-passphrase",
        },
    )

    response = client.post(
        "/settings/password",
        data={
            "current_password": "secret-passphrase",
            "new_password": "new-passphrase",
            "confirm_password": "new-passphrase",
        },
    )
    assert response.status_code == 200
    assert "Password changed." in response.text

    client.post("/logout")
    old_login_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "secret-passphrase"},
    )
    assert old_login_response.status_code == 401
    assert "Invalid email or password." in old_login_response.text

    new_login_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "new-passphrase"},
    )
    assert new_login_response.status_code == 200
    assert "Bened Example" in new_login_response.text


def test_local_first_intake_can_create_new_case_when_search_has_no_results(
    tmp_path,
    monkeypatch,
) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: _sample_extracted_data(
            subject="totally unrelated phrase cluster"
        ),
    )
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(artifact_dir))
    client = TestClient(create_app())

    onboard_response = client.post(
        "/onboarding",
        data={
            "display_name": "Bened Example",
            "email": "bened@example.com",
            "password": "secret-passphrase",
        },
    )
    assert onboard_response.status_code == 200

    upload_response = client.post(
        "/artifacts/upload",
        files={"file": ("empty.msg", b"fake msg bytes", "application/vnd.ms-outlook")},
    )
    assert upload_response.status_code == 200
    assert "Create new case" in upload_response.text

    import re

    artifact_id = re.search(r"/artifacts/([0-9a-f-]+)/create-case", upload_response.text).group(1)

    create_response = client.post(
        f"/artifacts/{artifact_id}/create-case",
        data={
            "title": "Fresh intake matter",
            "company": "Acme GmbH",
            "primary_contact": "Max Mustermann",
            "next_step": "Review the new matter and respond",
            "next_due_at": "2026-04-13T09:00",
        },
    )
    assert create_response.status_code == 200
    assert "Fresh intake matter" in create_response.text
    assert "Artifact assigned and next step scheduled." not in create_response.text


def _sample_extracted_data(subject: str) -> ExtractedArtifactData:
    return ExtractedArtifactData(
        source_kind="outlook_msg",
        parse_status="parsed",
        content_text="body text",
        subject=subject,
        sender=MailParticipant(name="Max Mustermann", email="max@acme.example"),
        recipients=(MailParticipant(name="Alex Example", email="alex@example.com"),),
        sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
        received_at=datetime(2026, 4, 12, 9, 31, tzinfo=UTC),
        direction="inbound",
        source_account_id="account-1",
        source_folder_id="inbox",
        source_message_id="message-1",
        conversation_id="conv-1",
        internet_message_id="<msg-1@example.com>",
    )

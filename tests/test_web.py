import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

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
    monkeypatch.delenv("GOLDENAGE_MAIL_FIXTURE_PATH", raising=False)
    monkeypatch.delenv("GOLDENAGE_MAIL_CLIENT_MODE", raising=False)
    client = TestClient(create_app())

    response = client.get("/worklist")

    assert response.status_code == 200
    assert "GoldenAge" in response.text
    assert "Due activities" in response.text
    assert 'rel="icon"' in response.text
    assert "golden_age_favicon_48.ico" in response.text
    assert "Mail Import" not in response.text


def test_demo_mail_import_requires_explicit_fixture_or_mode(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    monkeypatch.delenv("GOLDENAGE_MAIL_FIXTURE_PATH", raising=False)
    monkeypatch.delenv("GOLDENAGE_MAIL_CLIENT_MODE", raising=False)
    client = TestClient(create_app())

    response = client.post("/mail/desktop-mail/search", data={"result_limit": "10"})

    assert response.status_code == 200
    assert "Mail Import" not in response.text
    assert "Import into intake" not in response.text


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
    long_body = "body text " + ("unbroken" * 40)
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: ExtractedArtifactData(
            source_kind="outlook_msg",
            parse_status="parsed",
            content_text=long_body,
            subject="Acme contract renewal",
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
    assert '<pre class="mail-body">' in detail_response.text
    assert long_body in detail_response.text

    page_response = client.get(f"/worklist?case_id={case_id}&artifact_id={artifact_id}")
    assert page_response.status_code == 200
    assert '<main id="workspace" class="workspace">' in page_response.text
    assert "Due activities" in page_response.text
    assert "Message content" in page_response.text
    assert '<pre class="mail-body">' in page_response.text


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


def test_recent_conversation_opens_fallback_triage_when_no_suggestion(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: _sample_extracted_data(
            subject="totally unrelated phrase cluster"
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
        files={"file": ("unmatched.msg", b"fake msg bytes", "application/vnd.ms-outlook")},
    )

    import re

    conversation_id = re.search(
        r"/intake/conversations/([0-9a-f-]+)",
        upload_response.text,
    ).group(1)

    response = client.get(f"/intake/conversations/{conversation_id}")

    assert response.status_code == 200
    assert "No safe single-case match was found" in response.text
    assert "Search cases" in response.text
    assert "Create new case" in response.text


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
    assert "This upload type is not supported yet." in response.text
    captured = capsys.readouterr()
    assert "This upload type is not supported yet." in captured.out


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
    assert "Create your local workspace" in first_response.text
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
    assert "Local workspace" in onboard_response.text
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


def test_local_first_desktop_mail_search_and_import_flow(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    fixture_path = tmp_path / "mail-fixture.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "mail-1",
                    "account_name": "bened@example.com",
                    "mailbox_name": "Inbox",
                    "subject": "Acme contract renewal",
                    "sender_name": "Max Mustermann",
                    "sender_email": "max@acme.example",
                    "sent_at": "2026-04-12T09:30:00+00:00",
                    "preview_text": "Please review the latest renewal draft.",
                    "unread": True,
                    "rfc_message_id": "<mail-1@example.com>",
                    "raw_source": (
                        "From: Max Mustermann <max@acme.example>\n"
                        "To: Bened Example <bened@example.com>\n"
                        "Subject: Acme contract renewal\n"
                        "Date: Sun, 12 Apr 2026 09:30:00 +0000\n"
                        "Message-ID: <mail-1@example.com>\n"
                        "\n"
                        "Please review the latest renewal draft.\n"
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GOLDENAGE_LOCAL_FIRST_MODE", "sqlite3")
    monkeypatch.setenv("GOLDENAGE_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.setenv("GOLDENAGE_MAIL_FIXTURE_PATH", str(fixture_path))
    client = TestClient(create_app())
    client.post(
        "/onboarding",
        data={
            "display_name": "Bened Example",
            "email": "bened@example.com",
            "password": "secret-passphrase",
        },
    )

    search_response = client.post(
        "/mail/desktop-mail/search",
        data={
            "account_name": "bened@example.com",
            "mailbox_name": "Inbox",
            "sender_filter": "max@acme.example",
            "subject_filter": "renewal",
            "result_limit": "10",
            "unread_only": "on",
        },
    )

    assert search_response.status_code == 200
    assert "Mail candidates loaded" in search_response.text
    assert "Import into intake" in search_response.text
    assert "Acme contract renewal" in search_response.text
    assert 'hx-indicator="find .busy-indicator"' in search_response.text
    assert 'role="status" aria-live="polite">Importing' in search_response.text

    import_response = client.post(
        "/mail/desktop-mail/import",
        data={"candidate_id": "mail-1"},
    )

    assert import_response.status_code == 200
    assert "Mail message imported" in import_response.text
    assert "Create new case" in import_response.text
    assert "Acme contract renewal" in import_response.text
    assert "Import into intake" not in import_response.text
    assert import_response.text.index("intake-active") < import_response.text.index(
        "intake-dropzone"
    )


def test_demo_mode_enables_desktop_mail_import_with_fixture(tmp_path, monkeypatch) -> None:
    artifact_dir = tmp_path / "artifacts"
    fixture_path = tmp_path / "mail-fixture.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "mail-1",
                    "account_name": "alex@example.com",
                    "mailbox_name": "Inbox",
                    "subject": "Acme contract renewal",
                    "sender_email": "max@acme.example",
                    "sent_at": "2026-04-12T09:30:00+00:00",
                    "preview_text": "Please review the latest renewal draft.",
                    "unread": True,
                    "raw_source": (
                        "From: Max Mustermann <max@acme.example>\n"
                        "To: Alex Example <alex@example.com>\n"
                        "Subject: Acme contract renewal\n"
                        "\n"
                        "Please review the latest renewal draft.\n"
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.setenv("GOLDENAGE_MAIL_FIXTURE_PATH", str(fixture_path))
    client = TestClient(create_app())

    page_response = client.get("/worklist")
    search_response = client.post(
        "/mail/desktop-mail/search",
        data={
            "account_name": "alex@example.com",
            "subject_filter": "renewal",
            "result_limit": "10",
            "unread_only": "on",
        },
    )

    assert page_response.status_code == 200
    assert "Mail Import" in page_response.text
    assert 'hx-indicator="find .busy-indicator"' in page_response.text
    assert search_response.status_code == 200
    assert "Import into intake" in search_response.text


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
    assert "Upload mail" in upload_response.text
    assert 'value="totally unrelated phrase cluster"' in upload_response.text
    assert upload_response.text.index("intake-active") < upload_response.text.index(
        "intake-dropzone"
    )

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


@pytest.mark.parametrize("file_name", ["../outside.msg", "/tmp/outside.msg", "<script>.msg"])
def test_upload_keeps_original_name_out_of_storage_path(tmp_path, monkeypatch, file_name) -> None:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("GOLDENAGE_ARTIFACT_DIR", str(root))
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    for name in ("DATABASE_URL", "GOLDENAGE_LOCAL_FIRST_MODE", "GOLDENAGE_LOCAL_FIRST_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        OutlookMsgExtractor,
        "extract",
        lambda self, file_name, media_type, content: _sample_extracted_data(
            subject="Acme contract renewal"
        ),
    )
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/artifacts/upload",
            files={"file": (file_name, b"original bytes", "application/vnd.ms-outlook")},
        )
        assert response.status_code == 200
        artifact_id = UUID(re.search(r"/artifacts/([0-9a-f-]+)/assign", response.text).group(1))
        context = app.state.context
        artifact = context.service.get_intake_state(
            artifact_id=artifact_id, user=context.default_user
        ).artifact
        assert artifact.file_name == file_name
        assert Path(artifact.storage_key) == root / f"{artifact_id}.bin"
        assert Path(artifact.storage_key).read_bytes() == b"original bytes"
        assert not (tmp_path / "outside.msg").exists()
        assert "<script>.msg" not in response.text

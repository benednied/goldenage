import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from goldenage.adapters.demo import OutlookMsgExtractor
from goldenage.domain.models import ExtractedArtifactData, MailParticipant
from goldenage.web.app import create_app


def test_login_page_renders_video_and_auth_options(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    response = client.get("/login")

    assert response.status_code == 200
    assert "corporate-login.webm" in response.text
    assert "corporate-login.mp4" in response.text
    assert "Corporate video unavailable" in response.text
    assert "Workspace credentials" in response.text
    assert "LocalDB" in response.text
    assert "LDAP" in response.text
    assert "Entra ID" in response.text
    assert "Enterprise SSO placeholder" not in response.text
    assert "Coming soon" not in response.text
    assert "controls" not in response.text


def test_worklist_redirects_to_login_when_unauthenticated(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    response = client.get("/worklist", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_entraid_dummy_login_reaches_worklist(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    login_response = client.post("/login/entraid", follow_redirects=False)
    response = client.get("/worklist")

    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/worklist"
    assert response.status_code == 200
    assert "Due activities" in response.text


def test_ldap_dummy_login_requires_credentials_and_reaches_worklist(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())

    missing_response = client.post("/login/ldap")
    login_response = client.post(
        "/login/ldap",
        data={"username": "corp\\bened", "password": "temporary-password"},
        follow_redirects=False,
    )
    response = client.get("/worklist")

    assert missing_response.status_code == 400
    assert "Corporate LDAP username and password are required." in missing_response.text
    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/worklist"
    assert response.status_code == 200
    assert "Due activities" in response.text


def test_worklist_page_renders(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENAGE_DISABLE_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_MODE", raising=False)
    monkeypatch.delenv("GOLDENAGE_LOCAL_FIRST_DB", raising=False)
    monkeypatch.delenv("GOLDENAGE_SQLITE_PATH", raising=False)
    client = TestClient(create_app())
    _login_entraid(client)

    response = client.get("/worklist")

    assert response.status_code == 200
    assert "GoldenAge" in response.text
    assert "Due activities" in response.text


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
    _login_entraid(client)

    upload_response = client.post(
        "/artifacts/upload",
        files={"file": ("acme.msg", b"fake msg bytes", "application/vnd.ms-outlook")},
    )
    assert upload_response.status_code == 200
    assert "Confirm suggestion" in upload_response.text

    import re

    match = re.search(r"/artifacts/([0-9a-f-]+)/assign", upload_response.text)
    assert match is not None
    artifact_id = match.group(1)

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
    _login_entraid(client)

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


def test_local_first_sqlite_login_validates_localdb_credentials(tmp_path, monkeypatch) -> None:
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
    client.post("/logout", follow_redirects=False)

    bad_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "wrong-passphrase"},
    )
    good_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "secret-passphrase"},
        follow_redirects=False,
    )
    worklist_response = client.get("/worklist")

    assert bad_response.status_code == 400
    assert "Email or password is incorrect." in bad_response.text
    assert good_response.status_code == 303
    assert good_response.headers["location"] == "/worklist"
    assert worklist_response.status_code == 200
    assert "Bened Example" in worklist_response.text


def test_settings_saves_mail_defaults_and_changes_local_password(tmp_path, monkeypatch) -> None:
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
    mail_response = client.post(
        "/settings/mail",
        data={
            "account_name": "T-Online",
            "mailbox_name": "Inbox",
            "unread_only": "on",
            "result_limit": "10",
        },
    )
    bad_password_response = client.post(
        "/settings/password",
        data={
            "current_password": "wrong-passphrase",
            "new_password": "changed-passphrase",
            "confirm_password": "changed-passphrase",
        },
    )
    password_response = client.post(
        "/settings/password",
        data={
            "current_password": "secret-passphrase",
            "new_password": "changed-passphrase",
            "confirm_password": "changed-passphrase",
        },
    )

    assert settings_response.status_code == 200
    assert "Import defaults" in settings_response.text
    assert mail_response.status_code == 200
    assert "Mail defaults saved." in mail_response.text
    assert bad_password_response.status_code == 400
    assert "Current password is incorrect." in bad_password_response.text
    assert "message-error" in bad_password_response.text
    assert password_response.status_code == 200
    assert "Password changed." in password_response.text

    with sqlite3.connect(sqlite_path) as connection:
        selector_row = connection.execute(
            """
            SELECT account_name, mailbox_name, unread_only, result_limit
            FROM mail_import_selector
            """
        ).fetchone()
        password_hash = connection.execute(
            "SELECT password_hash FROM app_user WHERE email = 'bened@example.com'"
        ).fetchone()[0]

    assert selector_row == ("T-Online", "Inbox", 1, 10)
    assert password_hash != "changed-passphrase"
    assert password_hash.startswith("pbkdf2_sha256$600000$")

    client.post("/logout", follow_redirects=False)
    old_login_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "secret-passphrase"},
    )
    new_login_response = client.post(
        "/login/local",
        data={"email": "bened@example.com", "password": "changed-passphrase"},
        follow_redirects=False,
    )

    assert old_login_response.status_code == 400
    assert "Email or password is incorrect." in old_login_response.text
    assert new_login_response.status_code == 303
    assert new_login_response.headers["location"] == "/worklist"


def test_local_first_apple_mail_selector_imports_candidate_into_intake(
    tmp_path,
    monkeypatch,
) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    fixture_path = tmp_path / "apple-mail.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "apple-1",
                    "account_name": "iCloud",
                    "mailbox_name": "Inbox",
                    "subject": "Acme contract renewal",
                    "sender_name": "Max Mustermann",
                    "sender_email": "max@acme.example",
                    "sent_at": "2026-04-12T09:30:00+00:00",
                    "preview_text": "Please review the latest renewal draft.",
                    "unread": True,
                    "rfc_message_id": "<apple-1@example.com>",
                    "raw_source": (
                        "From: Max Mustermann <max@acme.example>\n"
                        "To: Bened Example <bened@example.com>\n"
                        "Subject: Acme contract renewal\n"
                        "Date: Sun, 12 Apr 2026 09:30:00 +0000\n"
                        "Message-ID: <apple-1@example.com>\n"
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

    case_id = str(uuid4())
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            INSERT INTO case_file (id, title, company, primary_contact, status, last_activity_at, visible_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                "Acme contract renewal",
                "Acme GmbH",
                "Max Mustermann",
                "open",
                "2026-04-12T08:00:00+00:00",
                None,
            ),
        )
        connection.commit()

    search_response = client.post(
        "/mail/desktop-mail/search",
        data={"mailbox_name": "Inbox", "unread_only": "on", "result_limit": 10},
    )
    assert search_response.status_code == 200
    assert "Import into intake" in search_response.text
    assert "Acme contract renewal" in search_response.text

    import_response = client.post(
        "/mail/desktop-mail/import",
        data={"candidate_id": "apple-1"},
    )
    assert import_response.status_code == 200
    assert "Confirm suggestion" in import_response.text
    assert "Imported candidates disappear from the review queue." in import_response.text

    with sqlite3.connect(sqlite_path) as connection:
        mail_row = connection.execute(
            """
            SELECT artifact_id, source_system, message_format, external_message_id,
                   source_account, source_mailbox
            FROM artifact_mail_metadata
            """
        ).fetchone()

    assert mail_row is not None
    assert mail_row[1] == "desktop_mail_client"
    assert mail_row[2] == "rfc822_email"
    assert mail_row[3] == "apple-1"
    assert mail_row[4] == "iCloud"
    assert mail_row[5] == "Inbox"

    worklist_response = client.get("/worklist")
    intake_response = client.get(f"/artifacts/{mail_row[0]}/intake")
    assert worklist_response.status_code == 200
    assert "Recent imports" in worklist_response.text
    assert "acme-contract-renewal.eml" in worklist_response.text
    assert intake_response.status_code == 200
    assert "intake-active" in intake_response.text
    assert 'hx-swap="innerHTML show:top"' in intake_response.text
    assert "Confirm suggestion" in intake_response.text

    duplicate_search_response = client.post(
        "/mail/desktop-mail/search",
        data={"mailbox_name": "Inbox", "unread_only": "on", "result_limit": 10},
    )
    assert duplicate_search_response.status_code == 200
    assert "Already imported: 1 matching mail message is already in intake." in (
        duplicate_search_response.text
    )
    assert "message-error" in duplicate_search_response.text


def test_local_first_apple_mail_search_normalizes_city_names_and_surrogate_preview(
    tmp_path,
    monkeypatch,
) -> None:
    sqlite_path = tmp_path / "goldenage.sqlite3"
    artifact_dir = tmp_path / "artifacts"
    fixture_path = tmp_path / "apple-mail.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "nuernberg-1",
                    "account_name": "T-Online",
                    "mailbox_name": "Nürnberg",
                    "subject": "Post aus Nürnberg",
                    "sender_name": "Nürnberg Updates",
                    "sender_email": "updates@example.com",
                    "sent_at": "2026-04-26T12:15:18+00:00",
                    "preview_text": "Emoji-heavy preview \ud83e",
                    "unread": True,
                    "rfc_message_id": "<nuernberg-1@example.com>",
                    "raw_source": (
                        "From: Nürnberg Updates <updates@example.com>\n"
                        "To: Bened Example <bened@example.com>\n"
                        "Subject: Post aus Nürnberg\n"
                        "Date: Sun, 26 Apr 2026 12:15:18 +0000\n"
                        "Message-ID: <nuernberg-1@example.com>\n"
                        "\n"
                        "Hallo aus Nürnberg.\n"
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
            "account_name": "T-Online",
            "mailbox_name": "Nürnberg",
            "subject_filter": "Nurnberg",
            "unread_only": "on",
            "result_limit": 10,
        },
    )

    assert search_response.status_code == 200
    assert "Import into intake" in search_response.text
    assert "Post aus Nürnberg" in search_response.text

    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT mailbox_name, subject, preview_text
            FROM mail_import_review_candidate
            WHERE candidate_id = 'nuernberg-1'
            """
        ).fetchone()

    assert row == ("Nürnberg", "Post aus Nürnberg", "Emoji-heavy preview �")


def _sample_extracted_data(subject: str) -> ExtractedArtifactData:
    return ExtractedArtifactData(
        message_format="outlook_msg",
        parse_status="parsed",
        rfc_message_id=None,
        content_text="body text",
        subject=subject,
        sender=MailParticipant(name="Max Mustermann", email="max@acme.example"),
        recipients=(MailParticipant(name="Alex Example", email="alex@example.com"),),
        sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
    )


def _login_entraid(client: TestClient) -> None:
    response = client.post("/login/entraid", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/worklist"

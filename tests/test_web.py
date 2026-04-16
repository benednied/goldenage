from datetime import UTC, datetime
import sqlite3

from fastapi.testclient import TestClient

from goldenage.adapters.demo import OutlookMsgExtractor
from goldenage.domain.models import ExtractedArtifactData, MailParticipant
from goldenage.web.app import create_app


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
    )

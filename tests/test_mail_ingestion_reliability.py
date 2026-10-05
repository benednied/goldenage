import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from goldenage.adapters.demo import HeuristicElizabethanSearchClient
from goldenage.adapters.sqlite import (
    SQLiteActivityRepository,
    SQLiteArtifactRepository,
    SQLiteAuditRepository,
    SQLiteCaseRepository,
)
from goldenage.application.use_cases import GoldenAgeService
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped
from goldenage.domain.models import (
    AssignmentSuggestion,
    ExtractedArtifactData,
    MailParticipant,
    UserContext,
)

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


class RecordingStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir()
        self.stored: list[str] = []
        self.deleted: list[str] = []

    def store(self, artifact_id, file_name, content) -> str:
        del file_name
        key = self.root / f"{artifact_id}.bin"
        key.write_bytes(content)
        self.stored.append(str(key))
        return str(key)

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        Path(storage_key).unlink()


class StaticExtractor:
    def extract(self, file_name, media_type, content) -> ExtractedArtifactData:
        del file_name, media_type, content
        return ExtractedArtifactData(
            content_text="same extracted body",
            subject="Same subject",
            sender=MailParticipant(name="Sender", email="sender@example.com"),
            recipients=(),
            sent_at=NOW,
        )


class FailingExtractor:
    def extract(self, file_name, media_type, content) -> ExtractedArtifactData:
        del file_name, media_type, content
        raise ValueError("parser failed")


class ToggleGisela:
    def __init__(self) -> None:
        self.fail = False

    def analyze_artifact(self, artifact, mail_metadata, visible_cases, now) -> AssignmentSuggestion:
        del mail_metadata, visible_cases
        if self.fail:
            raise RuntimeError("suggestion service failed")
        return AssignmentSuggestion(
            artifact_id=artifact.id,
            suggested_case_id=None,
            summary_reason="No safe match",
            confidence=0,
            created_at=now,
        )


def build_sqlite_service(
    tmp_path: Path,
    *,
    user: UserContext,
    gisela: ToggleGisela | None = None,
    extractor=None,
):
    database_path = tmp_path / "goldenage.sqlite3"
    ensure_sqlite_bootstrapped(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (str(user.id), user.email, user.display_name, "test-hash"),
        )
    return (
        GoldenAgeService(
            case_repository=SQLiteCaseRepository(database_path),
            activity_repository=SQLiteActivityRepository(database_path),
            artifact_repository=SQLiteArtifactRepository(database_path),
            audit_repository=SQLiteAuditRepository(database_path),
            artifact_store=RecordingStore(tmp_path / "files"),
            content_extractor=extractor or StaticExtractor(),
            gisela_client=gisela or ToggleGisela(),
            elizabethan_client=HeuristicElizabethanSearchClient(),
        ),
        database_path,
    )


def test_duplicate_manual_upload_is_claimed_before_a_second_permanent_file(tmp_path) -> None:
    user = UserContext(id=uuid4(), email="owner@example.com", display_name="Owner")
    service, database_path = build_sqlite_service(tmp_path, user=user)
    first = service.upload_artifact(
        file_name="first.msg",
        media_type="application/vnd.ms-outlook",
        content=b"same bytes",
        user=user,
        now=NOW,
    )
    second = service.upload_artifact(
        file_name="second.msg",
        media_type="application/vnd.ms-outlook",
        content=b"same bytes",
        user=user,
        now=NOW,
    )

    assert first.artifact is not None
    assert second.artifact is not None
    assert second.artifact.id == first.artifact.id
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM mail_message").fetchone() == (1,)
    assert len(list((tmp_path / "files").iterdir())) == 1


def test_identical_mail_is_scoped_to_the_authenticated_user(tmp_path) -> None:
    first_user = UserContext(id=uuid4(), email="first@example.com", display_name="First")
    second_user = UserContext(id=uuid4(), email="second@example.com", display_name="Second")
    service, database_path = build_sqlite_service(tmp_path, user=first_user)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (str(second_user.id), second_user.email, second_user.display_name, "test-hash"),
        )
    second_service = GoldenAgeService(
        case_repository=SQLiteCaseRepository(database_path),
        activity_repository=SQLiteActivityRepository(database_path),
        artifact_repository=SQLiteArtifactRepository(database_path),
        audit_repository=SQLiteAuditRepository(database_path),
        artifact_store=RecordingStore(tmp_path / "second-files"),
        content_extractor=StaticExtractor(),
        gisela_client=ToggleGisela(),
        elizabethan_client=HeuristicElizabethanSearchClient(),
    )
    first = service.upload_artifact(
        file_name="first.msg",
        media_type="application/vnd.ms-outlook",
        content=b"same bytes",
        user=first_user,
        now=NOW,
    )
    second = second_service.upload_artifact(
        file_name="second.msg",
        media_type="application/vnd.ms-outlook",
        content=b"same bytes",
        user=second_user,
        now=NOW,
    )

    assert first.artifact is not None
    assert second.artifact is not None
    assert second.artifact.id != first.artifact.id
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM mail_message").fetchone() == (2,)


def test_parser_and_database_failures_leave_no_permanent_artifact(tmp_path, monkeypatch) -> None:
    user = UserContext(id=uuid4(), email="owner@example.com", display_name="Owner")
    service, database_path = build_sqlite_service(
        tmp_path / "parser",
        user=user,
        extractor=FailingExtractor(),
    )
    with pytest.raises(ValueError, match="parser failed"):
        service.upload_artifact(
            file_name="bad.msg",
            media_type="application/vnd.ms-outlook",
            content=b"bad",
            user=user,
            now=NOW,
        )
    assert list((tmp_path / "parser" / "files").iterdir()) == []
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone() == (0,)

    service, database_path = build_sqlite_service(tmp_path / "database", user=user)
    repository = service._artifact_repository
    monkeypatch.setattr(
        repository,
        "save_mail_ingestion",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("database failed")),
    )
    with pytest.raises(RuntimeError, match="database failed"):
        service.upload_artifact(
            file_name="db-failure.msg",
            media_type="application/vnd.ms-outlook",
            content=b"db-failure",
            user=user,
            now=NOW,
        )
    assert list((tmp_path / "database" / "files").iterdir()) == []
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone() == (0,)


def test_failed_suggestion_is_visible_and_retryable_without_reimport(tmp_path) -> None:
    user = UserContext(id=uuid4(), email="owner@example.com", display_name="Owner")
    gisela = ToggleGisela()
    gisela.fail = True
    service, database_path = build_sqlite_service(tmp_path, user=user, gisela=gisela)
    failed = service.upload_artifact(
        file_name="retry.msg",
        media_type="application/vnd.ms-outlook",
        content=b"retry me",
        user=user,
        now=NOW,
    )
    assert failed.artifact is not None
    assert failed.suggestion is None
    assert failed.ingest_status == "analysis_failed"
    assert service.list_unassigned_intake(user=user)
    persisted = service.get_intake_state(artifact_id=failed.artifact.id, user=user)
    assert persisted.ingest_status == "analysis_failed"
    assert persisted.message_kind == "error"

    gisela.fail = False
    retried = service.retry_artifact_analysis(
        artifact_id=failed.artifact.id,
        user=user,
        now=NOW,
    )
    assert retried.artifact is not None
    assert retried.artifact.id == failed.artifact.id
    assert retried.suggestion is not None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM mail_message").fetchone() == (1,)
        events = {
            row[0] for row in connection.execute("SELECT event_type FROM audit_event").fetchall()
        }
    assert "artifact_analysis_failed" in events
    assert "artifact_analysis_retried" in events

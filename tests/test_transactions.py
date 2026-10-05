import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from goldenage.adapters import postgres
from goldenage.adapters.demo import HeuristicElizabethanSearchClient, HeuristicGiselaClient
from goldenage.adapters.sqlite import (
    SQLiteActivityRepository,
    SQLiteArtifactRepository,
    SQLiteAuditRepository,
    SQLiteCaseRepository,
    SQLiteUnitOfWork,
)
from goldenage.application.use_cases import GoldenAgeService
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped
from goldenage.domain.models import (
    Activity,
    Artifact,
    AuditEvent,
    CaseFile,
    ExtractedArtifactData,
    UserContext,
)

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


class NoopArtifactStore:
    def store(self, artifact_id: UUID, file_name: str, content: bytes) -> str:
        del file_name, content
        return f"stored/{artifact_id}"


class NoopContentExtractor:
    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        del file_name, media_type, content
        raise AssertionError("content extraction is outside this test")


class FakePostgresCursor:
    def __enter__(self) -> "FakePostgresCursor":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def execute(self, sql: str, params: object) -> None:
        del sql, params


class FakePostgresConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> FakePostgresCursor:
        return FakePostgresCursor()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _build_runtime(
    tmp_path: Path,
) -> tuple[
    GoldenAgeService,
    UserContext,
    SQLiteUnitOfWork,
    SQLiteCaseRepository,
    SQLiteActivityRepository,
    SQLiteArtifactRepository,
    SQLiteAuditRepository,
    CaseFile,
    Activity,
    Artifact,
]:
    database_path = tmp_path / "goldenage.sqlite3"
    ensure_sqlite_bootstrapped(database_path)
    user = UserContext(id=uuid4(), email="alex@example.com", display_name="Alex")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (str(user.id), user.email, user.display_name, "hash"),
        )

    unit_of_work = SQLiteUnitOfWork(database_path)
    case_repository = SQLiteCaseRepository(database_path, unit_of_work=unit_of_work)
    activity_repository = SQLiteActivityRepository(database_path, unit_of_work=unit_of_work)
    artifact_repository = SQLiteArtifactRepository(database_path, unit_of_work=unit_of_work)
    audit_repository = SQLiteAuditRepository(database_path, unit_of_work=unit_of_work)
    case_file = CaseFile(
        id=uuid4(),
        title="Acme renewal",
        company="Acme",
        primary_contact="Max",
        status="open",
        last_activity_at=NOW,
    )
    activity = Activity(
        id=uuid4(),
        case_id=case_file.id,
        description="Review the renewal",
        kind="follow_up",
        due_at=NOW,
        created_at=NOW,
        created_by=user.id,
    )
    artifact = Artifact(
        id=uuid4(),
        file_name="renewal.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=4,
        content_text="Renewal",
        storage_key="stored/renewal",
        uploaded_at=NOW,
        uploaded_by=user.id,
    )
    case_repository.save_case(case_file)
    activity_repository.save_activity(activity)
    artifact_repository.save_artifact(artifact)
    service = GoldenAgeService(
        case_repository=case_repository,
        activity_repository=activity_repository,
        artifact_repository=artifact_repository,
        audit_repository=audit_repository,
        artifact_store=NoopArtifactStore(),
        content_extractor=NoopContentExtractor(),
        gisela_client=HeuristicGiselaClient(),
        elizabethan_client=HeuristicElizabethanSearchClient(),
        unit_of_work=unit_of_work,
    )
    return (
        service,
        user,
        unit_of_work,
        case_repository,
        activity_repository,
        artifact_repository,
        audit_repository,
        case_file,
        activity,
        artifact,
    )


def _count(database_path: Path, table_name: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "goldenage.sqlite3"


def test_postgres_unit_of_work_shares_connection_and_closes_on_commit_or_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakePostgresConnection()
    monkeypatch.setattr(
        postgres,
        "psycopg",
        SimpleNamespace(connect=lambda dsn, row_factory: connection),
    )
    monkeypatch.setattr(postgres, "Jsonb", lambda value: value)
    unit_of_work = postgres.PostgresUnitOfWork("postgresql://example")
    case_repository = postgres.PostgresCaseRepository(
        "postgresql://example",
        unit_of_work=unit_of_work,
    )

    case_file = CaseFile(
        id=uuid4(),
        title="Acme renewal",
        company=None,
        primary_contact=None,
        status="open",
        last_activity_at=NOW,
    )
    with unit_of_work.transaction():
        case_repository.save_case(case_file)
        assert unit_of_work.current_connection is connection
        assert connection.commits == 0
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed
    assert unit_of_work.current_connection is None

    connection = FakePostgresConnection()
    monkeypatch.setattr(
        postgres,
        "psycopg",
        SimpleNamespace(connect=lambda dsn, row_factory: connection),
    )
    with pytest.raises(RuntimeError, match="boom"):
        with unit_of_work.transaction():
            raise RuntimeError("boom")
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed
    assert unit_of_work.current_connection is None


@pytest.mark.parametrize("failure_point", ("first_activity", "follow_up", "case", "audit"))
def test_resolution_rolls_back_after_each_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    runtime = _build_runtime(tmp_path)
    (
        service,
        user,
        unit_of_work,
        case_repository,
        activity_repository,
        _,
        audit_repository,
        case_file,
        activity,
        _,
    ) = runtime

    original_activity_save = activity_repository.save_activity
    original_case_save = case_repository.save_case
    original_audit_save = audit_repository.save_event
    activity_calls = 0

    def failing_activity_save(value: Activity) -> None:
        nonlocal activity_calls
        activity_calls += 1
        original_activity_save(value)
        if failure_point == "first_activity" or (
            failure_point == "follow_up" and activity_calls == 2
        ):
            raise RuntimeError("activity write failed")

    def failing_case_save(value: CaseFile) -> None:
        original_case_save(value)
        if failure_point == "case":
            raise RuntimeError("case write failed")

    def failing_audit_save(value: AuditEvent) -> None:
        original_audit_save(value)
        if failure_point == "audit":
            raise RuntimeError("audit write failed")

    monkeypatch.setattr(activity_repository, "save_activity", failing_activity_save)
    monkeypatch.setattr(case_repository, "save_case", failing_case_save)
    monkeypatch.setattr(audit_repository, "save_event", failing_audit_save)

    with pytest.raises(RuntimeError, match="write failed"):
        service.resolve_activity(
            activity_id=activity.id,
            user=user,
            now=NOW,
            next_step="Send revised terms",
            next_due_at=NOW,
            close_case=False,
            skip_follow_up=False,
        )

    assert unit_of_work.current_connection is None
    assert activity_repository.get_activity(activity.id, user) == activity
    assert _count(_database_path(tmp_path), "activity") == 1
    assert case_repository.get_case(case_file.id, user) == case_file
    assert _count(_database_path(tmp_path), "audit_event") == 0


def test_resolution_rolls_back_on_foreign_key_failure_after_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _build_runtime(tmp_path)
    (
        service,
        user,
        unit_of_work,
        case_repository,
        activity_repository,
        _,
        _,
        case_file,
        activity,
        _,
    ) = runtime
    original_activity_save = activity_repository.save_activity
    calls = 0

    def invalid_follow_up(value: Activity) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            value = replace(value, case_id=uuid4())
        original_activity_save(value)

    monkeypatch.setattr(activity_repository, "save_activity", invalid_follow_up)
    with pytest.raises(sqlite3.IntegrityError):
        service.resolve_activity(
            activity_id=activity.id,
            user=user,
            now=NOW,
            next_step="Send revised terms",
            next_due_at=NOW,
            close_case=False,
            skip_follow_up=False,
        )

    assert unit_of_work.current_connection is None
    assert activity_repository.get_activity(activity.id, user) == activity
    assert _count(_database_path(tmp_path), "activity") == 1
    assert case_repository.get_case(case_file.id, user) == case_file
    assert _count(_database_path(tmp_path), "audit_event") == 0


@pytest.mark.parametrize("failure_point", ("artifact", "activity", "case", "audit"))
def test_assignment_rolls_back_after_each_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    runtime = _build_runtime(tmp_path)
    (
        service,
        user,
        unit_of_work,
        case_repository,
        activity_repository,
        artifact_repository,
        audit_repository,
        case_file,
        _,
        artifact,
    ) = runtime
    original_artifact_save = artifact_repository.save_artifact
    original_activity_save = activity_repository.save_activity
    original_case_save = case_repository.save_case
    original_audit_save = audit_repository.save_event

    def failing_artifact_save(value: Artifact) -> None:
        original_artifact_save(value)
        if failure_point == "artifact":
            raise RuntimeError("artifact write failed")

    def failing_activity_save(value: Activity) -> None:
        original_activity_save(value)
        if failure_point == "activity":
            raise RuntimeError("activity write failed")

    def failing_case_save(value: CaseFile) -> None:
        original_case_save(value)
        if failure_point == "case":
            raise RuntimeError("case write failed")

    def failing_audit_save(value: AuditEvent) -> None:
        original_audit_save(value)
        if failure_point == "audit":
            raise RuntimeError("audit write failed")

    monkeypatch.setattr(artifact_repository, "save_artifact", failing_artifact_save)
    monkeypatch.setattr(activity_repository, "save_activity", failing_activity_save)
    monkeypatch.setattr(case_repository, "save_case", failing_case_save)
    monkeypatch.setattr(audit_repository, "save_event", failing_audit_save)

    with pytest.raises(RuntimeError, match="write failed"):
        service.assign_artifact_to_case(
            artifact_id=artifact.id,
            case_id=case_file.id,
            next_step="Call Max",
            next_due_at=NOW,
            user=user,
            now=NOW,
        )

    assert unit_of_work.current_connection is None
    assert artifact_repository.get_artifact(artifact.id, user) == artifact
    assert _count(_database_path(tmp_path), "activity") == 1
    assert case_repository.get_case(case_file.id, user) == case_file
    assert _count(_database_path(tmp_path), "audit_event") == 0


def test_create_case_rolls_back_orphan_case_on_foreign_key_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _build_runtime(tmp_path)
    (
        service,
        user,
        unit_of_work,
        _,
        activity_repository,
        artifact_repository,
        _,
        _,
        _,
        artifact,
    ) = runtime
    original_activity_save = activity_repository.save_activity

    def invalid_activity(value: Activity) -> None:
        original_activity_save(replace(value, case_id=uuid4()))

    monkeypatch.setattr(activity_repository, "save_activity", invalid_activity)
    with pytest.raises(sqlite3.IntegrityError):
        service.create_case_for_artifact(
            artifact_id=artifact.id,
            title="Fresh matter",
            company="Acme",
            primary_contact="Max",
            next_step="Call Max",
            next_due_at=NOW,
            user=user,
            now=NOW,
        )

    assert unit_of_work.current_connection is None
    assert _count(_database_path(tmp_path), "case_file") == 1
    assert _count(_database_path(tmp_path), "activity") == 1
    assert artifact_repository.get_artifact(artifact.id, user) == artifact


def test_assignment_and_create_case_commit_all_writes(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path / "assignment")
    (
        service,
        user,
        unit_of_work,
        _,
        _,
        artifact_repository,
        _,
        case_file,
        _,
        artifact,
    ) = runtime

    service.assign_artifact_to_case(
        artifact_id=artifact.id,
        case_id=case_file.id,
        next_step="Call Max",
        next_due_at=NOW,
        user=user,
        now=NOW,
    )
    assigned = artifact_repository.get_artifact(artifact.id, user)
    assert assigned is not None and assigned.assigned_case_id == case_file.id
    assert _count(_database_path(tmp_path / "assignment"), "activity") == 2
    assert _count(_database_path(tmp_path / "assignment"), "audit_event") == 1
    assert unit_of_work.current_connection is None

    runtime = _build_runtime(tmp_path / "create-case")
    (
        service,
        user,
        unit_of_work,
        _,
        _,
        artifact_repository,
        _,
        case_file,
        _,
        artifact,
    ) = runtime
    service.create_case_for_artifact(
        artifact_id=artifact.id,
        title="Fresh matter",
        company="Acme",
        primary_contact="Max",
        next_step="Call Max",
        next_due_at=NOW,
        user=user,
        now=NOW,
    )
    assert _count(_database_path(tmp_path / "create-case"), "case_file") == 2
    assert _count(_database_path(tmp_path / "create-case"), "activity") == 2
    assert _count(_database_path(tmp_path / "create-case"), "audit_event") == 1
    created_artifact = artifact_repository.get_artifact(artifact.id, user)
    assert created_artifact is not None and created_artifact.assigned_case_id != case_file.id
    assert unit_of_work.current_connection is None


def test_resolution_commits_all_writes_and_releases_connection(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    (
        service,
        user,
        unit_of_work,
        case_repository,
        activity_repository,
        _,
        _,
        case_file,
        activity,
        _,
    ) = runtime

    service.resolve_activity(
        activity_id=activity.id,
        user=user,
        now=NOW,
        next_step="Send revised terms",
        next_due_at=NOW,
        close_case=False,
        skip_follow_up=False,
    )

    completed = activity_repository.get_activity(activity.id, user)
    assert completed is not None and completed.completed_at == NOW
    assert _count(_database_path(tmp_path), "activity") == 2
    persisted_case = case_repository.get_case(case_file.id, user)
    assert persisted_case is not None and persisted_case.status == "open"
    assert _count(_database_path(tmp_path), "audit_event") == 1
    assert unit_of_work.current_connection is None

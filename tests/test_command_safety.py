from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import cast
from uuid import uuid4

import pytest

from goldenage.adapters.sqlite import (
    SQLiteActivityRepository,
    SQLiteArtifactRepository,
    SQLiteAuditRepository,
    SQLiteCaseRepository,
    SQLiteCommandRepository,
)
from goldenage.application.ports import (
    ArtifactContentExtractor,
    ArtifactStore,
    ElizabethanSearchClient,
    GiselaClient,
)
from goldenage.application.use_cases import GoldenAgeService
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped
from goldenage.domain.models import Activity, Artifact, CaseFile, UserContext
from goldenage.domain.rules import CommandConflictError, ResolutionError

NOW = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)


def _service(tmp_path):
    database_path = tmp_path / "commands.sqlite3"
    ensure_sqlite_bootstrapped(database_path)
    user = UserContext(uuid4(), "alex@example.com", "Alex")
    case_repository = SQLiteCaseRepository(database_path)
    activity_repository = SQLiteActivityRepository(database_path)
    artifact_repository = SQLiteArtifactRepository(database_path)
    audit_repository = SQLiteAuditRepository(database_path)
    command_repository = SQLiteCommandRepository(database_path)
    with case_repository._connect() as connection:
        connection.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (str(user.id), user.email, user.display_name, "test-hash"),
        )
        connection.commit()
    case = CaseFile(uuid4(), "Existing matter", "Acme", "Max", "open", NOW)
    case_repository.save_case(case)
    service = GoldenAgeService(
        case_repository=case_repository,
        activity_repository=activity_repository,
        artifact_repository=artifact_repository,
        audit_repository=audit_repository,
        artifact_store=cast(ArtifactStore, object()),
        content_extractor=cast(ArtifactContentExtractor, object()),
        gisela_client=cast(GiselaClient, object()),
        elizabethan_client=cast(ElizabethanSearchClient, object()),
        command_repository=command_repository,
    )
    return (
        service,
        user,
        case,
        case_repository,
        activity_repository,
        artifact_repository,
        audit_repository,
    )


def _artifact(repository: SQLiteArtifactRepository, user: UserContext) -> Artifact:
    artifact = Artifact(
        id=uuid4(),
        file_name="intake.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=1,
        content_text="body",
        storage_key="storage-key",
        uploaded_at=NOW,
        uploaded_by=user.id,
    )
    repository.save_artifact(artifact)
    return artifact


def _audit_count(repository: SQLiteAuditRepository, event_type: str) -> int:
    with repository._connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM audit_event WHERE event_type = ?", (event_type,)
        ).fetchone()[0]


def test_assignment_retry_is_one_transition_and_explicit_reassignment_is_audited(tmp_path) -> None:
    service, user, case, _, activity_repository, artifact_repository, audit_repository = _service(
        tmp_path
    )
    artifact = _artifact(artifact_repository, user)
    due_at = NOW + timedelta(days=1)
    command_id = uuid4()

    first = service.assign_artifact_to_case(
        artifact_id=artifact.id,
        case_id=case.id,
        next_step="Call Max",
        next_due_at=due_at,
        user=user,
        now=NOW,
        command_id=command_id,
    )
    retry = service.assign_artifact_to_case(
        artifact_id=artifact.id,
        case_id=case.id,
        next_step="Call Max",
        next_due_at=due_at,
        user=user,
        now=NOW + timedelta(minutes=1),
        command_id=command_id,
    )

    assert first.case_file.id == retry.case_file.id == case.id
    assert len(activity_repository.list_case_activities(case.id, user)) == 1
    assert _audit_count(audit_repository, "artifact_assigned") == 1

    with pytest.raises(CommandConflictError, match="different request"):
        service.assign_artifact_to_case(
            artifact_id=artifact.id,
            case_id=case.id,
            next_step="Different step",
            next_due_at=due_at,
            user=user,
            now=NOW,
            command_id=command_id,
        )

    service.assign_artifact_to_case(
        artifact_id=artifact.id,
        case_id=case.id,
        next_step="Intentional reassignment follow-up",
        next_due_at=due_at + timedelta(days=1),
        user=user,
        now=NOW,
        command_id=uuid4(),
        reassign=True,
    )
    assert len(activity_repository.list_case_activities(case.id, user)) == 2
    assert _audit_count(audit_repository, "artifact_reassigned") == 1


def test_create_case_retry_returns_the_committed_case_without_duplicates(tmp_path) -> None:
    (
        service,
        user,
        _,
        case_repository,
        activity_repository,
        artifact_repository,
        audit_repository,
    ) = _service(tmp_path)
    artifact = _artifact(artifact_repository, user)
    command_id = uuid4()
    due_at = NOW + timedelta(days=1)

    first = service.create_case_for_artifact(
        artifact_id=artifact.id,
        title="Fresh matter",
        company="Acme",
        primary_contact="Alex",
        next_step="Review intake",
        next_due_at=due_at,
        user=user,
        now=NOW,
        command_id=command_id,
    )
    retry = service.create_case_for_artifact(
        artifact_id=artifact.id,
        title="Fresh matter",
        company="Acme",
        primary_contact="Alex",
        next_step="Review intake",
        next_due_at=due_at,
        user=user,
        now=NOW + timedelta(minutes=2),
        command_id=command_id,
    )

    assert first.case_file.id == retry.case_file.id
    with case_repository._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM case_file WHERE title = ?", ("Fresh matter",)
            ).fetchone()[0]
            == 1
        )
    assert len(activity_repository.list_case_activities(first.case_file.id, user)) == 1
    assert _audit_count(audit_repository, "artifact_assigned") == 1


def test_concurrent_resolutions_have_one_durable_successor_and_audit(tmp_path) -> None:
    service, user, case, _, activity_repository, artifact_repository, audit_repository = _service(
        tmp_path
    )
    activity = Activity(
        id=uuid4(),
        case_id=case.id,
        description="Resolve this",
        kind="follow_up",
        due_at=NOW,
        created_at=NOW,
        created_by=user.id,
    )
    activity_repository.save_activity(activity)
    barrier = Barrier(2)

    def resolve() -> str:
        barrier.wait()
        try:
            service.resolve_activity(
                activity_id=activity.id,
                user=user,
                now=NOW,
                next_step="The one successor",
                next_due_at=NOW + timedelta(days=1),
                close_case=False,
                skip_follow_up=False,
                command_id=uuid4(),
            )
        except CommandConflictError, ResolutionError:
            return "conflict"
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: resolve(), range(2)))

    assert outcomes.count("success") == 1
    assert outcomes.count("conflict") == 1
    with activity_repository._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM activity WHERE case_id = ? AND description = ?",
                (str(case.id), "The one successor"),
            ).fetchone()[0]
            == 1
        )
    assert _audit_count(audit_repository, "activity_resolved") == 1

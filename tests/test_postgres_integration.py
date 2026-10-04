"""Integration tests for the real PostgreSQL schema and repositories.

Run this module explicitly with GOLDENAGE_TEST_POSTGRES_DSN pointing at a
disposable PostgreSQL database:

    GOLDENAGE_TEST_POSTGRES_DSN=postgresql://... pytest -m postgres_integration -q

The fixture creates a fresh schema for every test. It never truncates or
drops objects outside that schema and fails when the configured database is
missing or unreachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import ForeignKeyViolation

from goldenage import bootstrap_postgres
from goldenage.adapters.demo import build_demo_state
from goldenage.adapters.postgres import (
    PostgresActivityRepository,
    PostgresArtifactRepository,
    PostgresAuditRepository,
    PostgresCaseRepository,
)
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    MailConversation,
    MailMessage,
    MailParticipant,
    MailboxAccountConfig,
    MailboxSyncCheckpoint,
    UserContext,
)

pytestmark = pytest.mark.postgres_integration

ROOT_DIR = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT_DIR / "sql"
NOW = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)


class PostgresTestDatabase:
    """Connection and lifecycle helpers for one isolated test schema."""

    def __init__(self, dsn: str, schema: str) -> None:
        self.dsn = dsn
        self.schema = schema

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn)

    def apply_schema(self) -> None:
        bootstrap_postgres.apply_schema(self.dsn, SQL_DIR)

    def scalar(self, query: str, params: object | None = None) -> object:
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
            assert row is not None
            return row[0]

    def rows(self, query: str, params: object | None = None) -> list[tuple[object, ...]]:
        with self.connect() as connection:
            return list(connection.execute(query, params).fetchall())


@pytest.fixture
def postgres_db() -> Iterator[PostgresTestDatabase]:
    """Yield a disposable schema and fail instead of skipping on DB errors."""
    dsn = os.environ.get("GOLDENAGE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.fail(
            "GOLDENAGE_TEST_POSTGRES_DSN is required for PostgreSQL integration tests",
            pytrace=False,
        )

    schema = f"goldenage_test_{uuid4().hex}"
    try:
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            connection.commit()
    except psycopg.Error as error:
        pytest.fail(f"Could not connect to PostgreSQL integration database: {error}", pytrace=False)

    isolated_dsn = make_conninfo(dsn, options=f"-c search_path={schema},public")
    database = PostgresTestDatabase(isolated_dsn, schema)
    try:
        yield database
    finally:
        try:
            with psycopg.connect(dsn) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )
                connection.commit()
        except psycopg.Error as error:
            pytest.fail(f"Could not clean up PostgreSQL test schema: {error}", pytrace=False)


def test_bootstrap_migrations_are_idempotent_and_seed_is_repeatable(
    postgres_db: PostgresTestDatabase,
) -> None:
    """Run all migrations on an empty schema, then run bootstrap and seed twice."""
    assert postgres_db.rows(
        "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
    ) == []

    postgres_db.apply_schema()
    expected_migrations = (
        "0001_initial.sql",
        "0002_artifact_mail_metadata.sql",
        "0003_apple_mail_import.sql",
        "0003_mailbox_ingestion.sql",
    )
    assert tuple(row[0] for row in postgres_db.rows("SELECT name FROM schema_migration ORDER BY name")) == (
        *expected_migrations,
    )

    expected_tables = {
        "app_user",
        "app_group",
        "user_group_membership",
        "case_file",
        "activity",
        "artifact",
        "assignment_suggestion",
        "audit_event",
        "artifact_mail_metadata",
        "mail_import_source",
        "mail_import_selector",
        "mail_import_review_candidate",
        "imported_mail_message",
        "mail_conversation",
        "mail_message",
        "mailbox_account_config",
        "mailbox_sync_checkpoint",
    }
    actual_tables = {
        row[0]
        for row in postgres_db.rows(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
    }
    assert expected_tables <= actual_tables

    postgres_db.apply_schema()
    assert tuple(row[0] for row in postgres_db.rows("SELECT name FROM schema_migration ORDER BY name")) == (
        *expected_migrations,
    )

    state, user = build_demo_state()
    bootstrap_postgres.seed_demo_data(postgres_db.dsn)
    first_counts = {
        table: postgres_db.scalar(f"SELECT count(*) FROM {table}")
        for table in ("app_user", "case_file", "activity")
    }
    assert first_counts == {
        "app_user": 1,
        "case_file": len(state.cases),
        "activity": len(state.activities),
    }
    assert postgres_db.scalar("SELECT email FROM app_user WHERE id = %s", (user.id,)) == user.email

    bootstrap_postgres.seed_demo_data(postgres_db.dsn)
    second_counts = {
        table: postgres_db.scalar(f"SELECT count(*) FROM {table}")
        for table in ("app_user", "case_file", "activity")
    }
    assert second_counts == first_counts


def test_case_activity_and_artifact_repositories_enforce_visibility(
    postgres_db: PostgresTestDatabase,
) -> None:
    """Exercise the core CRUD paths against PostgreSQL, including group visibility."""
    postgres_db.apply_schema()
    actor_id = uuid4()
    allowed_group_id = uuid4()
    hidden_group_id = uuid4()
    other_user_id = uuid4()
    postgres_db.rows(
        """
        INSERT INTO app_user (id, email, display_name)
        VALUES (%s, %s, %s), (%s, %s, %s)
        RETURNING id
        """,
        (
            actor_id,
            "actor@example.com",
            "Actor",
            other_user_id,
            "other@example.com",
            "Other",
        ),
    )
    postgres_db.rows(
        """
        INSERT INTO app_group (id, name)
        VALUES (%s, %s), (%s, %s)
        RETURNING id
        """,
        (allowed_group_id, "Allowed", hidden_group_id, "Hidden"),
    )
    user = UserContext(
        id=actor_id,
        email="actor@example.com",
        display_name="Actor",
        visible_group_ids=frozenset({allowed_group_id}),
    )
    public_case = CaseFile(
        id=uuid4(),
        title="Public case",
        company="Public GmbH",
        primary_contact="Public Contact",
        status="open",
        last_activity_at=NOW,
    )
    allowed_case = CaseFile(
        id=uuid4(),
        title="Allowed case",
        company="Allowed GmbH",
        primary_contact=None,
        status="open",
        last_activity_at=NOW - timedelta(minutes=1),
        visible_group_id=allowed_group_id,
    )
    hidden_case = CaseFile(
        id=uuid4(),
        title="Hidden case",
        company="Hidden GmbH",
        primary_contact=None,
        status="open",
        last_activity_at=NOW - timedelta(minutes=2),
        visible_group_id=hidden_group_id,
    )
    case_repository = PostgresCaseRepository(postgres_db.dsn)
    for case_file in (public_case, allowed_case, hidden_case):
        case_repository.save_case(case_file)

    visible_cases = case_repository.list_cases(user)
    assert tuple(case_file.id for case_file in visible_cases) == (
        public_case.id,
        allowed_case.id,
    )
    assert case_repository.get_case(hidden_case.id, user) is None
    updated_public_case = CaseFile(
        id=public_case.id,
        title="Updated",
        company=public_case.company,
        primary_contact=public_case.primary_contact,
        status=public_case.status,
        last_activity_at=public_case.last_activity_at,
        visible_group_id=public_case.visible_group_id,
    )
    case_repository.save_case(updated_public_case)
    assert case_repository.get_case(public_case.id, user) == updated_public_case

    activity_repository = PostgresActivityRepository(postgres_db.dsn)
    due_public = Activity(
        id=uuid4(),
        case_id=public_case.id,
        description="Call the public contact",
        kind="follow_up",
        due_at=NOW - timedelta(hours=1),
        created_at=NOW - timedelta(days=1),
        created_by=actor_id,
    )
    due_allowed = Activity(
        id=uuid4(),
        case_id=allowed_case.id,
        description="Review the allowed case",
        kind="question",
        due_at=NOW,
        created_at=NOW - timedelta(hours=2),
        created_by=actor_id,
    )
    due_hidden = Activity(
        id=uuid4(),
        case_id=hidden_case.id,
        description="Review the hidden case",
        kind="question",
        due_at=NOW,
        created_at=NOW - timedelta(hours=3),
        created_by=actor_id,
    )
    completed_allowed = Activity(
        id=uuid4(),
        case_id=allowed_case.id,
        description="Already completed",
        kind="intake",
        due_at=NOW - timedelta(days=2),
        created_at=NOW - timedelta(days=3),
        created_by=actor_id,
        completed_at=NOW - timedelta(days=1),
    )
    for activity in (due_public, due_allowed, due_hidden, completed_allowed):
        activity_repository.save_activity(activity)
    assert tuple(activity.id for activity in activity_repository.list_due_activities(user, NOW)) == (
        due_public.id,
        due_allowed.id,
    )
    assert activity_repository.list_case_activities(hidden_case.id, user) == ()
    assert activity_repository.get_activity(due_hidden.id, user) is None
    assert activity_repository.get_activity(due_allowed.id, user) == due_allowed

    artifact_repository = PostgresArtifactRepository(postgres_db.dsn)
    public_artifact = _artifact(case_id=public_case.id, uploaded_by=actor_id, file_name="public.msg")
    allowed_artifact = _artifact(case_id=allowed_case.id, uploaded_by=actor_id, file_name="allowed.msg")
    hidden_artifact = _artifact(case_id=hidden_case.id, uploaded_by=actor_id, file_name="hidden.msg")
    own_unassigned = _artifact(case_id=None, uploaded_by=actor_id, file_name="own.msg")
    other_unassigned = _artifact(case_id=None, uploaded_by=other_user_id, file_name="other.msg")
    for artifact in (
        public_artifact,
        allowed_artifact,
        hidden_artifact,
        own_unassigned,
        other_unassigned,
    ):
        artifact_repository.save_artifact(artifact)
    assert artifact_repository.get_artifact(hidden_artifact.id, user) is None
    assert artifact_repository.get_artifact(allowed_artifact.id, user) == allowed_artifact
    assert tuple(
        artifact.id for artifact in artifact_repository.list_case_artifacts(hidden_case.id, user)
    ) == ()
    assert tuple(
        artifact.id
        for artifact in artifact_repository.list_unassigned_artifacts(user, limit=10)
    ) == (own_unassigned.id,)


def test_artifact_mail_audit_and_mailbox_repositories_round_trip(
    postgres_db: PostgresTestDatabase,
) -> None:
    """Round-trip JSON, numeric, source metadata, mailbox and audit values."""
    postgres_db.apply_schema()
    user_id = uuid4()
    visible_group_id = uuid4()
    hidden_group_id = uuid4()
    postgres_db.rows(
        "INSERT INTO app_user (id, email, display_name) VALUES (%s, %s, %s) RETURNING id",
        (user_id, "mail@example.com", "Mail User"),
    )
    postgres_db.rows(
        "INSERT INTO app_group (id, name) VALUES (%s, %s), (%s, %s) RETURNING id",
        (visible_group_id, "Visible", hidden_group_id, "Hidden"),
    )
    visible_user = UserContext(
        id=user_id,
        email="mail@example.com",
        display_name="Mail User",
        visible_group_ids=frozenset({visible_group_id}),
    )
    case = CaseFile(
        id=uuid4(),
        title="Mail case",
        company="Mail GmbH",
        primary_contact="mail@example.com",
        status="open",
        last_activity_at=NOW,
        visible_group_id=visible_group_id,
    )
    hidden_case = CaseFile(
        id=uuid4(),
        title="Hidden mail case",
        company=None,
        primary_contact=None,
        status="open",
        last_activity_at=NOW,
        visible_group_id=hidden_group_id,
    )
    case_repository = PostgresCaseRepository(postgres_db.dsn)
    case_repository.save_case(case)
    case_repository.save_case(hidden_case)
    artifact = _artifact(case_id=case.id, uploaded_by=user_id, file_name="mail.msg")
    hidden_artifact = _artifact(case_id=hidden_case.id, uploaded_by=user_id, file_name="hidden.msg")
    artifact_repository = PostgresArtifactRepository(postgres_db.dsn)
    artifact_repository.save_artifact(artifact)
    artifact_repository.save_artifact(hidden_artifact)

    suggestion = AssignmentSuggestion(
        artifact_id=artifact.id,
        suggested_case_id=case.id,
        summary_reason="Sender and company match",
        confidence=0.875,
        created_at=NOW,
    )
    metadata = ArtifactMailMetadata(
        artifact_id=artifact.id,
        source_system="outlook_upload",
        message_format="outlook_msg",
        parse_status="parsed",
        external_message_id="external-1",
        rfc_message_id="<mail-1@example.com>",
        source_account="mail@example.com",
        source_mailbox="Inbox",
        subject="A mail subject",
        sender_name="Sender",
        sender_email="sender@example.com",
        sender_domain="example.com",
        recipients=(MailParticipant(name="Recipient", email="recipient@example.com"),),
        sent_at=NOW - timedelta(hours=1),
        created_at=NOW,
    )
    conversation = MailConversation(
        id=uuid4(),
        source_kind="outlook",
        external_conversation_id="conversation-1",
        normalized_subject="a mail subject",
        latest_subject="A mail subject",
        latest_message_at=NOW,
        participants=metadata.recipients,
        message_count=1,
        latest_artifact_id=artifact.id,
        created_at=NOW,
        updated_at=NOW,
        assigned_case_id=case.id,
    )
    message = MailMessage(
        artifact_id=artifact.id,
        conversation_id=conversation.id,
        source_kind="outlook",
        source_account_id="account-1",
        source_folder_id="Inbox",
        source_message_id="message-1",
        source_conversation_id="conversation-1",
        internet_message_id="<mail-1@example.com>",
        dedupe_fingerprint="fingerprint-1",
        direction="inbound",
        received_at=NOW,
        created_at=NOW,
    )
    artifact_repository.save_suggestion(suggestion)
    artifact_repository.save_mail_metadata(metadata)
    artifact_repository.save_mail_conversation(conversation)
    artifact_repository.save_mail_message(message)
    assert artifact_repository.get_suggestion(artifact.id, visible_user) == suggestion
    assert artifact_repository.get_mail_metadata(artifact.id, visible_user) == metadata
    assert artifact_repository.get_mail_conversation(conversation.id, visible_user) == conversation
    assert artifact_repository.get_mail_message(artifact.id, visible_user) == message
    assert (
        artifact_repository.find_mail_message_by_source(
            source_kind="outlook",
            source_account_id="account-1",
            source_folder_id="Inbox",
            source_message_id="message-1",
            internet_message_id="<mail-1@example.com>",
            dedupe_fingerprint="not-the-first-match",
            user=visible_user,
        )
        == message
    )
    assert artifact_repository.list_conversation_artifacts(conversation.id, visible_user) == (artifact,)
    assert artifact_repository.list_recent_mail_conversations(visible_user, limit=10) == (conversation,)

    hidden_user = UserContext(
        id=user_id,
        email="mail@example.com",
        display_name="Mail User",
        visible_group_ids=frozenset(),
    )
    assert artifact_repository.get_mail_conversation(conversation.id, hidden_user) is None
    assert artifact_repository.get_mail_message(artifact.id, hidden_user) is None
    assert artifact_repository.list_recent_mail_conversations(hidden_user) == ()

    account = MailboxAccountConfig(
        id=uuid4(),
        user_id=user_id,
        source_kind="outlook",
        account_key="account-1",
        outlook_store_name="Mailbox",
        inbox_folder_key="Inbox",
        sent_folder_key="Sent",
        polling_interval_seconds=60,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    checkpoint = MailboxSyncCheckpoint(
        account_config_id=account.id,
        folder_key="Inbox",
        last_message_key="message-1",
        last_message_at=NOW,
        updated_at=NOW,
    )
    artifact_repository.save_mailbox_account_config(account)
    artifact_repository.save_mailbox_sync_checkpoint(checkpoint)
    assert artifact_repository.get_active_mailbox_account_config(visible_user) == account
    assert artifact_repository.list_mailbox_sync_checkpoints(account.id) == (checkpoint,)

    event = AuditEvent(
        id=uuid4(),
        actor_user_id=user_id,
        event_type="artifact.assigned",
        subject_id=artifact.id,
        payload_json={"case_id": str(case.id), "confidence": 0.875},
        created_at=NOW,
    )
    PostgresAuditRepository(postgres_db.dsn).save_event(event)
    assert postgres_db.rows(
        "SELECT actor_user_id, event_type, subject_id, payload_json FROM audit_event"
    ) == [(user_id, "artifact.assigned", artifact.id, event.payload_json)]


def test_failed_foreign_key_writes_roll_back_without_partial_state(
    postgres_db: PostgresTestDatabase,
) -> None:
    """A rejected repository write leaves PostgreSQL usable and unchanged."""
    postgres_db.apply_schema()
    user_id = uuid4()
    postgres_db.rows(
        "INSERT INTO app_user (id, email, display_name) VALUES (%s, %s, %s) RETURNING id",
        (user_id, "rollback@example.com", "Rollback User"),
    )
    activity_repository = PostgresActivityRepository(postgres_db.dsn)
    invalid_activity = Activity(
        id=uuid4(),
        case_id=uuid4(),
        description="This must not be stored",
        kind="follow_up",
        due_at=NOW,
        created_at=NOW,
        created_by=user_id,
    )
    with pytest.raises(ForeignKeyViolation):
        activity_repository.save_activity(invalid_activity)
    assert postgres_db.scalar("SELECT count(*) FROM activity") == 0

    case = CaseFile(
        id=uuid4(),
        title="Rollback case",
        company=None,
        primary_contact=None,
        status="open",
        last_activity_at=NOW,
    )
    PostgresCaseRepository(postgres_db.dsn).save_case(case)
    valid_activity = Activity(
        id=uuid4(),
        case_id=case.id,
        description="The next valid write still works",
        kind="follow_up",
        due_at=NOW,
        created_at=NOW,
        created_by=user_id,
    )
    activity_repository.save_activity(valid_activity)
    assert postgres_db.scalar("SELECT count(*) FROM activity") == 1


def _artifact(*, case_id: UUID | None, uploaded_by: UUID | None, file_name: str) -> Artifact:
    return Artifact(
        id=uuid4(),
        file_name=file_name,
        media_type="message/rfc822",
        size_bytes=128,
        content_text="A mail body",
        storage_key=f"artifacts/{uuid4()}",
        uploaded_at=NOW,
        uploaded_by=uploaded_by,
        assigned_case_id=case_id,
    )


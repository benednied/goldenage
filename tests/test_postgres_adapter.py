from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from goldenage.adapters import postgres
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AuditEvent,
    CaseFile,
    MailboxAccountConfig,
    MailboxSyncCheckpoint,
    MailConversation,
    MailMessage,
    MailParticipant,
    UserContext,
)

NOW = datetime(2026, 4, 12, 9, 30, tzinfo=UTC)


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_rows: list[dict[str, object] | None] | None = None,
        fetchall_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.fetchone_rows = fetchone_rows or []
        self.fetchall_rows = fetchall_rows or []
        self.executed: list[tuple[str, dict[str, object] | None]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> dict[str, object] | None:
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self) -> list[dict[str, object]]:
        return self.fetchall_rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def test_postgres_repository_requires_psycopg(monkeypatch) -> None:
    monkeypatch.setattr(postgres, "psycopg", None)

    with pytest.raises(postgres.PostgresRepositoryError, match="psycopg is not installed"):
        postgres.PostgresCaseRepository("postgresql://example")


def test_postgres_connect_delegates_to_psycopg(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        postgres,
        "psycopg",
        SimpleNamespace(
            connect=lambda dsn, row_factory: calls.append((dsn, row_factory)) or "conn"
        ),
    )

    assert postgres.PostgresCaseRepository("postgresql://example")._connect() == "conn"
    assert calls == [("postgresql://example", postgres.dict_row)]


def test_case_visible_handles_missing_public_and_group_visible_rows(monkeypatch) -> None:
    group_id = uuid4()
    cursor = FakeCursor(
        fetchone_rows=[
            None,
            {"visible_group_id": None},
            {"visible_group_id": group_id},
            {"visible_group_id": uuid4()},
        ]
    )
    connection = FakeConnection(cursor)
    repo = postgres.PostgresCaseRepository("postgresql://example")
    monkeypatch.setattr(repo, "_connect", lambda: connection)
    user = UserContext(
        id=uuid4(),
        email="user.fixture@example.test",
        display_name="Fixture User",
        visible_group_ids=frozenset({group_id}),
    )

    assert repo._case_visible(uuid4(), user) is False
    assert repo._case_visible(uuid4(), user) is True
    assert repo._case_visible(uuid4(), user) is True
    assert repo._case_visible(uuid4(), user) is False


def test_case_repository_maps_reads_and_commits_writes(monkeypatch) -> None:
    case_id = uuid4()
    user = UserContext(id=uuid4(), email="user.fixture@example.test", display_name="Fixture User")
    row = _case_row(case_id)
    cursor = FakeCursor(fetchone_rows=[row], fetchall_rows=[row])
    connection = FakeConnection(cursor)
    repo = postgres.PostgresCaseRepository("postgresql://example")
    monkeypatch.setattr(repo, "_connect", lambda: connection)

    assert repo.list_cases(user) == (CaseFile(**row),)  # ty:ignore[invalid-argument-type]
    assert repo.get_case(case_id, user) == CaseFile(**row)  # ty:ignore[invalid-argument-type]
    repo.save_case(CaseFile(**row))  # ty:ignore[invalid-argument-type]

    assert connection.commits == 1
    assert cursor.executed[-1][1] == row


def test_activity_repository_visibility_and_completion_branches(monkeypatch) -> None:
    activity_id = uuid4()
    group_id = uuid4()
    user = UserContext(
        id=uuid4(),
        email="user.fixture@example.test",
        display_name="Fixture User",
        visible_group_ids=frozenset({group_id}),
    )
    visible_row = _activity_row(activity_id, visible_group_id=group_id)
    hidden_row = _activity_row(uuid4(), visible_group_id=uuid4())
    cursor = FakeCursor(
        fetchone_rows=[hidden_row, visible_row],
        fetchall_rows=[_activity_row(uuid4())],
    )
    connection = FakeConnection(cursor)
    repo = postgres.PostgresActivityRepository("postgresql://example")
    monkeypatch.setattr(repo, "_connect", lambda: connection)
    monkeypatch.setattr(repo, "_case_visible", lambda case_id, user: False)

    assert repo.list_due_activities(user, NOW)
    assert repo.list_case_activities(uuid4(), user) == ()
    assert repo.get_activity(activity_id, user) is None
    assert repo.get_activity(activity_id, user) == postgres._row_to_activity(visible_row)

    cursor.fetchall_rows = [visible_row]
    monkeypatch.setattr(repo, "_case_visible", lambda case_id, user: True)
    assert repo.list_case_activities(uuid4(), user) == (postgres._row_to_activity(visible_row),)
    cursor.fetchone_rows = [None]
    assert repo.get_activity(activity_id, user) is None
    repo.save_activity(postgres._row_to_activity(visible_row))
    assert connection.commits == 1


def test_artifact_repository_maps_mail_and_visibility_branches(monkeypatch) -> None:
    group_id = uuid4()
    artifact_id = uuid4()
    conversation_id = uuid4()
    user = UserContext(
        id=uuid4(),
        email="user.fixture@example.test",
        display_name="Fixture User",
        visible_group_ids=frozenset({group_id}),
    )
    hidden_artifact = _artifact_row(artifact_id, visible_group_id=uuid4())
    visible_artifact = _artifact_row(artifact_id, visible_group_id=group_id)
    listed_artifact = _artifact_row(artifact_id)
    suggestion = _suggestion_row(artifact_id)
    metadata = _mail_metadata_row(artifact_id)
    conversation = _mail_conversation_row(conversation_id, artifact_id)
    message = _mail_message_row(artifact_id, conversation_id)
    cursor = FakeCursor(
        fetchone_rows=[
            hidden_artifact,
            visible_artifact,
            visible_artifact,
            suggestion,
            visible_artifact,
            metadata,
            conversation,
            visible_artifact,
            message,
        ],
        fetchall_rows=[listed_artifact],
    )
    connection = FakeConnection(cursor)
    repo = postgres.PostgresArtifactRepository("postgresql://example")
    monkeypatch.setattr(repo, "_connect", lambda: connection)
    monkeypatch.setattr(repo, "_case_visible", lambda case_id, user: True)

    assert repo.get_artifact(artifact_id, user) is None
    assert repo.get_artifact(artifact_id, user) == postgres._row_to_artifact(visible_artifact)
    assert repo.list_case_artifacts(uuid4(), user) == (postgres._row_to_artifact(listed_artifact),)
    assert repo.get_suggestion(artifact_id, user) == postgres._row_to_suggestion(suggestion)
    assert repo.get_mail_metadata(artifact_id, user) == postgres._row_to_mail_metadata(metadata)
    assert repo.get_mail_conversation(conversation_id, user) == postgres._row_to_mail_conversation(
        conversation
    )
    assert repo.get_mail_message(artifact_id, user) == postgres._row_to_mail_message(message)


def test_artifact_repository_hidden_and_missing_early_returns(monkeypatch) -> None:
    user = UserContext(id=uuid4(), email="user.fixture@example.test", display_name="Fixture User")
    cursor = FakeCursor(fetchone_rows=[None, None, None, None])
    connection = FakeConnection(cursor)
    repo = postgres.PostgresArtifactRepository("postgresql://example")
    monkeypatch.setattr(repo, "_connect", lambda: connection)
    monkeypatch.setattr(repo, "_case_visible", lambda case_id, user: False)

    assert repo.get_artifact(uuid4(), user) is None
    assert repo.list_case_artifacts(uuid4(), user) == ()
    assert repo.get_suggestion(uuid4(), user) is None
    assert repo.get_mail_metadata(uuid4(), user) is None
    assert repo.get_mail_message(uuid4(), user) is None


def test_postgres_row_mappers_cover_json_and_numeric_conversions() -> None:
    artifact_id = uuid4()
    conversation_id = uuid4()
    account_id = uuid4()

    assert postgres._row_to_mail_metadata(_mail_metadata_row(artifact_id)).recipients == (
        MailParticipant(name="Fixture User", email="user.fixture@example.test"),
    )
    assert (
        postgres._row_to_mail_conversation(
            _mail_conversation_row(conversation_id, artifact_id)
        ).message_count
        == 2
    )
    assert (
        postgres._row_to_mail_message(
            _mail_message_row(artifact_id, conversation_id)
        ).conversation_id
        == conversation_id
    )
    assert postgres._row_to_mailbox_account_config(_mailbox_account_row(account_id)).active is True
    assert (
        postgres._row_to_mailbox_sync_checkpoint(_mailbox_checkpoint_row(account_id)).folder_key
        == "Inbox"
    )


def test_save_methods_commit_and_wrap_json_payloads(monkeypatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    monkeypatch.setattr(postgres, "Jsonb", lambda value: SimpleNamespace(value=value))
    audit_repo = postgres.PostgresAuditRepository("postgresql://example")
    monkeypatch.setattr(audit_repo, "_connect", lambda: connection)

    audit_repo.save_event(
        AuditEvent(
            id=uuid4(),
            actor_user_id=uuid4(),
            event_type="case.updated",
            subject_id=uuid4(),
            payload_json={"ok": True},
            created_at=NOW,
        )
    )

    assert connection.commits == 1
    assert cursor.executed[0][1]["payload_json"].value == {"ok": True}  # ty:ignore[not-subscriptable, unresolved-attribute]


def test_artifact_repository_save_find_and_mailbox_methods(monkeypatch) -> None:
    artifact_id = uuid4()
    conversation_id = uuid4()
    account_id = uuid4()
    user = UserContext(id=uuid4(), email="user.fixture@example.test", display_name="Fixture User")
    artifact_row = _artifact_row(artifact_id)
    mail_message_row = _mail_message_row(artifact_id, conversation_id)
    mailbox_account_row = _mailbox_account_row(account_id)
    cursor = FakeCursor(
        fetchone_rows=[
            mail_message_row,
            mailbox_account_row,
        ],
        fetchall_rows=[artifact_row],
    )
    connection = FakeConnection(cursor)
    monkeypatch.setattr(postgres, "Jsonb", lambda value: SimpleNamespace(value=value))
    repo = postgres.PostgresArtifactRepository("postgresql://example")
    monkeypatch.setattr(repo, "_connect", lambda: connection)

    artifact = postgres._row_to_artifact(artifact_row)
    repo.save_artifact(artifact)
    assert repo.list_unassigned_artifacts(user, limit=5) == (artifact,)
    repo.save_suggestion(postgres._row_to_suggestion(_suggestion_row(artifact_id)))
    repo.save_mail_metadata(postgres._row_to_mail_metadata(_mail_metadata_row(artifact_id)))
    repo.save_mail_conversation(
        postgres._row_to_mail_conversation(_mail_conversation_row(conversation_id, artifact_id))
    )
    repo.save_mail_message(
        postgres._row_to_mail_message(_mail_message_row(artifact_id, conversation_id))
    )
    assert repo.find_mail_message_by_source(
        source_kind="outlook_msg",
        source_account_id="account-1",
        source_folder_id="Inbox",
        source_message_id="message-1",
        internet_message_id="<message-1@example.com>",
        dedupe_fingerprint="fingerprint",
        user=user,
    ) == postgres._row_to_mail_message(mail_message_row)
    repo.list_conversation_artifacts(conversation_id, user)
    repo.save_mailbox_account_config(postgres._row_to_mailbox_account_config(mailbox_account_row))
    assert repo.get_active_mailbox_account_config(user) == postgres._row_to_mailbox_account_config(
        mailbox_account_row
    )
    repo.save_mailbox_sync_checkpoint(
        postgres._row_to_mailbox_sync_checkpoint(_mailbox_checkpoint_row(account_id))
    )
    cursor.fetchall_rows = [_mailbox_checkpoint_row(account_id)]
    repo.list_mailbox_sync_checkpoints(account_id)
    cursor.fetchall_rows = [_mail_conversation_row(conversation_id, artifact_id)]
    assert repo.list_recent_mail_conversations(user, limit=1) == (
        postgres._row_to_mail_conversation(_mail_conversation_row(conversation_id, artifact_id)),
    )

    assert connection.commits == 7
    payloads = [params for sql, params in cursor.executed if params]
    assert any(isinstance(params.get("recipients_json"), SimpleNamespace) for params in payloads)
    assert any(isinstance(params.get("participants_json"), SimpleNamespace) for params in payloads)


def test_mapper_return_types_are_domain_models() -> None:
    artifact_id = uuid4()
    conversation_id = uuid4()
    account_id = uuid4()

    assert isinstance(postgres._row_to_activity(_activity_row(uuid4())), Activity)
    assert isinstance(postgres._row_to_artifact(_artifact_row(artifact_id)), Artifact)
    assert isinstance(
        postgres._row_to_mail_metadata(_mail_metadata_row(artifact_id)), ArtifactMailMetadata
    )
    assert isinstance(
        postgres._row_to_mail_conversation(_mail_conversation_row(conversation_id, artifact_id)),
        MailConversation,
    )
    assert isinstance(
        postgres._row_to_mail_message(_mail_message_row(artifact_id, conversation_id)),
        MailMessage,
    )
    assert isinstance(
        postgres._row_to_mailbox_account_config(_mailbox_account_row(account_id)),
        MailboxAccountConfig,
    )
    assert isinstance(
        postgres._row_to_mailbox_sync_checkpoint(_mailbox_checkpoint_row(account_id)),
        MailboxSyncCheckpoint,
    )


def _case_row(case_id: UUID) -> dict[str, object]:
    return {
        "id": case_id,
        "title": "Renewal",
        "company": "Acme",
        "primary_contact": "Fixture User",
        "status": "open",
        "last_activity_at": NOW,
        "visible_group_id": None,
    }


def _activity_row(activity_id: UUID, *, visible_group_id: UUID | None = None) -> dict[str, object]:
    return {
        "id": activity_id,
        "case_id": uuid4(),
        "description": "Review mail",
        "kind": "follow_up",
        "due_at": NOW,
        "created_at": NOW,
        "created_by": None,
        "completed_at": None,
        "visible_group_id": visible_group_id,
    }


def _artifact_row(
    artifact_id: UUID,
    *,
    visible_group_id: UUID | None = None,
) -> dict[str, object]:
    return {
        "id": artifact_id,
        "file_name": "mail.msg",
        "media_type": "application/vnd.ms-outlook",
        "size_bytes": 42,
        "content_text": "Body",
        "storage_key": "artifact/mail.msg",
        "uploaded_at": NOW,
        "uploaded_by": None,
        "assigned_case_id": uuid4(),
        "visible_group_id": visible_group_id,
    }


def _suggestion_row(artifact_id: UUID) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "suggested_case_id": uuid4(),
        "summary_reason": "Subject matches",
        "confidence": "0.75",
        "created_at": NOW,
    }


def _mail_metadata_row(artifact_id: UUID) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "source_system": "outlook_upload",
        "message_format": "outlook_msg",
        "parse_status": "parsed",
        "external_message_id": "message-1",
        "rfc_message_id": "<message-1@example.com>",
        "source_account": "Mailbox",
        "source_mailbox": "Inbox",
        "subject": "Renewal",
        "sender_name": "Sender",
        "sender_email": "sender.fixture@example.test",
        "sender_domain": "example.com",
        "recipients_json": [{"name": "Fixture User", "email": "user.fixture@example.test"}],
        "sent_at": NOW,
        "created_at": NOW,
    }


def _mail_conversation_row(conversation_id: UUID, artifact_id: UUID) -> dict[str, object]:
    return {
        "id": conversation_id,
        "source_kind": "outlook_msg",
        "external_conversation_id": "conv-1",
        "normalized_subject": "renewal",
        "latest_subject": "RE: Renewal",
        "latest_message_at": NOW,
        "participants_json": [{"name": "Fixture User", "email": "user.fixture@example.test"}],
        "message_count": "2",
        "latest_artifact_id": artifact_id,
        "created_at": NOW,
        "updated_at": NOW,
        "assigned_case_id": None,
    }


def _mail_message_row(artifact_id: UUID, conversation_id: UUID) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "conversation_id": conversation_id,
        "source_kind": "outlook_msg",
        "source_account_id": "account-1",
        "source_folder_id": "Inbox",
        "source_message_id": "message-1",
        "source_conversation_id": "conv-1",
        "internet_message_id": "<message-1@example.com>",
        "dedupe_fingerprint": "fingerprint",
        "direction": "inbound",
        "received_at": NOW,
        "created_at": NOW,
    }


def _mailbox_account_row(account_id: UUID) -> dict[str, object]:
    return {
        "id": account_id,
        "user_id": uuid4(),
        "source_kind": "outlook_mailbox",
        "account_key": "Mailbox",
        "outlook_store_name": "Mailbox",
        "inbox_folder_key": "Inbox",
        "sent_folder_key": "Sent",
        "polling_interval_seconds": "60",
        "active": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _mailbox_checkpoint_row(account_id: UUID) -> dict[str, object]:
    return {
        "account_config_id": account_id,
        "folder_key": "Inbox",
        "last_message_key": "message-1",
        "last_message_at": NOW,
        "updated_at": NOW,
    }

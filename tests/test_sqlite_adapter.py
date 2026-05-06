from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goldenage.adapters.sqlite import (
    SQLiteActivityRepository,
    SQLiteArtifactRepository,
    SQLiteAuditRepository,
    SQLiteCaseRepository,
    SQLiteLocalUserRepository,
    SQLiteMailImportRepository,
    SQLiteRepositoryError,
    _deserialize_datetime,
    _group_visibility_clause,
    _serialize_datetime,
)
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    MailCandidate,
    MailConversation,
    MailMessage,
    MailParticipant,
    MailSelector,
    UserContext,
)

NOW = datetime(2026, 4, 12, 9, 30, tzinfo=UTC)


def test_sqlite_repositories_cover_visibility_mail_and_local_user_paths(tmp_path) -> None:
    database_path = tmp_path / "goldenage.sqlite3"
    ensure_sqlite_bootstrapped(database_path)
    user = UserContext(id=uuid4(), email="alex@example.com", display_name="Alex")
    hidden_user = UserContext(
        id=uuid4(),
        email="hidden@example.com",
        display_name="Hidden",
        visible_group_ids=frozenset(),
    )
    group_id = uuid4()
    visible_user = UserContext(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        visible_group_ids=frozenset({group_id}),
    )
    case_id = uuid4()
    hidden_case_id = uuid4()
    activity_id = uuid4()
    artifact_id = uuid4()
    hidden_artifact_id = uuid4()
    conversation_id = uuid4()

    case_repo = SQLiteCaseRepository(database_path)
    activity_repo = SQLiteActivityRepository(database_path)
    artifact_repo = SQLiteArtifactRepository(database_path)
    audit_repo = SQLiteAuditRepository(database_path)
    user_repo = SQLiteLocalUserRepository(database_path)
    mail_repo = SQLiteMailImportRepository(database_path)
    with case_repo._connect() as connection:
        connection.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (str(user.id), user.email, user.display_name, "hash"),
        )
        connection.execute(
            "INSERT INTO app_group (id, name) VALUES (?, ?)",
            (str(group_id), "Restricted"),
        )
        connection.commit()

    case_file = CaseFile(
        id=case_id,
        title="Acme renewal",
        company="Acme",
        primary_contact="Max",
        status="open",
        last_activity_at=NOW,
    )
    hidden_case = CaseFile(
        id=hidden_case_id,
        title="Hidden renewal",
        company=None,
        primary_contact=None,
        status="open",
        last_activity_at=NOW,
        visible_group_id=group_id,
    )
    case_repo.save_case(case_file)
    case_repo.save_case(hidden_case)
    activity = Activity(
        id=activity_id,
        case_id=case_id,
        description="Call Max",
        kind="follow_up",
        due_at=NOW,
        created_at=NOW,
        created_by=user.id,
    )
    activity_repo.save_activity(activity)
    artifact = Artifact(
        id=artifact_id,
        file_name="mail.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=4,
        content_text="Body",
        storage_key="stored",
        uploaded_at=NOW,
        uploaded_by=user.id,
        assigned_case_id=case_id,
    )
    hidden_artifact = Artifact(
        id=hidden_artifact_id,
        file_name="hidden.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=4,
        content_text="Hidden",
        storage_key="hidden",
        uploaded_at=NOW,
        uploaded_by=user.id,
        assigned_case_id=hidden_case_id,
    )
    artifact_repo.save_artifact(artifact)
    artifact_repo.save_artifact(hidden_artifact)
    suggestion = AssignmentSuggestion(
        artifact_id=artifact_id,
        suggested_case_id=case_id,
        summary_reason="match",
        confidence=0.9,
        created_at=NOW,
    )
    artifact_repo.save_suggestion(suggestion)
    metadata = ArtifactMailMetadata(
        artifact_id=artifact_id,
        source_system="outlook_upload",
        message_format="outlook_msg",
        parse_status="parsed",
        external_message_id="external",
        rfc_message_id="<external@example.com>",
        source_account="Mailbox",
        source_mailbox="Inbox",
        subject="Acme renewal",
        sender_name="Max",
        sender_email="max@acme.example",
        sender_domain="acme.example",
        recipients=(MailParticipant(name="Alex", email="alex@example.com"),),
        sent_at=NOW,
        created_at=NOW,
    )
    artifact_repo.save_mail_metadata(metadata)
    conversation = MailConversation(
        id=conversation_id,
        source_kind="outlook_upload",
        external_conversation_id="conv",
        normalized_subject="acme renewal",
        latest_subject="Acme renewal",
        latest_message_at=NOW,
        participants=(MailParticipant(name="Max", email="max@acme.example"),),
        message_count=1,
        latest_artifact_id=artifact_id,
        created_at=NOW,
        updated_at=NOW,
    )
    message = MailMessage(
        artifact_id=artifact_id,
        conversation_id=conversation_id,
        source_kind="outlook_upload",
        source_account_id="Mailbox",
        source_folder_id="Inbox",
        source_message_id="message",
        source_conversation_id="conv",
        internet_message_id="<external@example.com>",
        dedupe_fingerprint="fingerprint",
        direction="inbound",
        received_at=NOW,
        created_at=NOW,
    )
    artifact_repo.save_mail_conversation(conversation)
    artifact_repo.save_mail_message(message)
    audit_repo.save_event(
        AuditEvent(
            id=uuid4(),
            actor_user_id=user.id,
            event_type="test",
            subject_id=artifact_id,
            payload_json={"ok": True},
            created_at=NOW,
        )
    )

    assert case_repo.list_cases(user) == (case_file,)
    assert case_repo.get_case(hidden_case_id, hidden_user) is None
    assert case_repo.get_case(hidden_case_id, visible_user) == hidden_case
    assert activity_repo.list_due_activities(user, NOW) == (activity,)
    assert activity_repo.list_case_activities(hidden_case_id, hidden_user) == ()
    assert activity_repo.get_activity(activity_id, user) == activity
    assert artifact_repo.get_artifact(hidden_artifact_id, hidden_user) is None
    assert artifact_repo.list_case_artifacts(hidden_case_id, hidden_user) == ()
    assert artifact_repo.list_unassigned_artifacts(user, limit=10) == ()
    assert artifact_repo.get_suggestion(artifact_id, user) == suggestion
    missing_artifact_id = uuid4()
    assert artifact_repo.get_suggestion(missing_artifact_id, user) is None
    assert artifact_repo.get_mail_metadata(artifact_id, user) == metadata
    assert artifact_repo.get_mail_metadata(missing_artifact_id, user) is None
    assert artifact_repo.get_mail_conversation(conversation_id, user) == conversation
    assert artifact_repo.list_recent_mail_conversations(user, limit=5) == (conversation,)
    assert artifact_repo.get_mail_message(artifact_id, user) == message
    assert artifact_repo.get_mail_message(missing_artifact_id, user) is None
    assert (
        artifact_repo.find_mail_message_by_source(
            source_kind="outlook_upload",
            source_account_id="Mailbox",
            source_folder_id="Inbox",
            source_message_id="message",
            internet_message_id=None,
            dedupe_fingerprint="other",
            user=user,
        )
        == message
    )
    assert (
        artifact_repo.find_mail_message_by_source(
            source_kind="outlook_upload",
            source_account_id=None,
            source_folder_id=None,
            source_message_id=None,
            internet_message_id="<external@example.com>",
            dedupe_fingerprint="other",
            user=user,
        )
        == message
    )
    assert (
        artifact_repo.find_mail_message_by_source(
            source_kind="outlook_upload",
            source_account_id=None,
            source_folder_id=None,
            source_message_id=None,
            internet_message_id=None,
            dedupe_fingerprint="fingerprint",
            user=user,
        )
        == message
    )
    assert artifact_repo.list_conversation_artifacts(conversation_id, user) == (artifact,)

    account = user_repo.get_first_user()
    assert user_repo.get_first_user() == account
    assert user_repo.get_user_by_email(" ALEX@example.com ") == account
    with pytest.raises(SQLiteRepositoryError, match="already exists"):
        user_repo.create_user(
            account_id=uuid4(),
            email="other@example.com",
            display_name="Other",
            password_hash="hash",
            profile_image_path=None,
        )
    user_repo.update_password_hash(account_id=user.id, password_hash="new-hash")
    assert user_repo.get_first_user().password_hash == "new-hash"  # ty:ignore[unresolved-attribute]
    with pytest.raises(SQLiteRepositoryError, match="not found"):
        user_repo.update_password_hash(account_id=uuid4(), password_hash="missing")

    selector = MailSelector(
        account_name="Mailbox",
        mailbox_name="Inbox",
        unread_only=True,
        sender_filter="Max",
        subject_filter="Acme",
        sent_after=NOW,
        result_limit=7,
    )
    candidate = MailCandidate(
        candidate_id="candidate-1",
        source_system="desktop_mail_client",
        account_name="Mailbox",
        mailbox_name="Inbox",
        subject="Acme renewal",
        sender_name="Max",
        sender_email="max@acme.example",
        sent_at=NOW,
        preview_text="Preview",
        unread=True,
        rfc_message_id="<candidate-1@example.com>",
    )
    mail_repo.upsert_source(user=user, source_system="desktop_mail_client", now=NOW)
    mail_repo.save_selector(
        user=user,
        source_system="desktop_mail_client",
        selector=selector,
        now=NOW,
    )
    mail_repo.replace_review_candidates(
        user=user,
        source_system="desktop_mail_client",
        candidates=(candidate,),
        now=NOW,
    )
    mail_repo.save_imported_message(
        user=user,
        source_system="desktop_mail_client",
        external_message_id="candidate-1",
        rfc_message_id="<candidate-1@example.com>",
        artifact_id=artifact_id,
        now=NOW,
    )
    assert mail_repo.get_selector(user=user, source_system="desktop_mail_client") == selector
    assert mail_repo.list_review_candidates(user=user, source_system="desktop_mail_client") == (
        candidate,
    )
    assert (
        mail_repo.get_review_candidate(
            user=user,
            source_system="desktop_mail_client",
            candidate_id="candidate-1",
        )
        == candidate
    )
    assert mail_repo.list_imported_message_ids(
        user=user,
        source_system="desktop_mail_client",
    ) == frozenset({"candidate-1", "<candidate-1@example.com>"})
    mail_repo.discard_review_candidate(
        user=user,
        source_system="desktop_mail_client",
        candidate_id="candidate-1",
    )
    assert mail_repo.list_review_candidates(user=user, source_system="desktop_mail_client") == ()


def test_sqlite_helpers_cover_group_clause_and_datetime_round_trip() -> None:
    group_id = uuid4()
    assert _group_visibility_clause(frozenset(), "visible_group_id") == (
        "visible_group_id IS NULL",
        (),
    )
    assert _group_visibility_clause(frozenset({group_id}), "visible_group_id") == (
        "(visible_group_id IS NULL OR visible_group_id IN (?))",
        (str(group_id),),
    )
    assert _serialize_datetime(None) is None
    assert _deserialize_datetime(None) is None
    assert _deserialize_datetime(_serialize_datetime(NOW)) == NOW

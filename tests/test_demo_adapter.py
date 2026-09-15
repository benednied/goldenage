from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from goldenage.adapters import demo
from goldenage.domain.models import (
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    MailboxAccountConfig,
    MailboxSyncCheckpoint,
    MailCandidate,
    MailConversation,
    MailMessage,
    MailParticipant,
    MailSelector,
    UserContext,
)

NOW = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)


def test_in_memory_repositories_apply_visibility_and_mail_lookup_edges(tmp_path) -> None:
    state, owner = demo.build_demo_state()
    hidden_user = UserContext(
        id=uuid4(),
        email="hidden@example.com",
        display_name="Hidden",
        visible_group_ids=frozenset(),
    )
    group_id = uuid4()
    case_id = next(iter(state.cases))
    state.cases[case_id] = replace(state.cases[case_id], visible_group_id=group_id)
    visible_user = replace(owner, visible_group_ids=frozenset({group_id}))

    case_repo = demo.InMemoryCaseRepository(state)
    activity_repo = demo.InMemoryActivityRepository(state, case_repo)
    artifact_repo = demo.InMemoryArtifactRepository(state, case_repo)
    artifact_id = uuid4()
    conversation_id = uuid4()
    state.artifacts[artifact_id] = Artifact(
        id=artifact_id,
        file_name="mail.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=4,
        content_text="Body",
        storage_key=str(tmp_path / "mail.msg"),
        uploaded_at=NOW,
        uploaded_by=owner.id,
        assigned_case_id=case_id,
    )
    state.suggestions[artifact_id] = AssignmentSuggestion(
        artifact_id=artifact_id,
        suggested_case_id=case_id,
        summary_reason="match",
        confidence=1.0,
        created_at=NOW,
    )
    state.artifact_mail_metadata[artifact_id] = ArtifactMailMetadata(
        artifact_id=artifact_id,
        source_system="outlook_upload",
        message_format="outlook_msg",
        parse_status="parsed",
        external_message_id=None,
        rfc_message_id=None,
        source_account=None,
        source_mailbox=None,
        subject="Acme renewal",
        sender_name="Max",
        sender_email="max@acme.example",
        sender_domain="acme.example",
        recipients=(MailParticipant(name="Alex", email="alex@example.com"),),
        sent_at=NOW,
        created_at=NOW,
    )
    state.mail_conversations[conversation_id] = MailConversation(
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
    state.mail_messages[artifact_id] = MailMessage(
        artifact_id=artifact_id,
        conversation_id=conversation_id,
        source_kind="outlook_upload",
        source_account_id="account",
        source_folder_id="Inbox",
        source_message_id="message",
        source_conversation_id="conv",
        internet_message_id="<message@example.com>",
        dedupe_fingerprint="fingerprint",
        direction="inbound",
        received_at=NOW,
        created_at=NOW,
    )

    assert case_repo.get_case(case_id, hidden_user) is None
    assert activity_repo.list_case_activities(case_id, hidden_user) == ()
    assert activity_repo.get_activity(uuid4(), visible_user) is None
    activity_id = next(iter(state.activities))
    assert activity_repo.get_activity(activity_id, hidden_user) is None
    assert artifact_repo.get_artifact(uuid4(), visible_user) is None
    assert artifact_repo.get_artifact(artifact_id, hidden_user) is None
    assert artifact_repo.list_case_artifacts(case_id, hidden_user) == ()
    assert artifact_repo.get_suggestion(artifact_id, hidden_user) is None
    assert artifact_repo.get_mail_metadata(artifact_id, hidden_user) is None
    assert artifact_repo.get_mail_conversation(uuid4(), visible_user) is None
    assert artifact_repo.get_mail_message(artifact_id, hidden_user) is None

    assert artifact_repo.get_artifact(artifact_id, visible_user) is not None
    assert artifact_repo.get_suggestion(artifact_id, visible_user) is not None
    assert artifact_repo.get_mail_metadata(artifact_id, visible_user) is not None
    assert artifact_repo.get_mail_conversation(conversation_id, visible_user) is not None
    assert artifact_repo.get_mail_message(artifact_id, visible_user) is not None
    assert artifact_repo.list_recent_mail_conversations(visible_user) == (
        state.mail_conversations[conversation_id],
    )
    assert (
        artifact_repo.find_mail_message_by_source(
            source_kind="other",
            source_account_id="account",
            source_folder_id="Inbox",
            source_message_id="message",
            internet_message_id="<message@example.com>",
            dedupe_fingerprint="fingerprint",
            user=visible_user,
        )
        is None
    )
    assert (
        artifact_repo.find_mail_message_by_source(
            source_kind="outlook_upload",
            source_account_id="account",
            source_folder_id="Inbox",
            source_message_id="message",
            internet_message_id=None,
            dedupe_fingerprint="other",
            user=visible_user,
        )
        == state.mail_messages[artifact_id]
    )
    assert artifact_repo.list_conversation_artifacts(conversation_id, visible_user) == (
        state.artifacts[artifact_id],
    )

    unassigned_id = uuid4()
    state.artifacts[unassigned_id] = Artifact(
        id=unassigned_id,
        file_name="unassigned.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=4,
        content_text="Body",
        storage_key="unassigned",
        uploaded_at=NOW,
        uploaded_by=owner.id,
        assigned_case_id=None,
    )
    assert artifact_repo.list_unassigned_artifacts(owner, limit=1) == (
        state.artifacts[unassigned_id],
    )

    account_config = MailboxAccountConfig(
        id=uuid4(),
        user_id=owner.id,
        source_kind="outlook_mailbox_message",
        account_key="Mailbox",
        outlook_store_name="Mailbox",
        inbox_folder_key="Inbox",
        sent_folder_key="Sent",
        polling_interval_seconds=60,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    artifact_repo.save_mailbox_account_config(account_config)
    assert artifact_repo.get_active_mailbox_account_config(owner) == account_config
    assert artifact_repo.get_active_mailbox_account_config(hidden_user) is None
    checkpoint = MailboxSyncCheckpoint(
        account_config_id=account_config.id,
        folder_key="Inbox",
        last_message_key="message",
        last_message_at=NOW,
        updated_at=NOW,
    )
    artifact_repo.save_mailbox_sync_checkpoint(checkpoint)
    assert artifact_repo.list_mailbox_sync_checkpoints(account_config.id) == (checkpoint,)


def test_in_memory_mail_import_repository_tracks_selectors_candidates_and_imported_ids() -> None:
    _, user = demo.build_demo_state()
    repository = demo.InMemoryMailImportRepository()
    candidate = MailCandidate(
        candidate_id="candidate-1",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="Renewal",
        sender_name="Max",
        sender_email="max@example.com",
        sent_at=NOW,
        preview_text="Preview",
        unread=True,
        rfc_message_id="<candidate-1@example.com>",
    )
    selector = MailSelector(mailbox_name="Inbox")

    repository.upsert_source(user=user, source_system="desktop_mail_client", now=NOW)
    repository.save_selector(
        user=user,
        source_system="desktop_mail_client",
        selector=selector,
        now=NOW,
    )
    repository.replace_review_candidates(
        user=user,
        source_system="desktop_mail_client",
        candidates=(candidate,),
        now=NOW,
    )
    repository.save_imported_message(
        user=user,
        source_system="desktop_mail_client",
        external_message_id="candidate-1",
        rfc_message_id="<candidate-1@example.com>",
        artifact_id=uuid4(),
        now=NOW,
    )

    assert repository.get_selector(user=user, source_system="desktop_mail_client") == selector
    assert repository.list_review_candidates(user=user, source_system="desktop_mail_client") == (
        candidate,
    )
    assert (
        repository.get_review_candidate(
            user=user,
            source_system="desktop_mail_client",
            candidate_id="candidate-1",
        )
        == candidate
    )
    assert (
        repository.get_review_candidate(
            user=user,
            source_system="desktop_mail_client",
            candidate_id="missing",
        )
        is None
    )
    assert repository.list_imported_message_ids(
        user=user,
        source_system="desktop_mail_client",
    ) == frozenset({"candidate-1", "<candidate-1@example.com>"})

    repository.discard_review_candidate(
        user=user,
        source_system="desktop_mail_client",
        candidate_id="candidate-1",
    )
    assert repository.list_review_candidates(user=user, source_system="desktop_mail_client") == ()


def test_heuristics_cover_subject_ambiguity_fallback_search_and_text_helpers() -> None:
    state, user = demo.build_demo_state()
    cases = tuple(state.cases.values())
    artifact = Artifact(
        id=uuid4(),
        file_name="fresh.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=4,
        content_text="Northwind compliance questionnaire",
        storage_key="stored",
        uploaded_at=NOW,
        uploaded_by=user.id,
    )

    assert demo.HeuristicGiselaClient().analyze_artifact(artifact, None, cases, NOW).confidence == 0
    assert demo._subject_suggestion_for_cases(None, cases, NOW) is None
    assert (
        demo._subject_suggestion_for_cases(
            ArtifactMailMetadata(
                artifact_id=artifact.id,
                source_system="outlook_upload",
                message_format="outlook_msg",
                parse_status="parsed",
                external_message_id=None,
                rfc_message_id=None,
                source_account=None,
                source_mailbox=None,
                subject="Unrelated note",
                sender_name=None,
                sender_email=None,
                sender_domain=None,
                recipients=(),
                sent_at=None,
                created_at=NOW,
            ),
            cases,
            NOW,
        )
        is None
    )
    assert (
        demo._subject_suggestion_for_cases(
            ArtifactMailMetadata(
                artifact_id=artifact.id,
                source_system="outlook_upload",
                message_format="outlook_msg",
                parse_status="parsed",
                external_message_id=None,
                rfc_message_id=None,
                source_account=None,
                source_mailbox=None,
                subject="Unrelated note",
                sender_name=None,
                sender_email=None,
                sender_domain=None,
                recipients=(),
                sent_at=None,
                created_at=NOW,
            ),
            (replace(cases[0], title=""),),
            NOW,
        )
        is None
    )
    assert (
        demo._subject_suggestion_for_cases(
            ArtifactMailMetadata(
                artifact_id=artifact.id,
                source_system="outlook_upload",
                message_format="outlook_msg",
                parse_status="parsed",
                external_message_id=None,
                rfc_message_id=None,
                source_account=None,
                source_mailbox=None,
                subject="Re:",
                sender_name=None,
                sender_email=None,
                sender_domain=None,
                recipients=(),
                sent_at=None,
                created_at=NOW,
            ),
            cases,
            NOW,
        )
        is None
    )
    assert demo._subject_match_score("", "Acme") == 0
    assert demo._participant_from_address(None) is None
    assert demo._participant_from_address("   ") is None
    assert demo._coerce_optional_str(lambda: " value ") == "value"
    assert demo._coerce_optional_str(None) is None
    assert demo._normalize_email(None) is None
    assert demo._mail_metadata_search_text(None) == ""
    assert demo._rank_cases("Acme renewal", (replace(cases[0], company=None),))
    ambiguous_cases = (
        replace(cases[0], id=uuid4(), title="Acme contract renewal"),
        replace(cases[0], id=uuid4(), title="Acme contract renewals"),
    )
    assert (
        demo._subject_suggestion_for_cases(
            ArtifactMailMetadata(
                artifact_id=artifact.id,
                source_system="outlook_upload",
                message_format="outlook_msg",
                parse_status="parsed",
                external_message_id=None,
                rfc_message_id=None,
                source_account=None,
                source_mailbox=None,
                subject="Acme contract renewal",
                sender_name=None,
                sender_email=None,
                sender_domain=None,
                recipients=(),
                sent_at=None,
                created_at=NOW,
            ),
            ambiguous_cases,
            NOW,
        )
        is None
    )

    results = demo.HeuristicElizabethanSearchClient().search_cases(
        "Northwind",
        artifact,
        None,
        cases,
        NOW,
    )
    assert results
    assert "matched" in results[0].reason


def test_local_artifact_store_writes_to_artifact_directory(tmp_path) -> None:
    artifact_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    storage_key = demo.LocalArtifactStore(tmp_path).store(artifact_id, "mail.msg", b"body")

    assert storage_key == str(tmp_path / f"{artifact_id}.bin")
    assert (tmp_path / f"{artifact_id}.bin").read_bytes() == b"body"

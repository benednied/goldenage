from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from goldenage.adapters.demo import (
    HeuristicElizabethanSearchClient,
    HeuristicGiselaClient,
    InMemoryActivityRepository,
    InMemoryArtifactRepository,
    InMemoryAuditRepository,
    InMemoryCaseRepository,
    LocalArtifactStore,
    build_demo_state,
)
from goldenage.application.use_cases import (
    GoldenAgeService,
    IntakeState,
    NotFoundError,
    _already_imported_message,
    _build_mail_metadata,
    _dedupe_fingerprint,
    _merge_participants,
    _normalize_mail_selector,
    _normalize_subject,
    _suggest_new_case_title,
)
from goldenage.domain.models import (
    ExtractedArtifactData,
    ImportedMailPayload,
    MailCandidate,
    MailParticipant,
    MailSelector,
)
from goldenage.domain.rules import ResolutionError


class StubContentExtractor:
    def __init__(self, extracted: ExtractedArtifactData) -> None:
        self._extracted = extracted

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        del file_name, media_type, content
        return self._extracted


class StubMailImportClient:
    def __init__(
        self,
        candidates: tuple[MailCandidate, ...],
        payloads: dict[str, ImportedMailPayload],
    ) -> None:
        self._candidates = candidates
        self._payloads = payloads

    def search_candidates(self, selector: MailSelector) -> tuple[MailCandidate, ...]:
        del selector
        return self._candidates

    def fetch_message(self, candidate_id: str) -> ImportedMailPayload:
        return self._payloads[candidate_id]


class StubMailImportRepository:
    def __init__(self) -> None:
        self.selector: MailSelector | None = None
        self.candidates: dict[str, MailCandidate] = {}
        self.imported_ids: set[str] = set()

    def upsert_source(self, *, user, source_system, now) -> None:
        del user, source_system, now

    def save_selector(self, *, user, source_system, selector, now) -> None:
        del user, source_system, now
        self.selector = selector

    def get_selector(self, *, user, source_system) -> MailSelector | None:
        del user, source_system
        return self.selector

    def replace_review_candidates(self, *, user, source_system, candidates, now) -> None:
        del user, source_system, now
        self.candidates = {candidate.candidate_id: candidate for candidate in candidates}

    def list_review_candidates(self, *, user, source_system) -> tuple[MailCandidate, ...]:
        del user, source_system
        return tuple(self.candidates.values())

    def get_review_candidate(self, *, user, source_system, candidate_id) -> MailCandidate | None:
        del user, source_system
        return self.candidates.get(candidate_id)

    def discard_review_candidate(self, *, user, source_system, candidate_id) -> None:
        del user, source_system
        self.candidates.pop(candidate_id, None)

    def list_imported_message_ids(self, *, user, source_system) -> frozenset[str]:
        del user, source_system
        return frozenset(self.imported_ids)

    def save_imported_message(
        self,
        *,
        user,
        source_system,
        external_message_id,
        rfc_message_id,
        artifact_id,
        now,
    ) -> None:
        del user, source_system, rfc_message_id, artifact_id, now
        self.imported_ids.add(external_message_id)


def build_service(
    tmp_path,
    extracted: ExtractedArtifactData | None = None,
    mail_import_client: StubMailImportClient | None = None,
    mail_import_repository: StubMailImportRepository | None = None,
):
    state, user = build_demo_state()
    case_repository = InMemoryCaseRepository(state)
    activity_repository = InMemoryActivityRepository(state, case_repository)
    artifact_repository = InMemoryArtifactRepository(state, case_repository)
    extractor = StubContentExtractor(
        extracted or _sample_extracted_data(subject="Vendor contract renewal")
    )
    service = GoldenAgeService(
        case_repository=case_repository,
        activity_repository=activity_repository,
        artifact_repository=artifact_repository,
        audit_repository=InMemoryAuditRepository(state),
        artifact_store=LocalArtifactStore(tmp_path),
        content_extractor=extractor,
        gisela_client=HeuristicGiselaClient(),
        elizabethan_client=HeuristicElizabethanSearchClient(),
        mail_import_client=mail_import_client,
        mail_import_repository=mail_import_repository,
    )
    return service, user, state


def test_today_worklist_contains_overdue_and_later_today_but_not_tomorrow(tmp_path) -> None:
    service, user, _ = build_service(tmp_path)
    current_day = datetime.now(UTC).date()
    end_of_day = datetime.combine(current_day, datetime.max.time(), tzinfo=UTC)

    items = service.get_today_worklist(user=user, now=end_of_day)

    descriptions = [item.activity.description for item in items]
    assert "Contact Sender about the amended pricing appendix." in descriptions
    assert "Review the outstanding compliance questionnaire." in descriptions
    assert "Prepare the response to the disputed invoice." not in descriptions


def test_upload_subject_match_proposes_case(tmp_path) -> None:
    service, user, state = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="RE: Vendor contract renewal draft"),
    )

    intake = service.upload_artifact(
        file_name="renewal.msg",
        media_type="application/vnd.ms-outlook",
        content=b"fake msg bytes",
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )

    assert intake.artifact is not None
    assert intake.suggestion is not None
    assert intake.search_mode is False
    assert intake.suggestion.suggested_case_id is not None
    mail_metadata = state.artifact_mail_metadata[intake.artifact.id]
    assert mail_metadata.subject == "RE: Vendor contract renewal draft"
    assert mail_metadata.sender_domain == "vendor.example.test"


def test_upload_sets_new_case_title_suggestion_from_cleaned_subject(tmp_path) -> None:
    service, user, _ = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="AW: RE: Fwd: Fresh intake matter"),
    )

    intake = service.upload_artifact(
        file_name="fresh.msg",
        media_type="application/vnd.ms-outlook",
        content=b"fake msg bytes",
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )

    assert intake.new_case_title_suggestion == "Fresh intake matter"


def test_upload_subject_match_handles_decomposed_german_city_name(tmp_path) -> None:
    service, user, state = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="RE: AI Tinkerers Nürnberg April Meetup"),
    )
    case_id = next(iter(state.cases))
    state.cases[case_id] = replace(
        state.cases[case_id],
        title="AI Tinkerers Nürnberg April Meetup",
    )

    intake = service.upload_artifact(
        file_name="nuernberg.msg",
        media_type="application/vnd.ms-outlook",
        content=b"fake msg bytes",
        user=user,
        now=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )

    assert intake.suggestion is not None
    assert intake.suggestion.suggested_case_id == case_id
    assert intake.search_mode is False


def test_upload_without_safe_subject_match_enters_search_mode(tmp_path) -> None:
    service, user, _ = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="totally unrelated phrase cluster"),
    )

    state = service.upload_artifact(
        file_name="unknown.msg",
        media_type="application/vnd.ms-outlook",
        content=b"fake msg bytes",
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )

    assert state.artifact is not None
    assert state.search_mode is True


def test_search_and_import_apple_mail_candidate_uses_shared_intake_flow(tmp_path) -> None:
    candidate = MailCandidate(
        candidate_id="apple-1",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="Vendor contract renewal",
        sender_name="Sender Fixture",
        sender_email="sender.fixture@vendor.example.test",
        sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
        preview_text="Please review the latest renewal draft.",
        unread=True,
        rfc_message_id="<apple-1@example.com>",
    )
    payload = ImportedMailPayload(
        source_system="desktop_mail_client",
        external_message_id="apple-1",
        rfc_message_id="<apple-1@example.com>",
        account_name="iCloud",
        mailbox_name="Inbox",
        file_name="acme-contract-renewal.eml",
        media_type="message/rfc822",
        content=(
            b"From: Sender Fixture <sender.fixture@vendor.example.test>\n"
            b"To: Fixture User <user.fixture@example.test>\n"
            b"Subject: Vendor contract renewal\n"
            b"Date: Sun, 12 Apr 2026 09:30:00 +0000\n"
            b"Message-ID: <apple-1@example.com>\n"
            b"\n"
            b"Please review the latest renewal draft.\n"
        ),
        unread=True,
    )
    repository = StubMailImportRepository()
    client = StubMailImportClient((candidate,), {"apple-1": payload})
    service, user, state = build_service(
        tmp_path,
        extracted=_sample_rfc822_data(subject="Vendor contract renewal"),
        mail_import_client=client,
        mail_import_repository=repository,
    )

    import_state = service.search_mail_candidates(
        selector=MailSelector(mailbox_name="Inbox"),
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )

    assert import_state.enabled is True
    assert len(import_state.candidates) == 1
    assert repository.selector is not None
    assert repository.selector.mailbox_name == "Inbox"

    intake_state = service.import_mail_candidate(
        candidate_id="apple-1",
        user=user,
        now=datetime(2026, 4, 12, 10, 5, tzinfo=UTC),
    )

    assert intake_state.artifact is not None
    assert intake_state.suggestion is not None
    assert intake_state.search_mode is False
    metadata = state.artifact_mail_metadata[intake_state.artifact.id]
    assert metadata.source_system == "desktop_mail_client"
    assert metadata.message_format == "rfc822_email"
    assert metadata.external_message_id == "apple-1"
    assert metadata.source_account == "iCloud"
    assert "apple-1" in repository.imported_ids

    empty_state = service.search_mail_candidates(
        selector=MailSelector(mailbox_name="Inbox"),
        user=user,
        now=datetime(2026, 4, 12, 10, 10, tzinfo=UTC),
    )
    assert empty_state.candidates == ()


def test_search_mail_candidates_reports_already_imported_matches(tmp_path) -> None:
    candidate = MailCandidate(
        candidate_id="apple-1",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="AI Tinkerers Nürnberg",
        sender_name="Nürnberg Updates",
        sender_email="updates@example.com",
        sent_at=datetime(2026, 4, 26, 9, 30, tzinfo=UTC),
        preview_text="Already imported.",
        unread=True,
        rfc_message_id="<apple-1@example.com>",
    )
    repository = StubMailImportRepository()
    repository.imported_ids.add("apple-1")
    client = StubMailImportClient((candidate,), {})
    service, user, _ = build_service(
        tmp_path,
        mail_import_client=client,
        mail_import_repository=repository,
    )

    state = service.search_mail_candidates(
        selector=MailSelector(subject_filter="Nürnberg"),
        user=user,
        now=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )

    assert state.candidates == ()
    assert state.message == "Already imported: 1 matching mail message is already in intake."
    assert state.message_kind == "error"


def test_service_reports_unavailable_mail_runtime_and_missing_entities(tmp_path) -> None:
    service, user, _ = build_service(tmp_path)

    assert service.get_mail_import_state(user=user).enabled is False
    assert (
        service.search_mail_candidates(
            selector=MailSelector(),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        ).message
        == "Mail import is not available in this runtime."
    )
    assert service.get_mail_selector_settings(user=user) == MailSelector()
    assert service._mail_selector_for_user(user) == MailSelector()

    with pytest.raises(NotFoundError, match="Mail settings"):
        service.save_mail_selector_settings(
            selector=MailSelector(),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Mail import"):
        service.import_mail_candidate(
            candidate_id="missing",
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Case"):
        service.get_case_detail(
            case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaffff"),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Activity"):
        service.get_case_detail_for_activity(
            activity_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbffff"),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Artifact"):
        service.get_intake_state(
            artifact_id=UUID("cccccccc-cccc-cccc-cccc-ccccccccffff"),
            user=user,
        )
    with pytest.raises(NotFoundError, match="Artifact"):
        service.search_cases_for_artifact(
            artifact_id=UUID("cccccccc-cccc-cccc-cccc-ccccccccffff"),
            query="Acme",
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Conversation"):
        service.get_intake_state_for_conversation(
            conversation_id=UUID("dddddddd-dddd-dddd-dddd-ddddddddffff"),
            user=user,
        )


def test_service_mail_selector_persistence_and_duplicate_import_errors(tmp_path) -> None:
    candidate = MailCandidate(
        candidate_id="apple-1",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="Vendor contract renewal",
        sender_name="Sender",
        sender_email="sender.fixture@example.test",
        sent_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
        preview_text="Preview",
        unread=True,
        rfc_message_id="<apple-1@example.com>",
    )
    payload = ImportedMailPayload(
        source_system="desktop_mail_client",
        external_message_id="apple-1",
        rfc_message_id="<apple-1@example.com>",
        account_name="iCloud",
        mailbox_name="Inbox",
        file_name="renewal.eml",
        media_type="message/rfc822",
        content=b"Subject: Vendor contract renewal\n\nBody",
        unread=True,
    )
    repository = StubMailImportRepository()
    client = StubMailImportClient((candidate,), {"apple-1": payload})
    service, user, _ = build_service(
        tmp_path,
        extracted=_sample_rfc822_data(subject="Vendor contract renewal"),
        mail_import_client=client,
        mail_import_repository=repository,
    )

    normalized = service.save_mail_selector_settings(
        selector=MailSelector(account_name=" iCloud ", mailbox_name=" Inbox ", result_limit=100),
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    assert normalized.account_name == "iCloud"
    assert normalized.mailbox_name == "Inbox"
    assert normalized.result_limit == 50

    with pytest.raises(NotFoundError, match="Mail candidate"):
        service.import_mail_candidate(
            candidate_id="missing",
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )

    repository.replace_review_candidates(
        user=user,
        source_system="desktop_mail_client",
        candidates=(candidate,),
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    repository.imported_ids.add("apple-1")
    with pytest.raises(ResolutionError, match="already imported"):
        service.import_mail_candidate(
            candidate_id="apple-1",
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )


def test_service_artifact_assignment_and_new_case_validation_paths(tmp_path) -> None:
    service, user, state = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="totally unrelated phrase cluster"),
    )
    intake = service.upload_artifact(
        file_name="unknown.msg",
        media_type="application/vnd.ms-outlook",
        content=b"fake msg bytes",
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    assert intake.artifact is not None
    artifact_id = intake.artifact.id
    case_id = next(iter(state.cases))

    with pytest.raises(NotFoundError, match="Artifact"):
        service.assign_artifact_to_case(
            artifact_id=UUID("cccccccc-cccc-cccc-cccc-ccccccccffff"),
            case_id=case_id,
            next_step="Contact Sender",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Artifact"):
        service.create_case_for_artifact(
            artifact_id=UUID("cccccccc-cccc-cccc-cccc-ccccccccffff"),
            title="Fresh matter",
            company="",
            primary_contact="",
            next_step="Contact Sender",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(NotFoundError, match="Case"):
        service.assign_artifact_to_case(
            artifact_id=artifact_id,
            case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaffff"),
            next_step="Contact Sender",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(ResolutionError, match="next step"):
        service.assign_artifact_to_case(
            artifact_id=artifact_id,
            case_id=case_id,
            next_step=" ",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(ResolutionError, match="case title"):
        service.create_case_for_artifact(
            artifact_id=artifact_id,
            title=" ",
            company="",
            primary_contact="",
            next_step="Contact Sender",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
    with pytest.raises(ResolutionError, match="next step"):
        service.create_case_for_artifact(
            artifact_id=artifact_id,
            title="Fresh matter",
            company="",
            primary_contact="",
            next_step=" ",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )

    detail = service.create_case_for_artifact(
        artifact_id=artifact_id,
        title=" Fresh matter ",
        company=" Acme ",
        primary_contact=" Fixture User ",
        next_step=" Contact Sender ",
        next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    assert detail.case_file.title == "Fresh matter"
    assert detail.case_file.company == "Acme"


def test_service_resolves_activity_and_updates_case_state(tmp_path) -> None:
    service, user, state = build_service(tmp_path)
    activity_id = next(iter(state.activities))
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

    detail = service.resolve_activity(
        activity_id=activity_id,
        user=user,
        now=now,
        next_step="Send revised terms",
        next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
        close_case=False,
        skip_follow_up=False,
    )

    assert state.activities[activity_id].completed_at == now
    assert any(
        activity.description == "Send revised terms" for activity in state.activities.values()
    )
    assert detail.case_file.status == "open"
    assert state.audit_events[-1].event_type == "activity_resolved"

    second_activity_id = next(
        activity.id
        for activity in state.activities.values()
        if activity.completed_at is None and activity.id != activity_id
    )
    closed = service.resolve_activity(
        activity_id=second_activity_id,
        user=user,
        now=now,
        next_step=None,
        next_due_at=None,
        close_case=True,
        skip_follow_up=False,
    )
    assert closed.case_file.status == "closed"


def test_service_defensive_repository_edge_paths(tmp_path) -> None:
    service, user, state = build_service(tmp_path)
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
    activity_id = next(iter(state.activities))
    missing_case_id = state.activities[activity_id].case_id
    stale_activity = state.activities[activity_id]

    del state.cases[missing_case_id]
    service._activity_repository.list_due_activities = lambda user, now: (stale_activity,)
    assert service.get_today_worklist(user=user, now=now) == ()
    service._activity_repository.get_activity = lambda activity_id, user: stale_activity
    with pytest.raises(NotFoundError, match="Case"):
        service.resolve_activity(
            activity_id=activity_id,
            user=user,
            now=now,
            next_step=None,
            next_due_at=None,
            close_case=False,
            skip_follow_up=True,
        )
    service._activity_repository = InMemoryActivityRepository(
        state,
        InMemoryCaseRepository(state),
    )

    state.cases[missing_case_id] = build_demo_state()[0].cases[missing_case_id]
    state.activities[activity_id] = stale_activity
    assert (
        service.get_case_detail_for_activity(
            activity_id=activity_id,
            user=user,
            now=now,
        ).case_file.id
        == missing_case_id
    )

    intake = service.upload_artifact(
        file_name="unknown.msg",
        media_type="application/vnd.ms-outlook",
        content=b"fake msg bytes",
        user=user,
        now=now,
    )
    assert intake.artifact is not None
    with pytest.raises(NotFoundError, match="Artifact"):
        service.get_case_detail(
            case_id=missing_case_id,
            selected_artifact_id=intake.artifact.id,
            user=user,
            now=now,
        )
    assert service.list_unassigned_intake(user=user, limit=1)


def test_service_import_mail_defensive_empty_artifact_path(tmp_path, monkeypatch) -> None:
    candidate = MailCandidate(
        candidate_id="apple-1",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="Vendor contract renewal",
        sender_name="Sender",
        sender_email="sender.fixture@example.test",
        sent_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
        preview_text="Preview",
        unread=True,
        rfc_message_id="<apple-1@example.com>",
    )
    payload = ImportedMailPayload(
        source_system="desktop_mail_client",
        external_message_id="apple-1",
        rfc_message_id="<apple-1@example.com>",
        account_name="iCloud",
        mailbox_name="Inbox",
        file_name="renewal.eml",
        media_type="message/rfc822",
        content=b"Subject: Vendor contract renewal\n\nBody",
        unread=True,
    )
    repository = StubMailImportRepository()
    repository.replace_review_candidates(
        user=None,
        source_system="desktop_mail_client",
        candidates=(candidate,),
        now=None,
    )
    service, user, _ = build_service(
        tmp_path,
        mail_import_client=StubMailImportClient((candidate,), {"apple-1": payload}),
        mail_import_repository=repository,
    )
    monkeypatch.setattr(
        service,
        "_ingest_mail_artifact",
        lambda **kwargs: IntakeState(artifact=None),
    )

    with pytest.raises(RuntimeError, match="did not create an artifact"):
        service.import_mail_candidate(
            candidate_id="apple-1",
            user=user,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )


def test_service_conversation_merge_and_existing_dedupe_paths(tmp_path) -> None:
    service, user, _ = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="RE: Vendor contract renewal"),
    )
    first_extracted = _sample_extracted_data(subject="RE: Vendor contract renewal")
    first = service.ingest_mail(
        file_name="first.msg",
        media_type="application/vnd.ms-outlook",
        content=b"first",
        extracted=first_extracted,
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    second_extracted = _sample_extracted_data(subject="RE: Vendor contract renewal")
    second_extracted = ExtractedArtifactData(
        source_kind=second_extracted.source_kind,
        parse_status=second_extracted.parse_status,
        content_text=second_extracted.content_text,
        subject=second_extracted.subject,
        sender=second_extracted.sender,
        recipients=second_extracted.recipients,
        sent_at=second_extracted.sent_at,
        received_at=datetime(2026, 4, 12, 10, 31, tzinfo=UTC),
        direction=second_extracted.direction,
        source_account_id=second_extracted.source_account_id,
        source_folder_id=second_extracted.source_folder_id,
        source_message_id="message-2",
        conversation_id=second_extracted.conversation_id,
        internet_message_id="<msg-2@example.com>",
    )
    second = service.ingest_mail(
        file_name="second.msg",
        media_type="application/vnd.ms-outlook",
        content=b"second",
        extracted=second_extracted,
        user=user,
        now=datetime(2026, 4, 12, 11, 0, tzinfo=UTC),
    )

    assert first.conversation is not None
    assert second.conversation is not None
    assert first.conversation.id == second.conversation.id
    assert second.conversation.message_count == 2
    assert second.conversation.latest_subject == "RE: Vendor contract renewal"

    duplicate = service.ingest_mail(
        file_name="duplicate.msg",
        media_type="application/vnd.ms-outlook",
        content=b"duplicate",
        extracted=_sample_extracted_data(subject="RE: Vendor contract renewal"),
        user=user,
        now=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
    )
    assert duplicate.conversation is not None
    assert duplicate.conversation.id == first.conversation.id


def test_service_search_candidate_messages_for_no_matches_and_partial_imports(tmp_path) -> None:
    imported = MailCandidate(
        candidate_id="imported",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="Imported",
        sender_name="Sender",
        sender_email="sender.fixture@example.test",
        sent_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
        preview_text="Imported",
        unread=True,
        rfc_message_id="<imported@example.com>",
    )
    fresh = MailCandidate(
        candidate_id="fresh",
        source_system="desktop_mail_client",
        account_name="iCloud",
        mailbox_name="Inbox",
        subject="Fresh",
        sender_name="Sender",
        sender_email="sender.fixture@example.test",
        sent_at=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        preview_text="Fresh",
        unread=True,
        rfc_message_id="<fresh@example.com>",
    )
    repository = StubMailImportRepository()
    repository.imported_ids.add("imported")
    service, user, _ = build_service(
        tmp_path,
        mail_import_client=StubMailImportClient((imported, fresh), {}),
        mail_import_repository=repository,
    )

    partial = service.search_mail_candidates(
        selector=MailSelector(),
        user=user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    assert partial.message == (
        "Mail candidates loaded. Already imported: 1 matching mail message is already in intake."
    )
    assert partial.message_kind == "error"

    empty_service, empty_user, _ = build_service(
        tmp_path,
        mail_import_client=StubMailImportClient((), {}),
        mail_import_repository=StubMailImportRepository(),
    )
    empty = empty_service.search_mail_candidates(
        selector=MailSelector(),
        user=empty_user,
        now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
    )
    assert empty.message == "No mail messages matched the current selector."


def test_service_pure_helpers_cover_edge_cases() -> None:
    extracted = ExtractedArtifactData(
        message_format="plain_text",  # ty:ignore[invalid-argument-type]
        parse_status="parsed",
        content_text="Body",
        subject=None,
        sender=None,
        recipients=(),
        sent_at=None,
    )
    assert (
        _build_mail_metadata(
            artifact_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            extracted=extracted,
            source_system="outlook_upload",
            external_message_id=None,
            rfc_message_id=None,
            source_account=None,
            source_mailbox=None,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        )
        is None
    )
    assert _suggest_new_case_title(None) == ""
    assert _normalize_mail_selector(MailSelector(result_limit=0)).result_limit == 1
    assert _already_imported_message(2) == (
        "Already imported: 2 matching mail messages are already in intake."
    )
    assert _normalize_subject(None) is None
    assert _normalize_subject(" RE: FW: Renewal  ") == "renewal"
    assert _merge_participants(
        (MailParticipant(name="Sender", email="sender.fixture@example.test"),),
        (
            MailParticipant(name="SENDER", email="SENDER.FIXTURE@example.test"),
            MailParticipant(name="Fixture User", email=None),
        ),
    ) == (
        MailParticipant(name="Sender", email="sender.fixture@example.test"),
        MailParticipant(name="Fixture User", email=None),
    )
    assert _dedupe_fingerprint(
        source_kind="kind",
        source_account_id=None,
        source_folder_id=None,
        source_message_id=None,
        internet_message_id=None,
        subject=None,
        sent_at=None,
        content_text="Body",
    )


def _sample_extracted_data(subject: str) -> ExtractedArtifactData:
    return ExtractedArtifactData(
        message_format="outlook_msg",
        parse_status="parsed",
        rfc_message_id=None,
        content_text="body text",
        subject=subject,
        sender=MailParticipant(name="Sender Fixture", email="sender.fixture@vendor.example.test"),
        recipients=(MailParticipant(name="Fixture User", email="user.fixture@example.test"),),
        sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
    )


def _sample_rfc822_data(subject: str) -> ExtractedArtifactData:
    return ExtractedArtifactData(
        message_format="rfc822_email",
        parse_status="parsed",
        rfc_message_id="<apple-1@example.com>",
        content_text="body text",
        subject=subject,
        sender=MailParticipant(name="Sender Fixture", email="sender.fixture@vendor.example.test"),
        recipients=(MailParticipant(name="Fixture User", email="user.fixture@example.test"),),
        sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
    )

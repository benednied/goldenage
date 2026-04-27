from dataclasses import replace
from datetime import UTC, datetime

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
from goldenage.application.use_cases import GoldenAgeService
from goldenage.domain.models import (
    ExtractedArtifactData,
    ImportedMailPayload,
    MailCandidate,
    MailParticipant,
    MailSelector,
)


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
        extracted or _sample_extracted_data(subject="Acme contract renewal")
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
    assert "Call Max about the amended pricing appendix." in descriptions
    assert "Review the outstanding compliance questionnaire." in descriptions
    assert "Prepare the response to the disputed invoice." not in descriptions


def test_upload_subject_match_proposes_case(tmp_path) -> None:
    service, user, state = build_service(
        tmp_path,
        extracted=_sample_extracted_data(subject="RE: Acme contract renewal draft"),
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
    assert mail_metadata.subject == "RE: Acme contract renewal draft"
    assert mail_metadata.sender_domain == "acme.example"


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
        subject="Acme contract renewal",
        sender_name="Max Mustermann",
        sender_email="max@acme.example",
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
            b"From: Max Mustermann <max@acme.example>\n"
            b"To: Alex Example <alex@example.com>\n"
            b"Subject: Acme contract renewal\n"
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
        extracted=_sample_rfc822_data(subject="Acme contract renewal"),
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


def _sample_rfc822_data(subject: str) -> ExtractedArtifactData:
    return ExtractedArtifactData(
        message_format="rfc822_email",
        parse_status="parsed",
        rfc_message_id="<apple-1@example.com>",
        content_text="body text",
        subject=subject,
        sender=MailParticipant(name="Max Mustermann", email="max@acme.example"),
        recipients=(MailParticipant(name="Alex Example", email="alex@example.com"),),
        sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
    )

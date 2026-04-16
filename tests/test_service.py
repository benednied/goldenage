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
from goldenage.domain.models import ExtractedArtifactData, MailParticipant


class StubContentExtractor:
    def __init__(self, extracted: ExtractedArtifactData) -> None:
        self._extracted = extracted

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        del file_name, media_type, content
        return self._extracted


def build_service(tmp_path, extracted: ExtractedArtifactData | None = None):
    state, user = build_demo_state()
    case_repository = InMemoryCaseRepository(state)
    activity_repository = InMemoryActivityRepository(state, case_repository)
    artifact_repository = InMemoryArtifactRepository(state, case_repository)
    extractor = StubContentExtractor(extracted or _sample_extracted_data(subject="Acme contract renewal"))
    service = GoldenAgeService(
        case_repository=case_repository,
        activity_repository=activity_repository,
        artifact_repository=artifact_repository,
        audit_repository=InMemoryAuditRepository(state),
        artifact_store=LocalArtifactStore(tmp_path),
        content_extractor=extractor,
        gisela_client=HeuristicGiselaClient(),
        elizabethan_client=HeuristicElizabethanSearchClient(),
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


def test_create_case_for_artifact_creates_new_case_and_activity(tmp_path) -> None:
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

    detail = service.create_case_for_artifact(
        artifact_id=intake.artifact.id,
        title="Fresh intake matter",
        company="Acme GmbH",
        primary_contact="Max Mustermann",
        next_step="Review the new matter and respond",
        next_due_at=datetime(2026, 4, 13, 9, 0, tzinfo=UTC),
        user=user,
        now=datetime(2026, 4, 12, 10, 5, tzinfo=UTC),
    )

    assert detail.case_file.title == "Fresh intake matter"
    assert detail.case_file.company == "Acme GmbH"
    assert detail.case_file.primary_contact == "Max Mustermann"
    assert len(detail.open_activities) == 1
    assert detail.recent_artifacts[0].id == intake.artifact.id
    assert state.artifacts[intake.artifact.id].assigned_case_id == detail.case_file.id


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

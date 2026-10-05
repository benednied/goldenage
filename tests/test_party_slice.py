from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from goldenage.adapters.demo import (
    DemoState,
    HeuristicElizabethanSearchClient,
    HeuristicGiselaClient,
    InMemoryActivityRepository,
    InMemoryArtifactRepository,
    InMemoryAuditRepository,
    InMemoryCaseRepository,
    InMemoryPartyRepository,
    build_demo_state,
)
from goldenage.adapters.sqlite import SQLiteCaseRepository, SQLitePartyRepository
from goldenage.application.use_cases import GoldenAgeService, NotFoundError
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped
from goldenage.domain.models import (
    CaseFile,
    Company,
    Contact,
    ContactNote,
    ExtractedArtifactData,
    UserContext,
)
from goldenage.domain.rules import ResolutionError

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class _Store:
    def store(self, artifact_id, file_name, content) -> str:
        del file_name, content
        return str(artifact_id)


class _Extractor:
    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        del file_name, media_type, content
        raise AssertionError("party tests do not ingest artifacts")


def _demo_service() -> tuple[GoldenAgeService, UserContext, DemoState]:
    state, user = build_demo_state()
    case_repository = InMemoryCaseRepository(state)
    party_repository = InMemoryPartyRepository(state, case_repository)
    service = GoldenAgeService(
        case_repository=case_repository,
        activity_repository=InMemoryActivityRepository(state, case_repository),
        artifact_repository=InMemoryArtifactRepository(state, case_repository),
        audit_repository=InMemoryAuditRepository(state),
        artifact_store=_Store(),
        content_extractor=_Extractor(),
        gisela_client=HeuristicGiselaClient(),
        elizabethan_client=HeuristicElizabethanSearchClient(),
        party_repository=party_repository,
    )
    return service, user, state


def test_party_workflow_creates_note_and_explicit_case_link() -> None:
    service, user, state = _demo_service()

    company = service.create_company(name="New Company", user=user, now=NOW)
    contact = service.create_contact(
        company_id=company.id,
        name="New Contact",
        email="person@example.com",
        phone="+49 30 123",
        user=user,
        now=NOW,
    )
    note = service.add_contact_note(
        contact_id=contact.id,
        body="Agreed to send the revised proposal.",
        user=user,
        now=NOW,
    )
    case = next(iter(state.cases.values()))
    service.associate_case(
        case_id=case.id,
        company_id=company.id,
        contact_id=contact.id,
        user=user,
        now=NOW,
    )

    detail = service.get_contact_detail(contact_id=contact.id, user=user)
    assert detail.notes == (note,)
    assert detail.cases[0].id == case.id
    assert [event.event_type for event in state.audit_events[-4:]] == [
        "company_created",
        "contact_created",
        "contact_note_created",
        "case_party_associated",
    ]


def test_party_duplicate_and_mismatched_association_are_rejected() -> None:
    service, user, state = _demo_service()
    company = service.create_company(name="Duplicate Co", user=user, now=NOW)
    with pytest.raises(ResolutionError, match="already exists"):
        service.create_company(name=" duplicate   co ", user=user, now=NOW)
    contact = service.create_contact(
        company_id=company.id,
        name="Person",
        email="same@example.com",
        phone="",
        user=user,
        now=NOW,
    )
    with pytest.raises(ResolutionError, match="already exists"):
        service.create_contact(
            company_id=company.id,
            name="Other Person",
            email="SAME@example.com",
            phone="",
            user=user,
            now=NOW,
        )
    other_company = service.create_company(name="Other Co", user=user, now=NOW)
    with pytest.raises(ResolutionError, match="different company"):
        service.associate_case(
            case_id=next(iter(state.cases)),
            company_id=other_company.id,
            contact_id=contact.id,
            user=user,
            now=NOW,
        )


def test_hidden_party_records_are_not_returned_by_direct_or_association_reads() -> None:
    service, user, state = _demo_service()
    group_id = uuid4()
    hidden_company = Company(
        id=uuid4(),
        name="Hidden Company",
        normalized_name="hidden company",
        created_at=NOW,
        updated_at=NOW,
        visible_group_id=group_id,
    )
    party_repository = InMemoryPartyRepository(state, InMemoryCaseRepository(state))
    party_repository.save_company(hidden_company)
    assert service.list_companies(user=user, query="hidden") == ()
    with pytest.raises(NotFoundError, match="Company not found"):
        service.get_company_detail(company_id=hidden_company.id, user=user)


def test_sqlite_party_records_survive_restart_and_legacy_strings_remain(tmp_path: Path) -> None:
    database_path = tmp_path / "goldenage.sqlite3"
    ensure_sqlite_bootstrapped(database_path)
    user = UserContext(id=uuid4(), email="alex@example.com", display_name="Alex")
    party_repository = SQLitePartyRepository(database_path)
    case_repository = SQLiteCaseRepository(database_path)
    with party_repository._connect() as connection:
        connection.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (str(user.id), user.email, user.display_name, "hash"),
        )

    company = Company(uuid4(), "Persisted Co", "persisted co", NOW, NOW, user.id)
    party_repository.save_company(company)
    contact = Contact(
        uuid4(),
        company.id,
        "Persisted Person",
        "persisted@example.com",
        None,
        "persisted@example.com",
        NOW,
        NOW,
        user.id,
    )
    party_repository.save_contact(contact)
    note = ContactNote(uuid4(), contact.id, "Restart-safe note", NOW, user.id)
    party_repository.save_contact_note(note)
    case = CaseFile(uuid4(), "Legacy case", "Legacy Co", "Legacy Person", "open", NOW)
    case_repository.save_case(case)

    restarted = SQLitePartyRepository(database_path)
    assert restarted.get_company(company.id, user) == company
    assert restarted.get_contact(contact.id, user) == contact
    assert restarted.list_contact_notes(contact.id, user) == (note,)
    assert case_repository.get_case(case.id, user) == case

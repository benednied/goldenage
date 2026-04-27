"""Demo and local-development adapters."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parseaddr
from pathlib import Path
from uuid import UUID

from goldenage.application.ports import (
    ActivityRepository,
    ArtifactContentExtractor,
    ArtifactRepository,
    ArtifactStore,
    AuditRepository,
    CaseRepository,
    ElizabethanSearchClient,
    GiselaClient,
)
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    ExtractedArtifactData,
    MailParticipant,
    SearchResult,
    UserContext,
)

WORD_RE = re.compile(r"[^\W_]{3,}")


@dataclass
class DemoState:
    """Mutable in-memory state shared by local adapters."""

    users: dict[UUID, UserContext]
    cases: dict[UUID, CaseFile]
    activities: dict[UUID, Activity]
    artifacts: dict[UUID, Artifact]
    artifact_mail_metadata: dict[UUID, ArtifactMailMetadata]
    suggestions: dict[UUID, AssignmentSuggestion]
    audit_events: list[AuditEvent]


class InMemoryCaseRepository(CaseRepository):
    """In-memory case repository."""

    def __init__(self, state: DemoState) -> None:
        self._state = state

    def list_cases(self, user: UserContext) -> Sequence[CaseFile]:
        return tuple(
            case_file
            for case_file in self._state.cases.values()
            if _case_visible_to_user(case_file, user)
        )

    def get_case(self, case_id: UUID, user: UserContext) -> CaseFile | None:
        case_file = self._state.cases.get(case_id)
        if case_file is None or not _case_visible_to_user(case_file, user):
            return None
        return case_file

    def save_case(self, case_file: CaseFile) -> None:
        self._state.cases[case_file.id] = case_file


class InMemoryActivityRepository(ActivityRepository):
    """In-memory activity repository."""

    def __init__(self, state: DemoState, case_repository: InMemoryCaseRepository) -> None:
        self._state = state
        self._case_repository = case_repository

    def list_due_activities(self, user: UserContext, now: datetime) -> Sequence[Activity]:
        return tuple(
            activity
            for activity in self._state.activities.values()
            if activity.completed_at is None
            and activity.due_at <= now
            and self._case_repository.get_case(activity.case_id, user) is not None
        )

    def list_case_activities(self, case_id: UUID, user: UserContext) -> Sequence[Activity]:
        if self._case_repository.get_case(case_id, user) is None:
            return ()
        return tuple(
            activity for activity in self._state.activities.values() if activity.case_id == case_id
        )

    def get_activity(self, activity_id: UUID, user: UserContext) -> Activity | None:
        activity = self._state.activities.get(activity_id)
        if activity is None:
            return None
        if self._case_repository.get_case(activity.case_id, user) is None:
            return None
        return activity

    def save_activity(self, activity: Activity) -> None:
        self._state.activities[activity.id] = activity


class InMemoryArtifactRepository(ArtifactRepository):
    """In-memory artifact repository."""

    def __init__(self, state: DemoState, case_repository: InMemoryCaseRepository) -> None:
        self._state = state
        self._case_repository = case_repository

    def save_artifact(self, artifact: Artifact) -> None:
        self._state.artifacts[artifact.id] = artifact

    def get_artifact(self, artifact_id: UUID, user: UserContext) -> Artifact | None:
        artifact = self._state.artifacts.get(artifact_id)
        if artifact is None:
            return None
        if artifact.assigned_case_id is None:
            return artifact
        if self._case_repository.get_case(artifact.assigned_case_id, user) is None:
            return None
        return artifact

    def list_case_artifacts(self, case_id: UUID, user: UserContext) -> Sequence[Artifact]:
        if self._case_repository.get_case(case_id, user) is None:
            return ()
        return tuple(
            artifact
            for artifact in self._state.artifacts.values()
            if artifact.assigned_case_id == case_id
        )

    def list_unassigned_artifacts(
        self,
        user: UserContext,
        *,
        limit: int,
    ) -> Sequence[Artifact]:
        artifacts = [
            artifact
            for artifact in self._state.artifacts.values()
            if artifact.assigned_case_id is None
            and (artifact.uploaded_by is None or artifact.uploaded_by == user.id)
        ]
        artifacts.sort(key=lambda artifact: artifact.uploaded_at, reverse=True)
        return tuple(artifacts[:limit])

    def save_suggestion(self, suggestion: AssignmentSuggestion) -> None:
        self._state.suggestions[suggestion.artifact_id] = suggestion

    def get_suggestion(self, artifact_id: UUID, user: UserContext) -> AssignmentSuggestion | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        return self._state.suggestions.get(artifact_id)

    def save_mail_metadata(self, metadata: ArtifactMailMetadata) -> None:
        self._state.artifact_mail_metadata[metadata.artifact_id] = metadata

    def get_mail_metadata(
        self, artifact_id: UUID, user: UserContext
    ) -> ArtifactMailMetadata | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        return self._state.artifact_mail_metadata.get(artifact_id)


class InMemoryAuditRepository(AuditRepository):
    """In-memory audit repository."""

    def __init__(self, state: DemoState) -> None:
        self._state = state

    def save_event(self, event: AuditEvent) -> None:
        self._state.audit_events.append(event)


class LocalArtifactStore(ArtifactStore):
    """Local artifact storage for development."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def store(self, artifact_id: UUID, file_name: str, content: bytes) -> str:
        target_dir = self._root / str(artifact_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_name
        target_path.write_bytes(content)
        return str(target_path)


class OutlookMsgExtractor(ArtifactContentExtractor):
    """Outlook MSG extractor for the current intake slice."""

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        del media_type
        try:
            from oxmsg import Message
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard.
            raise RuntimeError("python-oxmsg must be installed for Outlook intake.") from exc

        message = Message.load(content)
        sender = _participant_from_address(_coerce_optional_str(message.sender))
        recipients = tuple(
            MailParticipant(
                name=_coerce_optional_str(recipient.name),
                email=_normalize_email(_coerce_optional_str(recipient.email_address)),
            )
            for recipient in message.recipients
        )
        body_text = (message.body or message.html_body or "").strip()
        return ExtractedArtifactData(
            message_format="outlook_msg",
            parse_status="parsed",
            rfc_message_id=None,
            content_text=body_text,
            subject=_coerce_optional_str(message.subject) or file_name,
            sender=sender,
            recipients=recipients,
            sent_at=message.sent_date() if callable(message.sent_date) else message.sent_date,
        )


class HeuristicGiselaClient(GiselaClient):
    """Heuristic replacement for the future assignment agent."""

    def analyze_artifact(
        self,
        artifact: Artifact,
        mail_metadata: ArtifactMailMetadata | None,
        visible_cases: Sequence[CaseFile],
        now: datetime,
    ) -> AssignmentSuggestion:
        suggestion = _subject_suggestion_for_cases(mail_metadata, visible_cases, now)
        if suggestion is not None:
            return suggestion

        return AssignmentSuggestion(
            artifact_id=mail_metadata.artifact_id if mail_metadata is not None else artifact.id,
            suggested_case_id=None,
            summary_reason="No safe single-case subject match found.",
            confidence=0.0,
            created_at=now,
        )


class HeuristicElizabethanSearchClient(ElizabethanSearchClient):
    """Heuristic fallback search client."""

    def search_cases(
        self,
        query: str,
        artifact: Artifact,
        mail_metadata: ArtifactMailMetadata | None,
        visible_cases: Sequence[CaseFile],
        now: datetime,
    ) -> Sequence[SearchResult]:
        del now
        source = " ".join(
            part
            for part in (
                query,
                artifact.file_name,
                artifact.content_text,
                _mail_metadata_search_text(mail_metadata),
            )
            if part
        )
        ranked = _rank_cases(source, visible_cases)
        return tuple(
            SearchResult(
                case_id=case_file.id,
                title=case_file.title,
                company=case_file.company,
                last_activity_at=case_file.last_activity_at,
                reason=reason,
                score=score,
            )
            for score, case_file, reason in ranked[:5]
        )


def build_demo_state() -> tuple[DemoState, UserContext]:
    """Create seeded demo data so the app is usable immediately."""
    user = UserContext(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        email="alex@example.com",
        display_name="Alex Example",
        visible_group_ids=frozenset(),
    )
    now = datetime.now(UTC)

    cases = [
        CaseFile(
            id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
            title="Acme contract renewal",
            company="Acme Inc.",
            primary_contact="Max Mustermann",
            status="open",
            last_activity_at=now - timedelta(days=1),
        ),
        CaseFile(
            id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"),
            title="Northwind compliance follow-up",
            company="Northwind GmbH",
            primary_contact="Sabine Keller",
            status="open",
            last_activity_at=now - timedelta(hours=8),
        ),
        CaseFile(
            id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3"),
            title="Bluebird invoice dispute",
            company="Bluebird AG",
            primary_contact="Jonas Adler",
            status="open",
            last_activity_at=now - timedelta(days=3),
        ),
    ]

    activities = [
        Activity(
            id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
            case_id=cases[0].id,
            description="Call Max about the amended pricing appendix.",
            kind="follow_up",
            due_at=now - timedelta(hours=3),
            created_at=now - timedelta(days=2),
            created_by=user.id,
        ),
        Activity(
            id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2"),
            case_id=cases[1].id,
            description="Review the outstanding compliance questionnaire.",
            kind="question",
            due_at=now + timedelta(hours=2),
            created_at=now - timedelta(days=1),
            created_by=user.id,
        ),
        Activity(
            id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3"),
            case_id=cases[2].id,
            description="Prepare the response to the disputed invoice.",
            kind="escalation",
            due_at=now + timedelta(days=1),
            created_at=now - timedelta(days=1),
            created_by=user.id,
        ),
    ]

    state = DemoState(
        users={user.id: user},
        cases={case_file.id: case_file for case_file in cases},
        activities={activity.id: activity for activity in activities},
        artifacts={},
        artifact_mail_metadata={},
        suggestions={},
        audit_events=[],
    )
    return state, user


def _case_visible_to_user(case_file: CaseFile, user: UserContext) -> bool:
    if case_file.visible_group_id is None:
        return True
    return case_file.visible_group_id in user.visible_group_ids


def _rank_cases(
    source_text: str, visible_cases: Sequence[CaseFile]
) -> list[tuple[float, CaseFile, str]]:
    tokens = set(_normalized_tokens(source_text))
    ranked: list[tuple[float, CaseFile, str]] = []
    for case_file in visible_cases:
        fields = {
            "title": case_file.title,
            "company": case_file.company or "",
            "contact": case_file.primary_contact or "",
        }
        score = 0.0
        reasons: list[str] = []
        for label, value in fields.items():
            if not value:
                continue
            field_tokens = set(_normalized_tokens(value))
            overlap = tokens & field_tokens
            if overlap:
                increment = float(len(overlap))
                score += increment
                reasons.append(f"matched {label}: {', '.join(sorted(overlap))}")
        if score == 0:
            continue
        ranked.append((score, case_file, "; ".join(reasons)))
    ranked.sort(key=lambda item: (-item[0], -item[1].last_activity_at.timestamp()))
    return ranked


def _subject_suggestion_for_cases(
    mail_metadata: ArtifactMailMetadata | None,
    visible_cases: Sequence[CaseFile],
    now: datetime,
) -> AssignmentSuggestion | None:
    if mail_metadata is None or not mail_metadata.subject:
        return None

    ranked: list[tuple[float, CaseFile]] = []
    normalized_subject = _normalize_subject(mail_metadata.subject)
    if not normalized_subject:
        return None

    for case_file in visible_cases:
        score = _subject_match_score(normalized_subject, case_file.title)
        if score <= 0:
            continue
        ranked.append((score, case_file))

    if not ranked:
        return None

    ranked.sort(key=lambda item: (-item[0], -item[1].last_activity_at.timestamp()))
    best_score, best_case = ranked[0]
    runner_up_score = ranked[1][0] if len(ranked) > 1 else None
    if best_score < 85.0:
        return None
    if runner_up_score is not None and (best_score - runner_up_score) < 7.0:
        return None

    return AssignmentSuggestion(
        artifact_id=mail_metadata.artifact_id,
        suggested_case_id=best_case.id,
        summary_reason=(
            f'subject matched case title "{best_case.title}" with score {best_score:.0f}'
        ),
        confidence=round(best_score / 100.0, 3),
        created_at=now,
    )


def _subject_match_score(subject: str, case_title: str) -> float:
    subject_tokens = _normalized_tokens(subject)
    case_tokens = _normalized_tokens(case_title)
    if not subject_tokens or not case_tokens:
        return 0.0

    subject_set = set(subject_tokens)
    case_set = set(case_tokens)
    if case_set.issubset(subject_set):
        return 100.0

    overlap_ratio = len(subject_set & case_set) / len(case_set)
    sequence_ratio = SequenceMatcher(
        None,
        " ".join(sorted(subject_tokens)),
        " ".join(sorted(case_tokens)),
    ).ratio()
    return max(overlap_ratio * 100.0, sequence_ratio * 100.0)


def _normalize_subject(subject: str) -> str:
    normalized = subject.strip()
    while True:
        stripped = re.sub(r"^(re|fw|fwd|aw)\s*:\s*", "", normalized, flags=re.IGNORECASE)
        if stripped == normalized:
            break
        normalized = stripped.strip()
    return normalized


def _normalized_tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    forms = {
        normalized,
        _strip_diacritics(normalized),
        _german_transliteration(normalized),
    }
    tokens: set[str] = set()
    for form in forms:
        tokens.update(WORD_RE.findall(form))
    return tuple(sorted(tokens))


def _strip_diacritics(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def _german_transliteration(value: str) -> str:
    return value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def _participant_from_address(raw_value: str | None) -> MailParticipant | None:
    if not raw_value:
        return None
    name, email = parseaddr(raw_value)
    normalized_name = name.strip() or None
    normalized_email = email.strip().lower() or None
    if normalized_name is None and normalized_email is None:
        return None
    return MailParticipant(name=normalized_name, email=normalized_email)


def _coerce_optional_str(value_or_callable) -> str | None:
    value = value_or_callable() if callable(value_or_callable) else value_or_callable
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    return value.lower()


def _mail_metadata_search_text(mail_metadata: ArtifactMailMetadata | None) -> str:
    if mail_metadata is None:
        return ""
    fragments = [
        mail_metadata.subject or "",
        mail_metadata.sender_name or "",
        mail_metadata.sender_email or "",
        mail_metadata.sender_domain or "",
    ]
    for recipient in mail_metadata.recipients:
        fragments.append(recipient.name or "")
        fragments.append(recipient.email or "")
    return " ".join(fragment for fragment in fragments if fragment)

"""Core domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

CaseStatus = Literal["open", "closed"]
ActivityKind = Literal["intake", "follow_up", "question", "escalation"]
DueLabel = Literal["overdue", "today", "upcoming"]
MailSourceSystem = Literal["outlook_upload", "apple_mail_client", "desktop_mail_client"]
MailMessageFormat = Literal["outlook_msg", "rfc822_email"]
ParseStatus = Literal["parsed"]


@dataclass(frozen=True, slots=True)
class UserContext:
    """Authenticated user context used for authorization-aware queries."""

    id: UUID
    email: str
    display_name: str
    profile_image_path: str | None = None
    visible_group_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class LocalUserAccount:
    """Local-first user account persisted in SQLite."""

    id: UUID
    email: str
    display_name: str
    password_hash: str
    profile_image_path: str | None = None
    visible_group_ids: frozenset[UUID] = frozenset()

    def to_user_context(self) -> UserContext:
        """Return the request-time user context."""
        return UserContext(
            id=self.id,
            email=self.email,
            display_name=self.display_name,
            profile_image_path=self.profile_image_path,
            visible_group_ids=self.visible_group_ids,
        )


@dataclass(frozen=True, slots=True)
class CaseFile:
    """Primary work object that groups activities and artifacts."""

    id: UUID
    title: str
    company: str | None
    primary_contact: str | None
    status: CaseStatus
    last_activity_at: datetime
    visible_group_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Activity:
    """Work item or Wiedervorlage attached to a case."""

    id: UUID
    case_id: UUID
    description: str
    kind: ActivityKind
    due_at: datetime
    created_at: datetime
    created_by: UUID | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Artifact:
    """Incoming file or communication item to be assigned to a case."""

    id: UUID
    file_name: str
    media_type: str
    size_bytes: int
    content_text: str
    storage_key: str
    uploaded_at: datetime
    uploaded_by: UUID | None = None
    assigned_case_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MailParticipant:
    """One sender or recipient extracted from an email artifact."""

    name: str | None
    email: str | None


@dataclass(frozen=True, slots=True)
class ArtifactMailMetadata:
    """Parsed email envelope data stored alongside an artifact."""

    artifact_id: UUID
    source_system: MailSourceSystem
    message_format: MailMessageFormat
    parse_status: ParseStatus
    external_message_id: str | None
    rfc_message_id: str | None
    source_account: str | None
    source_mailbox: str | None
    subject: str | None
    sender_name: str | None
    sender_email: str | None
    sender_domain: str | None
    recipients: tuple[MailParticipant, ...]
    sent_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExtractedArtifactData:
    """Normalized output from an artifact extractor."""

    message_format: MailMessageFormat
    parse_status: ParseStatus
    rfc_message_id: str | None
    content_text: str
    subject: str | None
    sender: MailParticipant | None
    recipients: tuple[MailParticipant, ...]
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class MailSelector:
    """Criteria used to ask a mail source for review candidates."""

    account_name: str | None = None
    mailbox_name: str | None = None
    unread_only: bool = True
    sender_filter: str = ""
    subject_filter: str = ""
    sent_after: datetime | None = None
    result_limit: int = 25


@dataclass(frozen=True, slots=True)
class MailCandidate:
    """One reviewable message exposed by a mail import source."""

    candidate_id: str
    source_system: MailSourceSystem
    account_name: str | None
    mailbox_name: str | None
    subject: str | None
    sender_name: str | None
    sender_email: str | None
    sent_at: datetime | None
    preview_text: str
    unread: bool
    rfc_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ImportedMailPayload:
    """Raw message payload returned by a mail source for ingestion."""

    source_system: MailSourceSystem
    external_message_id: str
    rfc_message_id: str | None
    account_name: str | None
    mailbox_name: str | None
    file_name: str
    media_type: str
    content: bytes
    unread: bool


@dataclass(frozen=True, slots=True)
class AssignmentSuggestion:
    """Single-case suggestion from the intake agent."""

    artifact_id: UUID
    suggested_case_id: UUID | None
    summary_reason: str
    confidence: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Fallback search result shown after the initial suggestion is rejected."""

    case_id: UUID
    title: str
    company: str | None
    last_activity_at: datetime
    reason: str
    score: float


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Auditable workflow event."""

    id: UUID
    actor_user_id: UUID | None
    event_type: str
    subject_id: UUID
    payload_json: dict[str, object]
    created_at: datetime

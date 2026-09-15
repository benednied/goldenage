"""Application ports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    ExtractedArtifactData,
    ImportedMailPayload,
    MailCandidate,
    MailConversation,
    MailMessage,
    MailSelector,
    MailSourceSystem,
    SearchResult,
    UserContext,
)


class CaseRepository(Protocol):
    """Persistence port for cases."""

    def list_cases(self, user: UserContext) -> Sequence[CaseFile]:
        """Return visible cases for the user."""

    def get_case(self, case_id: UUID, user: UserContext) -> CaseFile | None:
        """Return one visible case."""

    def save_case(self, case_file: CaseFile) -> None:
        """Persist a case."""


class ActivityRepository(Protocol):
    """Persistence port for activities."""

    def list_due_activities(self, user: UserContext, now: datetime) -> Sequence[Activity]:
        """Return incomplete activities relevant for the dashboard."""

    def list_case_activities(self, case_id: UUID, user: UserContext) -> Sequence[Activity]:
        """Return activities for one case."""

    def get_activity(self, activity_id: UUID, user: UserContext) -> Activity | None:
        """Return one activity if visible to the user."""

    def save_activity(self, activity: Activity) -> None:
        """Persist an activity."""


class ArtifactRepository(Protocol):
    """Persistence port for artifacts and suggestions."""

    def save_artifact(self, artifact: Artifact) -> None:
        """Persist an artifact."""

    def get_artifact(self, artifact_id: UUID, user: UserContext) -> Artifact | None:
        """Return one visible artifact."""

    def list_case_artifacts(self, case_id: UUID, user: UserContext) -> Sequence[Artifact]:
        """Return artifacts linked to a case."""

    def list_unassigned_artifacts(
        self,
        user: UserContext,
        *,
        limit: int,
    ) -> Sequence[Artifact]:
        """Return recent artifacts that are still waiting for case assignment."""

    def save_suggestion(self, suggestion: AssignmentSuggestion) -> None:
        """Persist the current suggestion for an artifact."""

    def get_suggestion(
        self,
        artifact_id: UUID,
        user: UserContext,
    ) -> AssignmentSuggestion | None:
        """Return the stored suggestion for an artifact."""

    def save_mail_metadata(self, metadata: ArtifactMailMetadata) -> None:
        """Persist parsed mail metadata for an artifact."""

    def get_mail_metadata(
        self,
        artifact_id: UUID,
        user: UserContext,
    ) -> ArtifactMailMetadata | None:
        """Return parsed mail metadata for an artifact."""

    def save_mail_conversation(self, conversation: MailConversation) -> None:
        """Persist a mail conversation."""

    def get_mail_conversation(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> MailConversation | None:
        """Return one visible mail conversation."""

    def list_recent_mail_conversations(
        self,
        user: UserContext,
        *,
        limit: int = 10,
    ) -> Sequence[MailConversation]:
        """Return recent visible mail conversations."""

    def save_mail_message(self, message: MailMessage) -> None:
        """Persist source metadata for a mail artifact."""

    def get_mail_message(self, artifact_id: UUID, user: UserContext) -> MailMessage | None:
        """Return source metadata for a visible mail artifact."""

    def find_mail_message_by_source(
        self,
        *,
        source_kind: str,
        source_account_id: str | None,
        source_folder_id: str | None,
        source_message_id: str | None,
        internet_message_id: str | None,
        dedupe_fingerprint: str,
        user: UserContext,
    ) -> MailMessage | None:
        """Return an existing source message for dedupe checks."""

    def list_conversation_artifacts(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> Sequence[Artifact]:
        """Return artifacts belonging to a visible mail conversation."""


class AuditRepository(Protocol):
    """Persistence port for audit events."""

    def save_event(self, event: AuditEvent) -> None:
        """Persist an audit event."""


class ArtifactStore(Protocol):
    """Binary storage port."""

    def store(self, artifact_id: UUID, file_name: str, content: bytes) -> str:
        """Persist original bytes under a server-owned key without overwriting.

        file_name is untrusted metadata and must not determine a filesystem path.
        """


class ArtifactContentExtractor(Protocol):
    """Artifact extraction port."""

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        """Extract analyzable content and envelope metadata from an artifact."""


class MailImportClient(Protocol):
    """Read-only client for mailbox-backed candidate discovery and fetch."""

    def search_candidates(self, selector: MailSelector) -> Sequence[MailCandidate]:
        """Return review candidates matching the selector."""

    def fetch_message(self, candidate_id: str) -> ImportedMailPayload:
        """Return one raw message payload for ingestion."""


class MailboxSource(Protocol):
    """Long-running local mailbox source."""

    def watch_forever(self) -> None:
        """Watch the mailbox and invoke its callback for new messages."""

    def stop(self) -> None:
        """Stop watching the mailbox."""


class MailImportRepository(Protocol):
    """Persistence for selector, review queue, and dedupe state."""

    def upsert_source(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        now: datetime,
    ) -> None:
        """Persist that the user has configured or used a mail source."""

    def save_selector(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        selector: MailSelector,
        now: datetime,
    ) -> None:
        """Persist the last selector used for a mail source."""

    def get_selector(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
    ) -> MailSelector | None:
        """Return the last saved selector, if any."""

    def replace_review_candidates(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidates: Sequence[MailCandidate],
        now: datetime,
    ) -> None:
        """Replace the current review queue for a mail source."""

    def list_review_candidates(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
    ) -> Sequence[MailCandidate]:
        """Return the stored review queue for a mail source."""

    def get_review_candidate(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidate_id: str,
    ) -> MailCandidate | None:
        """Return one stored review candidate."""

    def discard_review_candidate(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidate_id: str,
    ) -> None:
        """Remove one review candidate from the queue."""

    def list_imported_message_ids(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
    ) -> frozenset[str]:
        """Return previously imported external message ids for dedupe."""

    def save_imported_message(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        external_message_id: str,
        rfc_message_id: str | None,
        artifact_id: UUID,
        now: datetime,
    ) -> None:
        """Persist the mapping from a source message to the created artifact."""


class GiselaClient(Protocol):
    """Single-suggestion intake agent."""

    def analyze_artifact(
        self,
        artifact: Artifact,
        mail_metadata: ArtifactMailMetadata | None,
        visible_cases: Sequence[CaseFile],
        now: datetime,
    ) -> AssignmentSuggestion:
        """Generate one suggested case assignment."""


class ElizabethanSearchClient(Protocol):
    """Fallback search agent."""

    def search_cases(
        self,
        query: str,
        artifact: Artifact,
        mail_metadata: ArtifactMailMetadata | None,
        visible_cases: Sequence[CaseFile],
        now: datetime,
    ) -> Sequence[SearchResult]:
        """Search for matching cases using bounded search tools."""

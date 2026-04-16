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
    MailConversation,
    MailMessage,
    MailboxAccountConfig,
    MailboxSyncCheckpoint,
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
        """Return recent visible conversations for the intake panel."""

    def save_mail_message(self, message: MailMessage) -> None:
        """Persist one mail message row."""

    def get_mail_message(
        self,
        artifact_id: UUID,
        user: UserContext,
    ) -> MailMessage | None:
        """Return a visible mail message."""

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
        """Find an existing ingested message by source identity."""

    def list_conversation_artifacts(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> Sequence[Artifact]:
        """Return the artifacts linked to a visible conversation."""

    def save_mailbox_account_config(self, config: MailboxAccountConfig) -> None:
        """Persist local mailbox account configuration."""

    def get_active_mailbox_account_config(
        self,
        user: UserContext,
    ) -> MailboxAccountConfig | None:
        """Return the active mailbox account for the user if configured."""

    def save_mailbox_sync_checkpoint(self, checkpoint: MailboxSyncCheckpoint) -> None:
        """Persist one folder sync checkpoint."""

    def list_mailbox_sync_checkpoints(
        self,
        account_config_id: UUID,
    ) -> Sequence[MailboxSyncCheckpoint]:
        """Return saved checkpoints for one configured mailbox."""


class AuditRepository(Protocol):
    """Persistence port for audit events."""

    def save_event(self, event: AuditEvent) -> None:
        """Persist an audit event."""


class ArtifactStore(Protocol):
    """Binary storage port."""

    def store(self, artifact_id: UUID, file_name: str, content: bytes) -> str:
        """Persist the original file bytes and return the storage key."""


class ArtifactContentExtractor(Protocol):
    """Artifact extraction port."""

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        """Extract analyzable content and envelope metadata from an artifact."""


class MailboxSource(Protocol):
    """Live local mailbox integration."""

    def watch_forever(self) -> None:
        """Start the mailbox event loop."""


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

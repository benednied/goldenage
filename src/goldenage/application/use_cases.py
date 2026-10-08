"""Application services."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from goldenage.application.ports import (
    ActivityRepository,
    ArtifactContentExtractor,
    ArtifactRepository,
    ArtifactStore,
    AuditRepository,
    CaseRepository,
    ElizabethanSearchClient,
    GiselaClient,
    MailboxRecoveryRepository,
    MailImportClient,
    MailImportRepository,
)
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    ExtractedArtifactData,
    MailboxRecovery,
    MailCandidate,
    MailConversation,
    MailMessage,
    MailParticipant,
    MailSelector,
    MailSourceSystem,
    SearchResult,
    UserContext,
)
from goldenage.domain.rules import ResolutionError, build_resolution_plan, due_label

DESKTOP_MAIL_SOURCE: MailSourceSystem = "desktop_mail_client"
LEGACY_APPLE_MAIL_SOURCE: MailSourceSystem = "apple_mail_client"
MessageKind = Literal["info", "error"]


class NotFoundError(LookupError):
    """Raised when a visible entity cannot be found."""


@dataclass(frozen=True, slots=True)
class WorklistItem:
    """Dashboard row combining activity and case context."""

    activity: Activity
    case_file: CaseFile
    due_status: str


@dataclass(frozen=True, slots=True)
class CaseDetail:
    """Detail panel for one case."""

    case_file: CaseFile
    active_activity: Activity | None
    open_activities: tuple[Activity, ...]
    recent_artifacts: tuple[Artifact, ...]
    completed_activities: tuple[Activity, ...] = ()
    history: tuple["HistoryItem", ...] = ()
    artifact_offset: int = 0
    artifact_limit: int = 5
    has_older_artifacts: bool = False
    has_newer_artifacts: bool = False
    history_offset: int = 0
    history_limit: int = 20
    has_older_history: bool = False
    has_newer_history: bool = False
    case_state_label: str = "Open case"
    selected_artifact: Artifact | None = None
    selected_mail_metadata: ArtifactMailMetadata | None = None
    selected_mail_message: MailMessage | None = None
    selected_conversation: MailConversation | None = None
    selected_conversation_artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True, slots=True)
class IntakeState:
    """UI state for the intake panel."""

    artifact: Artifact | None = None
    suggestion: AssignmentSuggestion | None = None
    search_mode: bool = False
    search_query: str = ""
    search_results: tuple[SearchResult, ...] = ()
    message: str | None = None
    message_kind: MessageKind = "info"
    conversation: MailConversation | None = None
    conversation_artifacts: tuple[Artifact, ...] = ()
    recent_conversations: tuple[MailConversation, ...] = ()
    ingest_status: str | None = None
    new_case_title_suggestion: str = ""


@dataclass(frozen=True, slots=True)
class MailImportState:
    """UI state for the desktop mail selector and review queue."""

    enabled: bool = False
    selector: MailSelector = field(default_factory=MailSelector)
    candidates: tuple[MailCandidate, ...] = ()
    message: str | None = None
    message_kind: MessageKind = "info"
    recoveries: tuple[MailboxRecovery, ...] = ()
    worker_status: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """Presentation-safe audit entry for a visible case."""

    event: AuditEvent
    actor_label: str
    action: str
    reason: str | None
    detail: str | None


CASE_PAGE_SIZE = 5
HISTORY_PAGE_SIZE = 20
RECOVERY_PAGE_SIZE = 10


class GoldenAgeService:
    """Main application orchestration service."""

    def __init__(
        self,
        *,
        case_repository: CaseRepository,
        activity_repository: ActivityRepository,
        artifact_repository: ArtifactRepository,
        audit_repository: AuditRepository,
        artifact_store: ArtifactStore,
        content_extractor: ArtifactContentExtractor,
        gisela_client: GiselaClient,
        elizabethan_client: ElizabethanSearchClient,
        mail_import_client: MailImportClient | None = None,
        mail_import_repository: MailImportRepository | None = None,
        recovery_repository: MailboxRecoveryRepository | None = None,
    ) -> None:
        self._case_repository = case_repository
        self._activity_repository = activity_repository
        self._artifact_repository = artifact_repository
        self._audit_repository = audit_repository
        self._artifact_store = artifact_store
        self._content_extractor = content_extractor
        self._gisela_client = gisela_client
        self._elizabethan_client = elizabethan_client
        self._mail_import_client = mail_import_client
        self._mail_import_repository = mail_import_repository
        self._recovery_repository = recovery_repository

    def get_today_worklist(self, *, user: UserContext, now: datetime) -> tuple[WorklistItem, ...]:
        """Return due activities sorted by urgency and timestamp."""
        due_activities = self._activity_repository.list_due_activities(user, now)
        visible_cases = {
            case_file.id: case_file for case_file in self._case_repository.list_cases(user)
        }
        items: list[WorklistItem] = []
        for activity in due_activities:
            case_file = visible_cases.get(activity.case_id)
            if case_file is None:
                continue
            items.append(
                WorklistItem(
                    activity=activity,
                    case_file=case_file,
                    due_status=due_label(activity.due_at, now),
                )
            )
        return tuple(sorted(items, key=lambda item: (item.activity.due_at, item.case_file.title)))

    def get_case_detail(
        self,
        *,
        case_id: UUID,
        user: UserContext,
        now: datetime,
        selected_artifact_id: UUID | None = None,
        artifact_offset: int = 0,
        history_offset: int = 0,
    ) -> CaseDetail:
        """Build detail state for one case."""
        del now
        case_file = self._case_repository.get_case(case_id, user)
        if case_file is None:
            raise NotFoundError("Case not found.")

        activities = tuple(
            sorted(
                self._activity_repository.list_case_activities(case_id, user),
                key=lambda activity: activity.due_at,
            )
        )
        open_activities = tuple(
            activity for activity in activities if activity.completed_at is None
        )
        active_activity = open_activities[0] if open_activities else None
        artifact_offset = _bounded_offset(artifact_offset)
        history_offset = _bounded_offset(history_offset)
        artifact_page = self._case_artifact_page(
            case_id,
            user,
            offset=artifact_offset,
            limit=CASE_PAGE_SIZE,
        )
        history_events = self._case_history_events(
            case_id,
            user,
            offset=history_offset,
            limit=HISTORY_PAGE_SIZE + 1,
        )
        has_older_history = len(history_events) > HISTORY_PAGE_SIZE
        history_events = tuple(history_events[:HISTORY_PAGE_SIZE])
        history = tuple(_history_item(event, user) for event in history_events)
        has_newer_history = history_offset > 0
        has_older_artifacts = len(artifact_page) > CASE_PAGE_SIZE
        artifact_page = tuple(artifact_page[:CASE_PAGE_SIZE])
        has_newer_artifacts = artifact_offset > 0
        selected_artifact = None
        selected_mail_metadata = None
        selected_mail_message = None
        selected_conversation = None
        selected_conversation_artifacts: tuple[Artifact, ...] = ()
        if selected_artifact_id is not None:
            selected_artifact = self._artifact_repository.get_artifact(selected_artifact_id, user)
            if selected_artifact is None or selected_artifact.assigned_case_id != case_id:
                raise NotFoundError("Artifact not found.")
            selected_mail_metadata = self._artifact_repository.get_mail_metadata(
                selected_artifact_id,
                user,
            )
            selected_mail_message = self._artifact_repository.get_mail_message(
                selected_artifact_id,
                user,
            )
            if selected_mail_message is not None:
                selected_conversation = self._artifact_repository.get_mail_conversation(
                    selected_mail_message.conversation_id,
                    user,
                )
                selected_conversation_artifacts = tuple(
                    self._artifact_repository.list_conversation_artifacts(
                        selected_mail_message.conversation_id,
                        user,
                    )
                )

        return CaseDetail(
            case_file=case_file,
            active_activity=active_activity,
            open_activities=open_activities,
            recent_artifacts=tuple(artifact_page),
            completed_activities=tuple(
                activity for activity in activities if activity.completed_at is not None
            ),
            history=history,
            artifact_offset=artifact_offset,
            has_older_artifacts=has_older_artifacts,
            has_newer_artifacts=has_newer_artifacts,
            history_offset=history_offset,
            has_older_history=has_older_history,
            has_newer_history=has_newer_history,
            case_state_label=_case_state_label(case_file, open_activities),
            selected_artifact=selected_artifact,
            selected_mail_metadata=selected_mail_metadata,
            selected_mail_message=selected_mail_message,
            selected_conversation=selected_conversation,
            selected_conversation_artifacts=selected_conversation_artifacts,
        )

    def list_case_history(
        self,
        *,
        case_id: UUID,
        user: UserContext,
        offset: int = 0,
        limit: int = HISTORY_PAGE_SIZE,
    ) -> tuple[HistoryItem, ...]:
        """Return a bounded, permission-filtered case history page."""
        if self._case_repository.get_case(case_id, user) is None:
            raise NotFoundError("Case not found.")
        bounded_limit = min(max(limit, 0), HISTORY_PAGE_SIZE)
        events = self._case_history_events(
            case_id,
            user,
            offset=_bounded_offset(offset),
            limit=bounded_limit,
        )
        return tuple(_history_item(event, user) for event in events)

    def _case_history_events(
        self,
        case_id: UUID,
        user: UserContext,
        *,
        offset: int,
        limit: int,
    ) -> tuple[AuditEvent, ...]:
        list_method = getattr(self._audit_repository, "list_case_events", None)
        if not callable(list_method):
            return ()
        return tuple(list_method(case_id, user, offset=offset, limit=limit))

    def get_mail_recovery_state(self, *, user: UserContext) -> tuple[MailboxRecovery, ...]:
        """Return failed imports the caller is authorized to retry."""
        if self._recovery_repository is None:
            return ()
        return tuple(self._recovery_repository.list_recoveries(user, limit=RECOVERY_PAGE_SIZE))

    def get_case_detail_for_activity(
        self,
        *,
        activity_id: UUID,
        user: UserContext,
        now: datetime,
    ) -> CaseDetail:
        """Resolve a detail panel by activity id."""
        activity = self._activity_repository.get_activity(activity_id, user)
        if activity is None:
            raise NotFoundError("Activity not found.")
        return self.get_case_detail(case_id=activity.case_id, user=user, now=now)

    def list_unassigned_intake(
        self,
        *,
        user: UserContext,
        limit: int = 10,
    ) -> tuple[Artifact, ...]:
        """Return recent intake artifacts that have not been assigned yet."""
        return tuple(
            self._artifact_repository.list_unassigned_artifacts(
                user,
                limit=limit,
            )
        )

    def resolve_activity(
        self,
        *,
        activity_id: UUID,
        user: UserContext,
        now: datetime,
        next_step: str | None,
        next_due_at: datetime | None,
        close_case: bool,
        skip_follow_up: bool,
    ) -> CaseDetail:
        """Resolve one due activity while enforcing a clear next state."""
        activity = self._activity_repository.get_activity(activity_id, user)
        if activity is None:
            raise NotFoundError("Activity not found.")

        plan = build_resolution_plan(
            activity,
            now=now,
            actor_user_id=user.id,
            next_step=next_step,
            next_due_at=next_due_at,
            close_case=close_case,
            skip_follow_up=skip_follow_up,
        )

        self._activity_repository.save_activity(replace(activity, completed_at=plan.completed_at))
        if plan.follow_up_activity is not None:
            self._activity_repository.save_activity(plan.follow_up_activity)

        case_file = self._case_repository.get_case(activity.case_id, user)
        if case_file is None:
            raise NotFoundError("Case not found.")

        new_status = "closed" if plan.close_case else "open"
        self._case_repository.save_case(replace(case_file, status=new_status, last_activity_at=now))

        self._record_audit(
            actor_user_id=user.id,
            event_type="activity_resolved",
            subject_id=activity.id,
            payload={
                "case_id": str(activity.case_id),
                "close_case": plan.close_case,
                "skip_follow_up": plan.skip_follow_up,
                "activity_description": activity.description,
                "next_step": next_step.strip() if next_step else None,
                "next_due_at": next_due_at.isoformat() if next_due_at else None,
                "reason": "No follow-up required." if plan.skip_follow_up else None,
                "follow_up_activity_id": (
                    str(plan.follow_up_activity.id) if plan.follow_up_activity else None
                ),
            },
            now=now,
        )

        return self.get_case_detail(case_id=activity.case_id, user=user, now=now)

    def upload_artifact(
        self,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
        user: UserContext,
        now: datetime,
    ) -> IntakeState:
        """Store an uploaded artifact and generate the initial assignment suggestion."""
        return self._ingest_mail_artifact(
            file_name=file_name,
            media_type=media_type,
            content=content,
            source_system="outlook_upload",
            user=user,
            now=now,
            external_message_id=None,
            rfc_message_id=None,
            source_account=None,
            source_mailbox=None,
            audit_event_type="artifact_uploaded",
        )

    def get_mail_import_state(
        self,
        *,
        user: UserContext,
        message: str | None = None,
        message_kind: MessageKind = "info",
    ) -> MailImportState:
        """Load persisted desktop mail selector state and review queue."""
        if self._mail_import_client is None or self._mail_import_repository is None:
            return MailImportState(recoveries=self.get_mail_recovery_state(user=user))
        selector = self._mail_selector_for_user(user)
        candidates = self._mail_import_repository.list_review_candidates(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
        )
        return MailImportState(
            enabled=True,
            selector=selector,
            candidates=tuple(candidates),
            message=message,
            message_kind=message_kind,
            recoveries=self.get_mail_recovery_state(user=user),
        )

    def get_mail_selector_settings(self, *, user: UserContext) -> MailSelector:
        """Return persisted desktop mail defaults for settings forms."""
        if self._mail_import_repository is None:
            return MailSelector()
        return self._mail_selector_for_user(user)

    def save_mail_selector_settings(
        self,
        *,
        selector: MailSelector,
        user: UserContext,
        now: datetime,
    ) -> MailSelector:
        """Persist desktop mail defaults without querying the mailbox."""
        if self._mail_import_repository is None:
            raise NotFoundError("Mail settings are not available in this runtime.")
        normalized_selector = _normalize_mail_selector(selector)
        self._mail_import_repository.upsert_source(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            now=now,
        )
        self._mail_import_repository.save_selector(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            selector=normalized_selector,
            now=now,
        )
        return normalized_selector

    def search_mail_candidates(
        self,
        *,
        selector: MailSelector,
        user: UserContext,
        now: datetime,
    ) -> MailImportState:
        """Query desktop mail for selector matches and persist the review queue."""
        if self._mail_import_client is None or self._mail_import_repository is None:
            return MailImportState(message="Mail import is not available in this runtime.")

        normalized_selector = _normalize_mail_selector(selector)
        imported_ids = self._mail_import_repository.list_imported_message_ids(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
        )
        source_candidates = tuple(self._mail_import_client.search_candidates(normalized_selector))
        candidates = tuple(
            candidate
            for candidate in source_candidates
            if candidate.candidate_id not in imported_ids
            and (candidate.rfc_message_id is None or candidate.rfc_message_id not in imported_ids)
        )
        imported_match_count = len(source_candidates) - len(candidates)
        self._mail_import_repository.upsert_source(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            now=now,
        )
        self._mail_import_repository.save_selector(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            selector=normalized_selector,
            now=now,
        )
        self._mail_import_repository.replace_review_candidates(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            candidates=candidates,
            now=now,
        )
        self._record_audit(
            actor_user_id=user.id,
            event_type="desktop_mail_candidates_loaded",
            subject_id=user.id,
            payload={
                "candidate_count": len(candidates),
                "imported_match_count": imported_match_count,
                "account_name": normalized_selector.account_name,
                "mailbox_name": normalized_selector.mailbox_name,
                "unread_only": normalized_selector.unread_only,
                "sender_filter": normalized_selector.sender_filter,
                "subject_filter": normalized_selector.subject_filter,
            },
            now=now,
        )
        if not candidates:
            if imported_match_count:
                return self.get_mail_import_state(
                    user=user,
                    message=_already_imported_message(imported_match_count),
                    message_kind="error",
                )
            return self.get_mail_import_state(
                user=user,
                message="No mail messages matched the current selector.",
            )
        if imported_match_count:
            return self.get_mail_import_state(
                user=user,
                message=(
                    f"Mail candidates loaded. {_already_imported_message(imported_match_count)}"
                ),
                message_kind="error",
            )
        return self.get_mail_import_state(
            user=user,
            message="Mail candidates loaded. Import the messages you want to triage.",
        )

    def import_mail_candidate(
        self,
        *,
        candidate_id: str,
        user: UserContext,
        now: datetime,
    ) -> IntakeState:
        """Fetch one desktop mail candidate and ingest it through the shared mail flow."""
        if self._mail_import_client is None or self._mail_import_repository is None:
            raise NotFoundError("Mail import is not available.")
        candidate = self._mail_import_repository.get_review_candidate(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            candidate_id=candidate_id,
        )
        if candidate is None:
            raise NotFoundError("Mail candidate not found.")

        payload = self._mail_import_client.fetch_message(candidate.candidate_id)
        imported_ids = self._mail_import_repository.list_imported_message_ids(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
        )
        if payload.external_message_id in imported_ids or (
            payload.rfc_message_id is not None and payload.rfc_message_id in imported_ids
        ):
            raise ResolutionError("This mail message was already imported.")

        intake_state = self._ingest_mail_artifact(
            file_name=payload.file_name,
            media_type=payload.media_type,
            content=payload.content,
            source_system=payload.source_system,
            user=user,
            now=now,
            external_message_id=payload.external_message_id,
            rfc_message_id=payload.rfc_message_id,
            source_account=payload.account_name,
            source_mailbox=payload.mailbox_name,
            audit_event_type="desktop_mail_message_imported",
        )
        if intake_state.artifact is None:
            raise RuntimeError("Imported mail did not create an artifact.")
        self._mail_import_repository.save_imported_message(
            user=user,
            source_system=payload.source_system,
            external_message_id=payload.external_message_id,
            rfc_message_id=payload.rfc_message_id,
            artifact_id=intake_state.artifact.id,
            now=now,
        )
        self._mail_import_repository.discard_review_candidate(
            user=user,
            source_system=DESKTOP_MAIL_SOURCE,
            candidate_id=candidate_id,
        )
        return IntakeState(
            artifact=intake_state.artifact,
            suggestion=intake_state.suggestion,
            search_mode=intake_state.search_mode,
            search_query=intake_state.search_query,
            search_results=intake_state.search_results,
            message="Mail message imported. Confirm or reject the proposed case.",
            conversation=intake_state.conversation,
            conversation_artifacts=intake_state.conversation_artifacts,
        )

    def ingest_mail(
        self,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
        extracted: ExtractedArtifactData,
        user: UserContext,
        now: datetime,
    ) -> IntakeState:
        """Ingest a normalized mailbox message."""
        try:
            return self._ingest_mail_artifact(
                file_name=file_name,
                media_type=media_type,
                content=content,
                source_system=DESKTOP_MAIL_SOURCE,
                user=user,
                now=now,
                external_message_id=extracted.source_message_id,
                rfc_message_id=extracted.internet_message_id or extracted.rfc_message_id,
                source_account=extracted.source_account_id,
                source_mailbox=extracted.source_folder_id,
                audit_event_type="mailbox_message_ingested",
                extracted=extracted,
            )
        except Exception as error:
            self._remember_failed_import(
                file_name=file_name,
                media_type=media_type,
                content=content,
                extracted=extracted,
                user=user,
                now=now,
                error=error,
            )
            raise

    def retry_mail_import(
        self,
        *,
        recovery_id: UUID,
        user: UserContext,
        now: datetime,
    ) -> IntakeState:
        """Retry one failed import owned by the caller."""
        if self._recovery_repository is None:
            raise NotFoundError("Mail recovery is not available.")
        recovery = self._recovery_repository.get_recovery(recovery_id, user)
        if recovery is None:
            raise NotFoundError("Failed import not found.")
        extracted = _extracted_from_json(recovery.extracted_json)
        try:
            intake_state = self._ingest_mail_artifact(
                file_name=recovery.file_name,
                media_type=recovery.media_type,
                content=recovery.content,
                source_system=recovery.source_system,
                user=user,
                now=now,
                external_message_id=recovery.external_message_id,
                rfc_message_id=recovery.rfc_message_id,
                source_account=recovery.account_name,
                source_mailbox=recovery.mailbox_name,
                audit_event_type="mailbox_message_retried",
                extracted=extracted,
            )
        except Exception as error:
            self._recovery_repository.save_recovery(
                replace(
                    recovery,
                    error_message=str(error),
                    failed_at=now,
                    retry_count=recovery.retry_count + 1,
                )
            )
            raise
        self._recovery_repository.save_recovery(
            replace(
                recovery,
                status="recovered",
                recovered_artifact_id=(intake_state.artifact.id if intake_state.artifact else None),
                recovered_at=now,
                failed_at=now,
            )
        )
        return intake_state

    def get_recent_intake(self, *, user: UserContext, limit: int = 10) -> IntakeState:
        """Return recent mail conversations for the intake panel."""
        return IntakeState(
            recent_conversations=tuple(
                self._artifact_repository.list_recent_mail_conversations(user, limit=limit)
            )
        )

    def get_intake_state_for_conversation(
        self,
        *,
        conversation_id: UUID,
        user: UserContext,
    ) -> IntakeState:
        """Load intake state for one conversation."""
        conversation = self._artifact_repository.get_mail_conversation(conversation_id, user)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        artifacts = tuple(
            self._artifact_repository.list_conversation_artifacts(conversation_id, user)
        )
        artifact = artifacts[0] if artifacts else None
        suggestion = (
            self._artifact_repository.get_suggestion(artifact.id, user)
            if artifact is not None
            else None
        )
        artifact_state = (
            self._intake_state_for_artifact(artifact, suggestion, user=user)
            if artifact is not None
            else IntakeState()
        )
        return IntakeState(
            artifact=artifact_state.artifact,
            suggestion=artifact_state.suggestion,
            search_mode=artifact_state.search_mode,
            search_query=artifact_state.search_query,
            search_results=artifact_state.search_results,
            message=artifact_state.message,
            message_kind=artifact_state.message_kind,
            conversation=conversation,
            conversation_artifacts=artifacts,
            recent_conversations=tuple(
                self._artifact_repository.list_recent_mail_conversations(user, limit=10)
            ),
            new_case_title_suggestion=artifact_state.new_case_title_suggestion,
        )

    def _mail_selector_for_user(self, user: UserContext) -> MailSelector:
        if self._mail_import_repository is None:
            return MailSelector()
        return (
            self._mail_import_repository.get_selector(
                user=user,
                source_system=DESKTOP_MAIL_SOURCE,
            )
            or self._mail_import_repository.get_selector(
                user=user,
                source_system=LEGACY_APPLE_MAIL_SOURCE,
            )
            or MailSelector()
        )

    def _case_artifact_page(
        self,
        case_id: UUID,
        user: UserContext,
        *,
        offset: int,
        limit: int,
    ) -> tuple[Artifact, ...]:
        page_method = getattr(self._artifact_repository, "list_case_artifacts_page", None)
        if callable(page_method):
            return tuple(
                page_method(
                    case_id,
                    user,
                    offset=offset,
                    limit=limit + 1,
                )
            )
        artifacts = sorted(
            self._artifact_repository.list_case_artifacts(case_id, user),
            key=lambda artifact: artifact.uploaded_at,
            reverse=True,
        )
        return tuple(artifacts[offset : offset + limit + 1])

    def _ingest_mail_artifact(
        self,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
        source_system: MailSourceSystem,
        user: UserContext,
        now: datetime,
        external_message_id: str | None,
        rfc_message_id: str | None,
        source_account: str | None,
        source_mailbox: str | None,
        audit_event_type: str,
        extracted: ExtractedArtifactData | None = None,
    ) -> IntakeState:
        extracted = extracted or self._content_extractor.extract(file_name, media_type, content)
        if external_message_id is not None or source_system == DESKTOP_MAIL_SOURCE:
            existing = self._existing_mail_message(
                extracted=extracted,
                source_system=source_system,
                source_account=source_account,
                source_mailbox=source_mailbox,
                external_message_id=external_message_id,
                rfc_message_id=rfc_message_id,
                user=user,
            )
            if existing is not None:
                existing_artifact = self._artifact_repository.get_artifact(
                    existing.artifact_id,
                    user,
                )
                if existing_artifact is not None:
                    state = self._intake_state_for_artifact(
                        existing_artifact,
                        self._artifact_repository.get_suggestion(existing.artifact_id, user),
                        user=user,
                    )
                    conversation = self._artifact_repository.get_mail_conversation(
                        existing.conversation_id,
                        user,
                    )
                    return replace(
                        state,
                        conversation=conversation,
                        conversation_artifacts=(
                            tuple(
                                self._artifact_repository.list_conversation_artifacts(
                                    existing.conversation_id,
                                    user,
                                )
                            )
                            if conversation is not None
                            else ()
                        ),
                    )
        artifact_id = uuid4()
        storage_key = self._artifact_store.store(artifact_id, file_name, content)
        artifact = Artifact(
            id=artifact_id,
            file_name=file_name,
            media_type=media_type,
            size_bytes=len(content),
            content_text=extracted.content_text,
            storage_key=storage_key,
            uploaded_at=now,
            uploaded_by=user.id,
        )
        self._artifact_repository.save_artifact(artifact)
        mail_metadata = _build_mail_metadata(
            artifact_id=artifact_id,
            extracted=extracted,
            source_system=source_system,
            external_message_id=external_message_id,
            rfc_message_id=rfc_message_id,
            source_account=source_account,
            source_mailbox=source_mailbox,
            now=now,
        )
        if mail_metadata is not None:
            self._artifact_repository.save_mail_metadata(mail_metadata)
        conversation = self._save_mail_thread(
            artifact=artifact,
            extracted=extracted,
            source_system=source_system,
            source_account=source_account,
            source_mailbox=source_mailbox,
            now=now,
            user=user,
        )
        suggestion = self._gisela_client.analyze_artifact(
            artifact,
            mail_metadata,
            self._case_repository.list_cases(user),
            now,
        )
        self._artifact_repository.save_suggestion(suggestion)
        self._record_audit(
            actor_user_id=user.id,
            event_type=audit_event_type,
            subject_id=artifact.id,
            payload={
                "file_name": artifact.file_name,
                "source_system": source_system,
                "subject": mail_metadata.subject if mail_metadata is not None else None,
                "external_message_id": external_message_id,
                "suggested_case_id": (
                    str(suggestion.suggested_case_id)
                    if suggestion.suggested_case_id is not None
                    else None
                ),
            },
            now=now,
        )
        intake_state = self._intake_state_for_artifact(artifact, suggestion, user=user)
        if intake_state.search_mode:
            return intake_state
        return IntakeState(
            artifact=intake_state.artifact,
            suggestion=intake_state.suggestion,
            message="Document analyzed. Confirm or reject the proposed case.",
            conversation=conversation,
            conversation_artifacts=(
                tuple(self._artifact_repository.list_conversation_artifacts(conversation.id, user))
                if conversation is not None
                else ()
            ),
            new_case_title_suggestion=intake_state.new_case_title_suggestion,
        )

    def _existing_mail_message(
        self,
        *,
        extracted: ExtractedArtifactData,
        source_system: MailSourceSystem,
        source_account: str | None,
        source_mailbox: str | None,
        external_message_id: str | None,
        rfc_message_id: str | None,
        user: UserContext,
    ) -> MailMessage | None:
        source_kind = extracted.source_kind or source_system
        return self._artifact_repository.find_mail_message_by_source(
            source_kind=source_kind,
            source_account_id=extracted.source_account_id or source_account,
            source_folder_id=extracted.source_folder_id or source_mailbox,
            source_message_id=extracted.source_message_id or external_message_id,
            internet_message_id=extracted.internet_message_id or rfc_message_id,
            dedupe_fingerprint=_dedupe_fingerprint(
                source_kind=source_kind,
                source_account_id=extracted.source_account_id or source_account,
                source_folder_id=extracted.source_folder_id or source_mailbox,
                source_message_id=extracted.source_message_id or external_message_id,
                internet_message_id=extracted.internet_message_id or rfc_message_id,
                subject=extracted.subject,
                sent_at=extracted.sent_at,
                content_text=extracted.content_text,
            ),
            user=user,
        )

    def _remember_failed_import(
        self,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
        extracted: ExtractedArtifactData,
        user: UserContext,
        now: datetime,
        error: Exception,
    ) -> None:
        if self._recovery_repository is None:
            return
        recovery = MailboxRecovery(
            id=uuid4(),
            user_id=user.id,
            source_system=DESKTOP_MAIL_SOURCE,
            external_message_id=extracted.source_message_id,
            rfc_message_id=extracted.internet_message_id or extracted.rfc_message_id,
            account_name=extracted.source_account_id,
            mailbox_name=extracted.source_folder_id,
            file_name=file_name,
            media_type=media_type,
            content=content,
            extracted_json=_extracted_to_json(extracted),
            error_message=str(error),
            status="failed",
            failed_at=now,
        )
        try:
            self._recovery_repository.save_recovery(recovery)
        except Exception:
            # Never replace the original ingestion failure with a recovery-store failure.
            return

    def get_intake_state(self, *, artifact_id: UUID, user: UserContext) -> IntakeState:
        """Load persisted intake state."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        suggestion = self._artifact_repository.get_suggestion(artifact_id, user)
        return self._intake_state_for_artifact(artifact, suggestion, user=user)

    def search_cases_for_artifact(
        self,
        *,
        artifact_id: UUID,
        query: str,
        user: UserContext,
        now: datetime,
    ) -> IntakeState:
        """Run the fallback search flow after suggestion rejection."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        suggestion = self._artifact_repository.get_suggestion(artifact_id, user)
        mail_metadata = self._artifact_repository.get_mail_metadata(artifact_id, user)
        results = self._elizabethan_client.search_cases(
            query,
            artifact,
            mail_metadata,
            self._case_repository.list_cases(user),
            now,
        )
        self._record_audit(
            actor_user_id=user.id,
            event_type="artifact_search_requested",
            subject_id=artifact.id,
            payload={"query": query, "result_count": len(results)},
            now=now,
        )
        return IntakeState(
            artifact=artifact,
            suggestion=suggestion,
            search_mode=True,
            search_query=query,
            search_results=tuple(results),
            new_case_title_suggestion=_suggest_new_case_title(mail_metadata),
        )

    def assign_artifact_to_case(
        self,
        *,
        artifact_id: UUID,
        case_id: UUID,
        next_step: str,
        next_due_at: datetime,
        user: UserContext,
        now: datetime,
    ) -> CaseDetail:
        """Confirm an artifact assignment and create the next activity."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        case_file = self._case_repository.get_case(case_id, user)
        if case_file is None:
            raise NotFoundError("Case not found.")

        normalized_step = next_step.strip()
        if not normalized_step:
            raise ResolutionError("A next step description is required.")

        updated_artifact = replace(artifact, assigned_case_id=case_id)
        self._artifact_repository.save_artifact(updated_artifact)
        mail_message = self._artifact_repository.get_mail_message(artifact_id, user)
        if mail_message is not None:
            conversation = self._artifact_repository.get_mail_conversation(
                mail_message.conversation_id,
                user,
            )
            if conversation is not None:
                self._artifact_repository.save_mail_conversation(
                    replace(conversation, assigned_case_id=case_id, updated_at=now)
                )
        self._activity_repository.save_activity(
            Activity(
                id=uuid4(),
                case_id=case_id,
                description=normalized_step,
                kind="intake",
                due_at=next_due_at,
                created_at=now,
                created_by=user.id,
            )
        )
        self._case_repository.save_case(replace(case_file, status="open", last_activity_at=now))
        self._record_audit(
            actor_user_id=user.id,
            event_type="artifact_assigned",
            subject_id=artifact_id,
            payload={
                "case_id": str(case_id),
                "next_step": normalized_step,
                "next_due_at": next_due_at.isoformat(),
                "reason": (
                    suggestion.summary_reason
                    if (suggestion := self._artifact_repository.get_suggestion(artifact_id, user))
                    is not None
                    else None
                ),
            },
            now=now,
        )
        return self.get_case_detail(case_id=case_id, user=user, now=now)

    def create_case_for_artifact(
        self,
        *,
        artifact_id: UUID,
        title: str,
        company: str,
        primary_contact: str,
        next_step: str,
        next_due_at: datetime,
        user: UserContext,
        now: datetime,
    ) -> CaseDetail:
        """Create a case from an intake artifact and schedule its first step."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        normalized_title = title.strip()
        normalized_step = next_step.strip()
        if not normalized_title:
            raise ResolutionError("A case title is required.")
        if not normalized_step:
            raise ResolutionError("A next step description is required.")
        case_id = uuid4()
        self._case_repository.save_case(
            CaseFile(
                id=case_id,
                title=normalized_title,
                company=company.strip() or None,
                primary_contact=primary_contact.strip() or None,
                status="open",
                last_activity_at=now,
            )
        )
        return self.assign_artifact_to_case(
            artifact_id=artifact_id,
            case_id=case_id,
            next_step=normalized_step,
            next_due_at=next_due_at,
            user=user,
            now=now,
        )

    def _save_mail_thread(
        self,
        *,
        artifact: Artifact,
        extracted: ExtractedArtifactData,
        source_system: MailSourceSystem,
        source_account: str | None,
        source_mailbox: str | None,
        now: datetime,
        user: UserContext,
    ) -> MailConversation | None:
        source_kind = extracted.source_kind or source_system
        external_conversation_id = extracted.conversation_id or _normalize_subject(
            extracted.subject
        )
        dedupe_fingerprint = _dedupe_fingerprint(
            source_kind=source_kind,
            source_account_id=extracted.source_account_id or source_account,
            source_folder_id=extracted.source_folder_id or source_mailbox,
            source_message_id=extracted.source_message_id,
            internet_message_id=extracted.internet_message_id or extracted.rfc_message_id,
            subject=extracted.subject,
            sent_at=extracted.sent_at,
            content_text=extracted.content_text,
        )
        existing = self._artifact_repository.find_mail_message_by_source(
            source_kind=source_kind,
            source_account_id=extracted.source_account_id or source_account,
            source_folder_id=extracted.source_folder_id or source_mailbox,
            source_message_id=extracted.source_message_id,
            internet_message_id=extracted.internet_message_id or extracted.rfc_message_id,
            dedupe_fingerprint=dedupe_fingerprint,
            user=user,
        )
        if existing is not None:
            return self._artifact_repository.get_mail_conversation(existing.conversation_id, user)

        conversation = _conversation_for_message(
            repository=self._artifact_repository,
            user=user,
            source_kind=source_kind,
            external_conversation_id=external_conversation_id,
            artifact=artifact,
            extracted=extracted,
            now=now,
        )
        self._artifact_repository.save_mail_conversation(conversation)
        self._artifact_repository.save_mail_message(
            MailMessage(
                artifact_id=artifact.id,
                conversation_id=conversation.id,
                source_kind=source_kind,
                source_account_id=extracted.source_account_id or source_account,
                source_folder_id=extracted.source_folder_id or source_mailbox,
                source_message_id=extracted.source_message_id,
                source_conversation_id=extracted.conversation_id,
                internet_message_id=extracted.internet_message_id or extracted.rfc_message_id,
                dedupe_fingerprint=dedupe_fingerprint,
                direction=extracted.direction,
                received_at=extracted.received_at,
                created_at=now,
            )
        )
        return conversation

    def _record_audit(
        self,
        *,
        actor_user_id: UUID | None,
        event_type: str,
        subject_id: UUID,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        self._audit_repository.save_event(
            AuditEvent(
                id=uuid4(),
                actor_user_id=actor_user_id,
                event_type=event_type,
                subject_id=subject_id,
                payload_json=payload,
                created_at=now,
            )
        )

    def _intake_state_for_artifact(
        self,
        artifact: Artifact,
        suggestion: AssignmentSuggestion | None,
        *,
        user: UserContext,
    ) -> IntakeState:
        mail_metadata = self._artifact_repository.get_mail_metadata(artifact.id, user)
        new_case_title_suggestion = _suggest_new_case_title(mail_metadata)
        if suggestion is None or suggestion.suggested_case_id is None:
            return IntakeState(
                artifact=artifact,
                suggestion=suggestion,
                search_mode=True,
                message="No safe single-case match was found. Use the bounded search flow.",
                new_case_title_suggestion=new_case_title_suggestion,
            )
        return IntakeState(
            artifact=artifact,
            suggestion=suggestion,
            new_case_title_suggestion=new_case_title_suggestion,
        )


def _bounded_offset(offset: int) -> int:
    """Keep URL-driven navigation finite and predictable."""
    return min(max(offset, 0), 10_000)


def _case_state_label(case_file: CaseFile, open_activities: tuple[Activity, ...]) -> str:
    if case_file.status == "closed":
        return "Closed case"
    if open_activities:
        return "Open case"
    return "Open case · no follow-up scheduled"


def _history_item(event: AuditEvent, user: UserContext) -> HistoryItem:
    payload = event.payload_json
    if event.actor_user_id is None:
        actor_label = "System"
    elif event.actor_user_id == user.id:
        actor_label = user.display_name
    else:
        actor_label = str(event.actor_user_id)

    close_case = payload.get("close_case") is True
    action = {
        "activity_resolved": "Closed case" if close_case else "Completed activity",
        "artifact_assigned": "Assigned intake",
        "artifact_uploaded": "Uploaded intake",
        "mailbox_message_ingested": "Imported mailbox message",
        "mailbox_message_retried": "Retried mailbox message",
        "desktop_mail_message_imported": "Imported desktop mail",
        "artifact_search_requested": "Searched for a case",
    }.get(event.event_type, event.event_type.replace("_", " ").capitalize())
    reason_value = payload.get("reason")
    reason = reason_value if isinstance(reason_value, str) and reason_value else None
    if reason is None and payload.get("skip_follow_up") is True:
        reason = "No follow-up required."
    detail_value = (
        payload.get("next_step")
        or payload.get("activity_description")
        or payload.get("query")
        or payload.get("summary_reason")
    )
    detail = detail_value if isinstance(detail_value, str) and detail_value else None
    return HistoryItem(
        event=event,
        actor_label=actor_label,
        action=action,
        reason=reason,
        detail=detail,
    )


def _extracted_to_json(extracted: ExtractedArtifactData) -> dict[str, object]:
    """Serialize normalized mail data for a durable retry record."""
    sender = None
    if extracted.sender is not None:
        sender = {"name": extracted.sender.name, "email": extracted.sender.email}
    return {
        "content_text": extracted.content_text,
        "subject": extracted.subject,
        "sender": sender,
        "recipients": [
            {"name": recipient.name, "email": recipient.email} for recipient in extracted.recipients
        ],
        "sent_at": extracted.sent_at.isoformat() if extracted.sent_at else None,
        "rfc_message_id": extracted.rfc_message_id,
        "message_format": extracted.message_format,
        "parse_status": extracted.parse_status,
        "source_kind": extracted.source_kind,
        "received_at": extracted.received_at.isoformat() if extracted.received_at else None,
        "direction": extracted.direction,
        "source_account_id": extracted.source_account_id,
        "source_folder_id": extracted.source_folder_id,
        "source_message_id": extracted.source_message_id,
        "conversation_id": extracted.conversation_id,
        "internet_message_id": extracted.internet_message_id,
    }


def _extracted_from_json(payload: dict[str, object]) -> ExtractedArtifactData:
    sender_payload = payload.get("sender")
    sender = None
    if isinstance(sender_payload, dict):
        sender = MailParticipant(
            name=_optional_string(sender_payload.get("name")),
            email=_optional_string(sender_payload.get("email")),
        )
    recipients_payload = payload.get("recipients")
    recipients: list[MailParticipant] = []
    if isinstance(recipients_payload, list):
        for item in recipients_payload:
            if isinstance(item, dict):
                recipients.append(
                    MailParticipant(
                        name=_optional_string(item.get("name")),
                        email=_optional_string(item.get("email")),
                    )
                )
    return ExtractedArtifactData(
        content_text=str(payload.get("content_text") or ""),
        subject=_optional_string(payload.get("subject")),
        sender=sender,
        recipients=tuple(recipients),
        sent_at=_optional_datetime(payload.get("sent_at")),
        rfc_message_id=_optional_string(payload.get("rfc_message_id")),
        message_format=cast(
            Literal["outlook_msg", "rfc822_email"], payload.get("message_format", "rfc822_email")
        ),
        parse_status="parsed",
        source_kind=_optional_string(payload.get("source_kind")),
        received_at=_optional_datetime(payload.get("received_at")),
        direction=cast(Literal["inbound", "outbound"] | None, payload.get("direction")),
        source_account_id=_optional_string(payload.get("source_account_id")),
        source_folder_id=_optional_string(payload.get("source_folder_id")),
        source_message_id=_optional_string(payload.get("source_message_id")),
        conversation_id=_optional_string(payload.get("conversation_id")),
        internet_message_id=_optional_string(payload.get("internet_message_id")),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)


def _build_mail_metadata(
    *,
    artifact_id: UUID,
    extracted: ExtractedArtifactData,
    source_system: MailSourceSystem,
    external_message_id: str | None,
    rfc_message_id: str | None,
    source_account: str | None,
    source_mailbox: str | None,
    now: datetime,
) -> ArtifactMailMetadata | None:
    message_format = extracted.message_format
    if message_format not in {"outlook_msg", "rfc822_email"}:
        return None

    sender_name = extracted.sender.name if extracted.sender is not None else None
    sender_email = extracted.sender.email if extracted.sender is not None else None
    sender_domain = None
    if sender_email and "@" in sender_email:
        sender_domain = sender_email.rsplit("@", 1)[1].lower()

    return ArtifactMailMetadata(
        artifact_id=artifact_id,
        source_system=source_system,
        message_format=message_format,
        parse_status=extracted.parse_status,
        external_message_id=external_message_id,
        rfc_message_id=rfc_message_id or extracted.rfc_message_id,
        source_account=source_account,
        source_mailbox=source_mailbox,
        subject=extracted.subject,
        sender_name=sender_name,
        sender_email=sender_email,
        sender_domain=sender_domain,
        recipients=extracted.recipients,
        sent_at=extracted.sent_at,
        created_at=now,
    )


_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:aw|re|fw|fwd|wg|sv|antwort|reply)(?:\[\d+\])?\s*:\s*)+",
    re.IGNORECASE,
)


def _suggest_new_case_title(mail_metadata: ArtifactMailMetadata | None) -> str:
    if mail_metadata is None or mail_metadata.subject is None:
        return ""
    title = mail_metadata.subject.strip()
    previous = None
    while title and title != previous:
        previous = title
        title = _SUBJECT_PREFIX_RE.sub("", title).strip()
    return title


def _normalize_mail_selector(selector: MailSelector) -> MailSelector:
    return MailSelector(
        account_name=(selector.account_name or "").strip() or None,
        mailbox_name=(selector.mailbox_name or "").strip() or None,
        unread_only=selector.unread_only,
        sender_filter=(selector.sender_filter or "").strip(),
        subject_filter=(selector.subject_filter or "").strip(),
        sent_after=selector.sent_after,
        result_limit=min(max(selector.result_limit, 1), 50),
    )


def _already_imported_message(count: int) -> str:
    if count == 1:
        return "Already imported: 1 matching mail message is already in intake."
    return f"Already imported: {count} matching mail messages are already in intake."


def _conversation_for_message(
    *,
    repository: ArtifactRepository,
    user: UserContext,
    source_kind: str,
    external_conversation_id: str | None,
    artifact: Artifact,
    extracted: ExtractedArtifactData,
    now: datetime,
) -> MailConversation:
    latest_message_at = extracted.received_at or extracted.sent_at or artifact.uploaded_at
    participants = _mail_participants(extracted)
    existing = None
    for candidate in repository.list_recent_mail_conversations(user, limit=50):
        if (
            candidate.source_kind == source_kind
            and candidate.external_conversation_id == external_conversation_id
            and external_conversation_id is not None
        ):
            existing = candidate
            break
    if existing is None:
        return MailConversation(
            id=uuid4(),
            source_kind=source_kind,
            external_conversation_id=external_conversation_id,
            normalized_subject=_normalize_subject(extracted.subject),
            latest_subject=extracted.subject,
            latest_message_at=latest_message_at,
            participants=participants,
            message_count=1,
            latest_artifact_id=artifact.id,
            created_at=now,
            updated_at=now,
            assigned_case_id=artifact.assigned_case_id,
        )
    return replace(
        existing,
        latest_subject=extracted.subject or existing.latest_subject,
        latest_message_at=max(existing.latest_message_at, latest_message_at),
        participants=_merge_participants(existing.participants, participants),
        message_count=existing.message_count + 1,
        latest_artifact_id=artifact.id,
        updated_at=now,
        assigned_case_id=artifact.assigned_case_id or existing.assigned_case_id,
    )


def _mail_participants(extracted: ExtractedArtifactData) -> tuple[MailParticipant, ...]:
    participants = []
    if extracted.sender is not None:
        participants.append(extracted.sender)
    participants.extend(extracted.recipients)
    return _merge_participants((), tuple(participants))


def _merge_participants(
    existing: tuple[MailParticipant, ...],
    incoming: tuple[MailParticipant, ...],
) -> tuple[MailParticipant, ...]:
    merged: list[MailParticipant] = []
    seen: set[tuple[str | None, str | None]] = set()
    for participant in (*existing, *incoming):
        key = (
            participant.name.lower() if participant.name else None,
            participant.email.lower() if participant.email else None,
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(participant)
    return tuple(merged)


def _normalize_subject(subject: str | None) -> str | None:
    if subject is None:
        return None
    normalized = subject.strip().lower()
    normalized = re.sub(r"^((re|fw|fwd):\s*)+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized or None


def _dedupe_fingerprint(
    *,
    source_kind: str,
    source_account_id: str | None,
    source_folder_id: str | None,
    source_message_id: str | None,
    internet_message_id: str | None,
    subject: str | None,
    sent_at: datetime | None,
    content_text: str,
) -> str:
    payload = "|".join(
        (
            source_kind,
            source_account_id or "",
            source_folder_id or "",
            source_message_id or "",
            internet_message_id or "",
            subject or "",
            sent_at.isoformat() if sent_at is not None else "",
            content_text,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

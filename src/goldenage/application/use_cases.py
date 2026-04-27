"""Application services."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal
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
    MailCandidate,
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


@dataclass(frozen=True, slots=True)
class MailImportState:
    """UI state for the desktop mail selector and review queue."""

    enabled: bool = False
    selector: MailSelector = field(default_factory=MailSelector)
    candidates: tuple[MailCandidate, ...] = ()
    message: str | None = None
    message_kind: MessageKind = "info"


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

    def get_case_detail(self, *, case_id: UUID, user: UserContext, now: datetime) -> CaseDetail:
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
        recent_artifacts = tuple(
            sorted(
                self._artifact_repository.list_case_artifacts(case_id, user),
                key=lambda artifact: artifact.uploaded_at,
                reverse=True,
            )[:5]
        )
        return CaseDetail(
            case_file=case_file,
            active_activity=active_activity,
            open_activities=open_activities,
            recent_artifacts=recent_artifacts,
        )

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
            return MailImportState()
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
    ) -> IntakeState:
        artifact_id = uuid4()
        storage_key = self._artifact_store.store(artifact_id, file_name, content)
        extracted = self._content_extractor.extract(file_name, media_type, content)
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
        intake_state = self._intake_state_for_artifact(artifact, suggestion)
        if intake_state.search_mode:
            return intake_state
        return IntakeState(
            artifact=intake_state.artifact,
            suggestion=intake_state.suggestion,
            message="Document analyzed. Confirm or reject the proposed case.",
        )

    def get_intake_state(self, *, artifact_id: UUID, user: UserContext) -> IntakeState:
        """Load persisted intake state."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        suggestion = self._artifact_repository.get_suggestion(artifact_id, user)
        return self._intake_state_for_artifact(artifact, suggestion)

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
            payload={"case_id": str(case_id), "next_due_at": next_due_at.isoformat()},
            now=now,
        )
        return self.get_case_detail(case_id=case_id, user=user, now=now)

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
    ) -> IntakeState:
        if suggestion is None or suggestion.suggested_case_id is None:
            return IntakeState(
                artifact=artifact,
                suggestion=suggestion,
                search_mode=True,
                message="No safe single-case match was found. Use the bounded search flow.",
            )
        return IntakeState(artifact=artifact, suggestion=suggestion)


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
    if extracted.message_format not in {"outlook_msg", "rfc822_email"}:
        return None

    sender_name = extracted.sender.name if extracted.sender is not None else None
    sender_email = extracted.sender.email if extracted.sender is not None else None
    sender_domain = None
    if sender_email and "@" in sender_email:
        sender_domain = sender_email.rsplit("@", 1)[1].lower()

    return ArtifactMailMetadata(
        artifact_id=artifact_id,
        source_system=source_system,
        message_format=extracted.message_format,
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

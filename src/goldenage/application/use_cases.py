"""Application services."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
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
)
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    ExtractedArtifactData,
    SearchResult,
    UserContext,
)
from goldenage.domain.rules import ResolutionError, build_resolution_plan, due_label


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
    selected_artifact: Artifact | None = None
    selected_mail_metadata: ArtifactMailMetadata | None = None


@dataclass(frozen=True, slots=True)
class IntakeState:
    """UI state for the intake panel."""

    artifact: Artifact | None = None
    suggestion: AssignmentSuggestion | None = None
    search_mode: bool = False
    search_query: str = ""
    search_results: tuple[SearchResult, ...] = ()
    message: str | None = None


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
    ) -> None:
        self._case_repository = case_repository
        self._activity_repository = activity_repository
        self._artifact_repository = artifact_repository
        self._audit_repository = audit_repository
        self._artifact_store = artifact_store
        self._content_extractor = content_extractor
        self._gisela_client = gisela_client
        self._elizabethan_client = elizabethan_client

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
        open_activities = tuple(activity for activity in activities if activity.completed_at is None)
        active_activity = open_activities[0] if open_activities else None
        recent_artifacts = tuple(
            sorted(
                self._artifact_repository.list_case_artifacts(case_id, user),
                key=lambda artifact: artifact.uploaded_at,
                reverse=True,
            )[:5]
        )
        selected_artifact = None
        selected_mail_metadata = None
        if selected_artifact_id is not None:
            selected_artifact = self._artifact_repository.get_artifact(selected_artifact_id, user)
            if selected_artifact is None or selected_artifact.assigned_case_id != case_id:
                raise NotFoundError("Artifact not found.")
            selected_mail_metadata = self._artifact_repository.get_mail_metadata(
                selected_artifact_id,
                user,
            )
        return CaseDetail(
            case_file=case_file,
            active_activity=active_activity,
            open_activities=open_activities,
            recent_artifacts=recent_artifacts,
            selected_artifact=selected_artifact,
            selected_mail_metadata=selected_mail_metadata,
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
        self._case_repository.save_case(
            replace(case_file, status=new_status, last_activity_at=now)
        )

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
        mail_metadata = _build_mail_metadata(artifact_id=artifact_id, extracted=extracted, now=now)
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
            event_type="artifact_uploaded",
            subject_id=artifact.id,
            payload={
                "file_name": artifact.file_name,
                "subject": mail_metadata.subject if mail_metadata is not None else None,
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

    def create_case_for_artifact(
        self,
        *,
        artifact_id: UUID,
        title: str,
        company: str | None,
        primary_contact: str | None,
        next_step: str,
        next_due_at: datetime,
        user: UserContext,
        now: datetime,
    ) -> CaseDetail:
        """Create a new case from intake, assign the artifact, and schedule the first activity."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")

        normalized_title = title.strip()
        if not normalized_title:
            raise ResolutionError("A case title is required.")

        normalized_step = next_step.strip()
        if not normalized_step:
            raise ResolutionError("A next step description is required.")

        case_id = uuid4()
        case_file = CaseFile(
            id=case_id,
            title=normalized_title,
            company=company.strip() or None if company is not None else None,
            primary_contact=primary_contact.strip() or None
            if primary_contact is not None
            else None,
            status="open",
            last_activity_at=now,
        )
        self._case_repository.save_case(case_file)
        self._artifact_repository.save_artifact(replace(artifact, assigned_case_id=case_id))
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
        self._record_audit(
            actor_user_id=user.id,
            event_type="artifact_assigned_to_new_case",
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
    now: datetime,
) -> ArtifactMailMetadata | None:
    if extracted.source_kind != "outlook_msg":
        return None

    sender_name = extracted.sender.name if extracted.sender is not None else None
    sender_email = extracted.sender.email if extracted.sender is not None else None
    sender_domain = None
    if sender_email and "@" in sender_email:
        sender_domain = sender_email.rsplit("@", 1)[1].lower()

    return ArtifactMailMetadata(
        artifact_id=artifact_id,
        source_kind=extracted.source_kind,
        parse_status=extracted.parse_status,
        subject=extracted.subject,
        sender_name=sender_name,
        sender_email=sender_email,
        sender_domain=sender_domain,
        recipients=extracted.recipients,
        sent_at=extracted.sent_at,
        created_at=now,
    )

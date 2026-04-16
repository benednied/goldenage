"""Application services."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

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
    MailConversation,
    MailMessage,
    MailParticipant,
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
    conversation: MailConversation | None = None
    conversation_artifacts: tuple[Artifact, ...] = ()
    recent_conversations: tuple[MailConversation, ...] = ()
    ingest_status: str = "new"


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

    def get_recent_intake(self, *, user: UserContext, limit: int = 8) -> IntakeState:
        """Return recent conversation-oriented intake items."""
        return IntakeState(
            recent_conversations=tuple(
                self._artifact_repository.list_recent_mail_conversations(user, limit=limit)
            )
        )

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
            mail_message = self._artifact_repository.get_mail_message(selected_artifact_id, user)
            if mail_message is not None:
                selected_conversation = self._artifact_repository.get_mail_conversation(
                    mail_message.conversation_id,
                    user,
                )
                selected_conversation_artifacts = tuple(
                    self._artifact_repository.list_conversation_artifacts(
                        mail_message.conversation_id,
                        user,
                    )
                )
        return CaseDetail(
            case_file=case_file,
            active_activity=active_activity,
            open_activities=open_activities,
            recent_artifacts=recent_artifacts,
            selected_artifact=selected_artifact,
            selected_mail_metadata=selected_mail_metadata,
            selected_conversation=selected_conversation,
            selected_conversation_artifacts=selected_conversation_artifacts,
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
        extracted = self._content_extractor.extract(file_name, media_type, content)
        return self.ingest_mail(
            file_name=file_name,
            media_type=media_type,
            content=content,
            extracted=extracted,
            user=user,
            now=now,
            audit_event_type="artifact_uploaded",
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
        audit_event_type: str = "mail_ingested",
    ) -> IntakeState:
        """Converged ingestion path for uploads and mailbox sync."""
        dedupe_fingerprint = _build_dedupe_fingerprint(extracted, file_name=file_name)
        existing_message = self._artifact_repository.find_mail_message_by_source(
            source_kind=extracted.source_kind,
            source_account_id=extracted.source_account_id,
            source_folder_id=extracted.source_folder_id,
            source_message_id=extracted.source_message_id,
            internet_message_id=extracted.internet_message_id,
            dedupe_fingerprint=dedupe_fingerprint,
            user=user,
        )
        if existing_message is not None:
            artifact = self._artifact_repository.get_artifact(existing_message.artifact_id, user)
            if artifact is None:
                raise NotFoundError("Artifact not found.")
            suggestion = self._artifact_repository.get_suggestion(artifact.id, user)
            intake_state = self._intake_state_for_artifact(artifact, suggestion, user=user)
            return replace(
                intake_state,
                message="Message was already ingested. Showing the existing conversation.",
                ingest_status="duplicate",
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
        mail_metadata = _build_mail_metadata(artifact_id=artifact_id, extracted=extracted, now=now)
        if mail_metadata is not None:
            self._artifact_repository.save_mail_metadata(mail_metadata)
        conversation = self._upsert_conversation(
            artifact_id=artifact_id,
            extracted=extracted,
            mail_metadata=mail_metadata,
            now=now,
            user=user,
        )
        self._artifact_repository.save_mail_message(
            MailMessage(
                artifact_id=artifact_id,
                conversation_id=conversation.id,
                source_kind=extracted.source_kind,
                source_account_id=extracted.source_account_id,
                source_folder_id=extracted.source_folder_id,
                source_message_id=extracted.source_message_id,
                source_conversation_id=extracted.conversation_id,
                internet_message_id=extracted.internet_message_id,
                dedupe_fingerprint=dedupe_fingerprint,
                direction=extracted.direction,
                received_at=extracted.received_at,
                created_at=now,
            )
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
                "subject": mail_metadata.subject if mail_metadata is not None else None,
                "conversation_id": str(conversation.id),
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
        return replace(
            intake_state,
            message="Document analyzed. Confirm or reject the proposed case.",
        )

    def get_intake_state(self, *, artifact_id: UUID, user: UserContext) -> IntakeState:
        """Load persisted intake state."""
        artifact = self._artifact_repository.get_artifact(artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        suggestion = self._artifact_repository.get_suggestion(artifact_id, user)
        return self._intake_state_for_artifact(artifact, suggestion, user=user)

    def get_intake_state_for_conversation(
        self,
        *,
        conversation_id: UUID,
        user: UserContext,
    ) -> IntakeState:
        """Load one conversation-oriented intake state."""
        conversation = self._artifact_repository.get_mail_conversation(conversation_id, user)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        artifact = self._artifact_repository.get_artifact(conversation.latest_artifact_id, user)
        if artifact is None:
            raise NotFoundError("Artifact not found.")
        suggestion = self._artifact_repository.get_suggestion(artifact.id, user)
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
        intake_state = self._intake_state_for_artifact(artifact, suggestion, user=user)
        return replace(
            intake_state,
            search_mode=True,
            search_query=query,
            search_results=tuple(results),
            message=None,
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
        self._sync_conversation_assignment(artifact_id=artifact_id, case_id=case_id, user=user)
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
        self._sync_conversation_assignment(artifact_id=artifact_id, case_id=case_id, user=user)
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

    def _upsert_conversation(
        self,
        *,
        artifact_id: UUID,
        extracted: ExtractedArtifactData,
        mail_metadata: ArtifactMailMetadata | None,
        now: datetime,
        user: UserContext,
    ) -> MailConversation:
        conversation_id = _conversation_id_for(extracted)
        existing = self._artifact_repository.get_mail_conversation(conversation_id, user)
        participants = _participants_for_conversation(extracted, mail_metadata)
        message_at = extracted.received_at or extracted.sent_at or now
        if existing is None:
            conversation = MailConversation(
                id=conversation_id,
                source_kind=extracted.source_kind,
                external_conversation_id=extracted.conversation_id,
                normalized_subject=_normalize_subject(extracted.subject),
                latest_subject=extracted.subject,
                latest_message_at=message_at,
                participants=participants,
                message_count=1,
                latest_artifact_id=artifact_id,
                created_at=now,
                updated_at=now,
            )
        else:
            latest_subject = existing.latest_subject
            latest_artifact_id = existing.latest_artifact_id
            latest_message_at = existing.latest_message_at
            if message_at >= existing.latest_message_at:
                latest_subject = extracted.subject
                latest_artifact_id = artifact_id
                latest_message_at = message_at
            conversation = replace(
                existing,
                latest_subject=latest_subject,
                latest_message_at=latest_message_at,
                participants=participants or existing.participants,
                message_count=existing.message_count + 1,
                latest_artifact_id=latest_artifact_id,
                updated_at=now,
            )
        self._artifact_repository.save_mail_conversation(conversation)
        return conversation

    def _sync_conversation_assignment(
        self,
        *,
        artifact_id: UUID,
        case_id: UUID,
        user: UserContext,
    ) -> None:
        mail_message = self._artifact_repository.get_mail_message(artifact_id, user)
        if mail_message is None:
            return
        conversation = self._artifact_repository.get_mail_conversation(mail_message.conversation_id, user)
        if conversation is None:
            return
        self._artifact_repository.save_mail_conversation(
            replace(conversation, assigned_case_id=case_id, updated_at=conversation.updated_at)
        )

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
        mail_message = self._artifact_repository.get_mail_message(artifact.id, user)
        conversation = None
        conversation_artifacts: tuple[Artifact, ...] = ()
        if mail_message is not None:
            conversation = self._artifact_repository.get_mail_conversation(mail_message.conversation_id, user)
            conversation_artifacts = tuple(
                self._artifact_repository.list_conversation_artifacts(
                    mail_message.conversation_id,
                    user,
                )
            )
        base = IntakeState(
            artifact=artifact,
            suggestion=suggestion,
            conversation=conversation,
            conversation_artifacts=conversation_artifacts,
            recent_conversations=tuple(
                self._artifact_repository.list_recent_mail_conversations(user, limit=8)
            ),
        )
        if suggestion is None or suggestion.suggested_case_id is None:
            return replace(
                base,
                search_mode=True,
                message="No safe single-case match was found. Use the bounded search flow.",
            )
        return base


def _build_mail_metadata(
    *,
    artifact_id: UUID,
    extracted: ExtractedArtifactData,
    now: datetime,
) -> ArtifactMailMetadata | None:
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


def _build_dedupe_fingerprint(extracted: ExtractedArtifactData, *, file_name: str) -> str:
    sender = extracted.sender.email if extracted.sender is not None else ""
    subject = _normalize_subject(extracted.subject) or file_name.lower()
    received = (extracted.received_at or extracted.sent_at)
    return "|".join(
        (
            extracted.source_kind,
            extracted.source_account_id or "",
            extracted.source_folder_id or "",
            extracted.source_message_id or "",
            extracted.internet_message_id or "",
            sender or "",
            subject,
            received.isoformat() if received is not None else "",
        )
    )


def _conversation_id_for(extracted: ExtractedArtifactData) -> UUID:
    key = extracted.conversation_id or extracted.internet_message_id or _normalize_subject(extracted.subject)
    return uuid5(NAMESPACE_URL, f"{extracted.source_kind}:{key or uuid4()}")


def _normalize_subject(subject: str | None) -> str | None:
    if not subject:
        return None
    normalized = subject.strip()
    for prefix in ("re:", "fw:", "fwd:", "aw:"):
        while normalized.lower().startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    return normalized.lower() or None


def _participants_for_conversation(
    extracted: ExtractedArtifactData,
    mail_metadata: ArtifactMailMetadata | None,
) -> tuple[MailParticipant, ...]:
    participants: list[MailParticipant] = []
    if extracted.sender is not None:
        participants.append(extracted.sender)
    if mail_metadata is not None:
        participants.extend(mail_metadata.recipients)
    deduped: list[MailParticipant] = []
    seen: set[tuple[str | None, str | None]] = set()
    for participant in participants:
        key = (participant.name, participant.email)
        if key in seen:
            continue
        deduped.append(participant)
        seen.add(key)
    return tuple(deduped)

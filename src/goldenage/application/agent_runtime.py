"""Persistent, permission-scoped runtime for the Gisela assistant."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from goldenage.application.ports import (
    AgentRepository,
    ArtifactRepository,
    AuditRepository,
    CaseRepository,
    ElizabethanSearchClient,
)
from goldenage.application.use_cases import GoldenAgeService, NotFoundError
from goldenage.domain.models import (
    AgentConversation,
    AgentExecutionContext,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    Artifact,
    AuditEvent,
    CaseFile,
    SearchResult,
    UserContext,
)
from goldenage.domain.rules import ResolutionError

AGENT_NAME = "Gisela"
MAX_QUERY_LENGTH = 200
MAX_MESSAGE_LENGTH = 4000


class AgentRuntimeError(RuntimeError):
    """Raised when a chat request cannot be started or resumed."""


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """Validated, bounded request for one application-owned tool."""

    tool_name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class AgentChatState:
    """Conversation state returned to a web client or another adapter."""

    conversation: AgentConversation
    messages: tuple[AgentMessage, ...]
    runs: tuple[AgentRun, ...]
    tool_calls: tuple[AgentToolCall, ...] = ()


class AgentModel(Protocol):
    """Small model boundary; production models can be substituted later."""

    def plan(
        self,
        prompt: str,
        transcript: Sequence[AgentMessage],
    ) -> ToolRequest | None:
        """Return one bounded tool request or a direct response."""


class DeterministicAgentModel(AgentModel):
    """Safe fake model for local use and tests; retrieved text is never reparsed."""

    _UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}")

    def plan(
        self,
        prompt: str,
        transcript: Sequence[AgentMessage],
    ) -> ToolRequest | None:
        del transcript
        normalized = prompt.strip()
        lowered = normalized.casefold()
        if lowered in {"list cases", "show cases", "cases"}:
            return ToolRequest("list_cases", {})
        if lowered.startswith("search cases:"):
            return ToolRequest(
                "search_cases",
                {"query": normalized.split(":", 1)[1].strip()[:MAX_QUERY_LENGTH]},
            )
        if lowered.startswith("show case:"):
            return ToolRequest("get_case", {"case_id": normalized.split(":", 1)[1].strip()})
        if lowered.startswith("show artifact:"):
            return ToolRequest("get_artifact", {"artifact_id": normalized.split(":", 1)[1].strip()})
        if lowered in {"list artifacts", "show artifacts", "artifacts"}:
            return ToolRequest("list_artifacts", {})
        if lowered.startswith("assign artifact:"):
            return _parse_assignment(normalized)
        return None


class ScriptedAgentModel(AgentModel):
    """Deterministic model fixture that returns predefined tool requests in order."""

    def __init__(self, requests: Sequence[ToolRequest | None]) -> None:
        self._requests = list(requests)

    def plan(
        self,
        prompt: str,
        transcript: Sequence[AgentMessage],
    ) -> ToolRequest | None:
        del prompt, transcript
        return self._requests.pop(0) if self._requests else None


class AgentRuntime:
    """Orchestrate durable transcripts and user-equivalent application tools.

    The runtime never receives a database connection or a shell. Every tool is routed through
    existing repositories or application commands with a request-time user context carrying the
    immutable agent provenance.
    """

    def __init__(
        self,
        *,
        repository: AgentRepository,
        case_repository: CaseRepository,
        artifact_repository: ArtifactRepository,
        audit_repository: AuditRepository,
        service: GoldenAgeService,
        model: AgentModel | None = None,
        search_client: ElizabethanSearchClient | None = None,
    ) -> None:
        self._repository = repository
        self._case_repository = case_repository
        self._artifact_repository = artifact_repository
        self._audit_repository = audit_repository
        self._service = service
        self._model = model or DeterministicAgentModel()
        self._search_client = search_client

    def start_conversation(
        self,
        *,
        user: UserContext,
        now: datetime,
        title: str = "New conversation",
    ) -> AgentConversation:
        normalized_title = " ".join(title.split())[:120] or "New conversation"
        conversation = AgentConversation(
            id=uuid4(),
            acting_user_id=user.id,
            agent_name=AGENT_NAME,
            title=normalized_title,
            created_at=now,
            updated_at=now,
        )
        self._repository.save_conversation(conversation)
        return conversation

    def get_chat_state(self, *, conversation_id: UUID, user: UserContext) -> AgentChatState:
        conversation = self._repository.get_conversation(conversation_id, user)
        if conversation is None:
            raise NotFoundError("Gisela conversation not found.")
        messages = tuple(self._repository.list_messages(conversation_id, user))
        runs = tuple(self._repository.list_runs(conversation_id, user))
        tool_calls = tuple(
            call for run in runs for call in self._repository.list_tool_calls(run.id, user)
        )
        return AgentChatState(
            conversation=conversation,
            messages=messages,
            runs=runs,
            tool_calls=tool_calls,
        )

    def list_conversations(
        self, *, user: UserContext, limit: int = 25
    ) -> tuple[AgentConversation, ...]:
        return tuple(self._repository.list_conversations(user, limit=limit))

    def send_message(
        self,
        *,
        conversation_id: UUID,
        content: str,
        user: UserContext,
        now: datetime,
        request_id: str | None = None,
    ) -> AgentChatState:
        conversation = self._repository.get_conversation(conversation_id, user)
        if conversation is None:
            raise NotFoundError("Gisela conversation not found.")
        prompt = content.strip()
        if not prompt:
            raise ResolutionError("A message is required.")
        if len(prompt) > MAX_MESSAGE_LENGTH:
            raise ResolutionError("That message is too long.")

        run = AgentRun(
            id=uuid4(),
            conversation_id=conversation.id,
            acting_user_id=user.id,
            actor_kind="agent",
            agent_name=AGENT_NAME,
            status="running",
            started_at=now,
            request_id=request_id,
        )
        provenance = AgentExecutionContext(
            actor_kind="agent",
            acting_user_id=user.id,
            agent_name=AGENT_NAME,
            conversation_id=conversation.id,
            agent_run_id=run.id,
        )
        execution_user = replace(user, execution_context=provenance)
        self._repository.save_run(run)
        self._repository.save_message(
            AgentMessage(
                id=uuid4(),
                conversation_id=conversation.id,
                run_id=run.id,
                role="user",
                content=prompt,
                created_at=now,
                sequence=self._next_message_sequence(conversation.id, user),
            )
        )
        self._record_audit(
            event_type="agent_run_started",
            subject_id=run.id,
            payload={"request_id": request_id, "prompt_length": len(prompt)},
            now=now,
            provenance=provenance,
        )

        try:
            transcript = tuple(self._repository.list_messages(conversation.id, user))
            request = self._model.plan(prompt, transcript)
            if request is None:
                response = (
                    "I’m Gisela. I can list visible cases or artifacts, search visible cases, "
                    "show one visible record, and assign an artifact through the normal workflow."
                )
            else:
                response = self._run_tool(
                    request=request,
                    run=run,
                    execution_user=execution_user,
                    provenance=provenance,
                    now=now,
                )
            self._repository.save_message(
                AgentMessage(
                    id=uuid4(),
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content=response,
                    created_at=now,
                    sequence=self._next_message_sequence(conversation.id, user),
                )
            )
            self._repository.save_run(replace(run, status="completed", completed_at=now))
            self._repository.save_conversation(replace(conversation, updated_at=now))
            self._record_audit(
                event_type="agent_run_completed",
                subject_id=run.id,
                payload={"tool_count": len(self._repository.list_tool_calls(run.id, user))},
                now=now,
                provenance=provenance,
            )
        except Exception as error:
            message = str(error) or "The agent operation failed."
            self._repository.save_message(
                AgentMessage(
                    id=uuid4(),
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content=f"I could not complete that request: {message}",
                    created_at=now,
                    sequence=self._next_message_sequence(conversation.id, user),
                )
            )
            self._repository.save_run(
                replace(run, status="failed", completed_at=now, error=message)
            )
            self._repository.save_conversation(replace(conversation, updated_at=now))
            self._record_audit(
                event_type="agent_run_failed",
                subject_id=run.id,
                payload={"error": message},
                now=now,
                provenance=provenance,
            )
        return self.get_chat_state(conversation_id=conversation.id, user=user)

    def _next_message_sequence(self, conversation_id: UUID, user: UserContext) -> int:
        messages = self._repository.list_messages(conversation_id, user)
        return max((message.sequence for message in messages), default=-1) + 1

    def cancel_run(
        self,
        *,
        run_id: UUID,
        user: UserContext,
        now: datetime,
    ) -> AgentRun:
        run = self._repository.get_run(run_id, user)
        if run is None:
            raise NotFoundError("Gisela run not found.")
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        cancelled = replace(run, status="cancelled", completed_at=now, error="Cancelled by user.")
        self._repository.save_run(cancelled)
        self._record_audit(
            event_type="agent_run_cancelled",
            subject_id=run.id,
            payload={"reason": "user_requested"},
            now=now,
            provenance=AgentExecutionContext(
                actor_kind="agent",
                acting_user_id=user.id,
                agent_name=run.agent_name,
                conversation_id=run.conversation_id,
                agent_run_id=run.id,
            ),
        )
        return cancelled

    def _run_tool(
        self,
        *,
        request: ToolRequest,
        run: AgentRun,
        execution_user: UserContext,
        provenance: AgentExecutionContext,
        now: datetime,
    ) -> str:
        tool_name = request.tool_name.strip()
        if tool_name not in {
            "list_cases",
            "get_case",
            "search_cases",
            "list_artifacts",
            "get_artifact",
            "assign_artifact",
        }:
            raise AgentRuntimeError("That tool is not available to Gisela.")
        arguments = _bounded_arguments(request.arguments)
        idempotency_key = _idempotency_key(
            conversation_id=run.conversation_id,
            request_id=run.request_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        prior = self._repository.get_tool_call_by_idempotency(
            conversation_id=run.conversation_id,
            idempotency_key=idempotency_key,
            user=execution_user,
        )
        if prior is not None:
            output = prior.output_json or {
                "error": prior.error or "The previous attempt did not return data."
            }
            return _tool_response(tool_name, output, deduplicated=True)

        tool_call = AgentToolCall(
            id=uuid4(),
            conversation_id=run.conversation_id,
            run_id=run.id,
            acting_user_id=execution_user.id,
            actor_kind="agent",
            agent_name=AGENT_NAME,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            input_json=arguments,
            status="running",
            output_json=None,
            error=None,
            created_at=now,
        )
        # The running row is written before the business command. A retry therefore cannot
        # replay a transition after a process interruption between command and result persistence.
        self._repository.save_tool_call(tool_call)
        self._record_audit(
            event_type="agent_tool_started",
            subject_id=tool_call.id,
            payload={"tool_name": tool_name, "input": arguments},
            now=now,
            provenance=provenance,
        )
        try:
            output = self._execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                user=execution_user,
                provenance=provenance,
                now=now,
            )
        except Exception as error:
            message = str(error) or "Tool execution failed."
            self._repository.save_tool_call(
                replace(tool_call, status="failed", error=message, completed_at=now)
            )
            self._record_audit(
                event_type="agent_tool_failed",
                subject_id=tool_call.id,
                payload={"tool_name": tool_name, "error": message},
                now=now,
                provenance=provenance,
            )
            raise

        self._repository.save_tool_call(
            replace(tool_call, status="completed", output_json=output, completed_at=now)
        )
        self._repository.save_message(
            AgentMessage(
                id=uuid4(),
                conversation_id=run.conversation_id,
                run_id=run.id,
                role="tool",
                content=json.dumps(output, sort_keys=True),
                created_at=now,
                sequence=self._next_message_sequence(run.conversation_id, execution_user),
            )
        )
        self._record_audit(
            event_type="agent_tool_completed",
            subject_id=tool_call.id,
            payload={"tool_name": tool_name, "output": output},
            now=now,
            provenance=provenance,
        )
        return _tool_response(tool_name, output)

    def _execute_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, object],
        user: UserContext,
        provenance: AgentExecutionContext,
        now: datetime,
    ) -> dict[str, object]:
        if tool_name == "list_cases":
            query = str(arguments.get("query", "")).strip().casefold()
            cases = self._case_repository.list_cases(user)
            if query:
                cases = tuple(case for case in cases if _case_matches(case, query))
            return {"cases": [_case_json(case) for case in cases[:25]]}
        if tool_name == "get_case":
            case_id = _uuid_argument(arguments, "case_id")
            case = self._case_repository.get_case(case_id, user)
            if case is None:
                raise NotFoundError("Case not found.")
            return {"case": _case_json(case)}
        if tool_name == "search_cases":
            query = str(arguments.get("query", "")).strip()[:MAX_QUERY_LENGTH]
            if not query:
                raise ResolutionError("A case search query is required.")
            cases = tuple(self._case_repository.list_cases(user))
            if self._search_client is not None:
                search_context = Artifact(
                    id=uuid4(),
                    file_name="agent-search",
                    media_type="text/plain",
                    size_bytes=0,
                    content_text=query,
                    storage_key="agent-search",
                    uploaded_at=now,
                    uploaded_by=user.id,
                )
                results = self._search_client.search_cases(
                    query,
                    search_context,
                    None,
                    cases,
                    now,
                )
                return {
                    "query": query,
                    "results": [_search_result_json(result) for result in results],
                }
            matches = [case for case in cases if _case_matches(case, query.casefold())]
            return {"query": query, "results": [_case_json(case) for case in matches[:10]]}
        if tool_name == "list_artifacts":
            artifacts = list(self._service.list_unassigned_intake(user=user, limit=25))
            for case in self._case_repository.list_cases(user):
                artifacts.extend(self._artifact_repository.list_case_artifacts(case.id, user))
            unique_artifacts = {artifact.id: artifact for artifact in artifacts}
            return {
                "artifacts": [
                    {
                        "id": str(artifact.id),
                        "file_name": artifact.file_name,
                        "media_type": artifact.media_type,
                        "assigned_case_id": (
                            str(artifact.assigned_case_id)
                            if artifact.assigned_case_id is not None
                            else None
                        ),
                    }
                    for artifact in list(unique_artifacts.values())[:25]
                ]
            }
        if tool_name == "get_artifact":
            artifact_id = _uuid_argument(arguments, "artifact_id")
            artifact = self._artifact_repository.get_artifact(artifact_id, user)
            if artifact is None:
                raise NotFoundError("Artifact not found.")
            # Content is returned as explicitly untrusted data. It is not passed to the model's
            # planner as instructions and cannot widen the tool set or acting identity.
            return {
                "artifact": {
                    "id": str(artifact.id),
                    "file_name": artifact.file_name,
                    "media_type": artifact.media_type,
                    "content_excerpt": artifact.content_text[:500],
                    "content_is_untrusted": True,
                    "assigned_case_id": (
                        str(artifact.assigned_case_id) if artifact.assigned_case_id else None
                    ),
                }
            }
        if tool_name == "assign_artifact":
            artifact_id = _uuid_argument(arguments, "artifact_id")
            case_id = _uuid_argument(arguments, "case_id")
            next_step = str(arguments.get("next_step", "")).strip()
            raw_due_at = arguments.get("next_due_at")
            next_due_at = (
                datetime.fromisoformat(str(raw_due_at)) if raw_due_at else now + timedelta(days=1)
            )
            self._service.assign_artifact_to_case(
                artifact_id=artifact_id,
                case_id=case_id,
                next_step=next_step,
                next_due_at=next_due_at,
                user=user,
                now=now,
                provenance=provenance,
            )
            return {
                "artifact_id": str(artifact_id),
                "case_id": str(case_id),
                "next_due_at": next_due_at.isoformat(),
                "transition": "artifact_assigned",
            }
        raise AgentRuntimeError("That tool is not available to Gisela.")

    def _record_audit(
        self,
        *,
        event_type: str,
        subject_id: UUID,
        payload: dict[str, object],
        now: datetime,
        provenance: AgentExecutionContext,
    ) -> None:
        self._audit_repository.save_event(
            AuditEvent(
                id=uuid4(),
                actor_user_id=provenance.acting_user_id,
                event_type=event_type,
                subject_id=subject_id,
                payload_json=payload,
                created_at=now,
                actor_kind=provenance.actor_kind,
                acting_user_id=provenance.acting_user_id,
                agent_name=provenance.agent_name,
                conversation_id=provenance.conversation_id,
                agent_run_id=provenance.agent_run_id,
            )
        )


def _parse_assignment(prompt: str) -> ToolRequest:
    parts = prompt.split(":", 1)
    if len(parts) != 2:
        raise ResolutionError("Use 'assign artifact: ARTIFACT_ID case: CASE_ID next: STEP'.")
    payload = parts[1]
    match = re.match(
        r"\s*(?P<artifact>[0-9a-fA-F-]+)\s+case:\s*(?P<case>[0-9a-fA-F-]+)\s+next:\s*(?P<next>.+)",
        payload,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise ResolutionError("Use 'assign artifact: ARTIFACT_ID case: CASE_ID next: STEP'.")
    return ToolRequest(
        "assign_artifact",
        {
            "artifact_id": match.group("artifact"),
            "case_id": match.group("case"),
            "next_step": match.group("next")[:500],
        },
    )


def _bounded_arguments(arguments: Mapping[str, object]) -> dict[str, object]:
    allowed = {str(key): value for key, value in arguments.items() if isinstance(key, str)}
    bounded: dict[str, object] = {}
    for key, value in allowed.items():
        if isinstance(value, str):
            bounded[key] = value[:MAX_MESSAGE_LENGTH]
        elif isinstance(value, (int, float, bool)) or value is None:
            bounded[key] = value
    return bounded


def _uuid_argument(arguments: Mapping[str, object], key: str) -> UUID:
    try:
        return UUID(str(arguments[key]))
    except (KeyError, ValueError, TypeError) as error:
        raise ResolutionError(f"A valid {key} is required.") from error


def _idempotency_key(
    *,
    conversation_id: UUID,
    request_id: str | None,
    tool_name: str,
    arguments: Mapping[str, object],
) -> str:
    payload = json.dumps(
        {
            "conversation_id": str(conversation_id),
            "request_id": request_id,
            "tool_name": tool_name,
            "arguments": arguments,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _case_matches(case: CaseFile, query: str) -> bool:
    haystack = " ".join(
        value.casefold() for value in (case.title, case.company or "", case.primary_contact or "")
    )
    return all(token in haystack for token in query.split()[:12])


def _case_json(case: CaseFile) -> dict[str, object]:
    return {
        "id": str(case.id),
        "title": case.title,
        "company": case.company,
        "primary_contact": case.primary_contact,
        "status": case.status,
        "last_activity_at": case.last_activity_at.isoformat(),
    }


def _search_result_json(result: SearchResult) -> dict[str, object]:
    return {
        "case_id": str(result.case_id),
        "title": result.title,
        "company": result.company,
        "last_activity_at": result.last_activity_at.isoformat(),
        "reason": result.reason,
        "score": result.score,
    }


def _tool_response(tool_name: str, output: Mapping[str, object], deduplicated: bool = False) -> str:
    prefix = f"I used {tool_name}."
    if deduplicated:
        prefix += " This was a retry; the existing result was reused."
    return f"{prefix} Result: {json.dumps(dict(output), sort_keys=True)}"

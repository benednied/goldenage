"""Trusted-local automation orchestration.

The runtime in this module is intentionally trusted code execution.  It makes
ownership, effective identity, configured paths, provenance, and outcomes
explicit, but it is not a security sandbox: a script can still use the host
process's Python and filesystem capabilities.  Deployments that need tenant
isolation must run separate OS identities/instances.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from goldenage.application.ports import (
    ActivityRepository,
    ArtifactRepository,
    AuditRepository,
    AutomationRepository,
    CaseRepository,
)
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AuditEvent,
    Automation,
    AutomationRun,
    AutomationRunStatus,
    AutomationTrigger,
    AutomationTriggerKind,
    UserContext,
)

MAX_OUTPUT_BYTES = 16_000
MAX_RESULT_BYTES = 8_000
_CRON_FIELDS = ("minute", "hour", "day", "month", "weekday")
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


@dataclass(frozen=True, slots=True)
class AutomationEvent:
    """A committed business event that may activate automation listeners."""

    id: str
    event_type: str
    user_id: UUID
    occurred_at: datetime
    artifact: Artifact | None = None
    mail_metadata: ArtifactMailMetadata | None = None
    committed: bool = True


class TrustedPythonRuntime(Protocol):
    """Execution port kept separate from the web request and repositories."""

    def run(
        self,
        code: str,
        *,
        api: "AutomationAppApi",
        user: UserContext,
        trigger: AutomationTrigger,
    ) -> tuple[str, str, dict[str, object]]:
        """Execute trusted code and return bounded output and its result."""


class TrustedScriptError(RuntimeError):
    """Script failure carrying output captured before the exception."""

    def __init__(self, original: Exception, *, stdout: str, stderr: str) -> None:
        super().__init__(str(original))
        self.original_type = type(original).__name__
        self.captured_stdout = _bounded_text(stdout)
        self.captured_stderr = _bounded_text(stderr)


class InProcessTrustedPythonRuntime:
    """Default trusted runtime; it deliberately does not claim isolation."""

    def run(
        self,
        code: str,
        *,
        api: "AutomationAppApi",
        user: UserContext,
        trigger: AutomationTrigger,
    ) -> tuple[str, str, dict[str, object]]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        namespace: dict[str, object] = {
            "__name__": "goldenage_trusted_automation",
            "automation": api,
            "user": user,
            "trigger": trigger,
            "result": {},
        }
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exec(compile(code, "<trusted-automation>", "exec"), namespace, namespace)
        except Exception as error:
            raise TrustedScriptError(
                error, stdout=stdout.getvalue(), stderr=stderr.getvalue()
            ) from error
        raw_result = namespace.get("result", {})
        if not isinstance(raw_result, Mapping):
            raw_result = {"value": str(raw_result)}
        return (
            _bounded_text(stdout.getvalue()),
            _bounded_text(stderr.getvalue()),
            _bounded_result(dict(raw_result)),
        )


class AutomationAppApi:
    """Small app-owned API exposed to trusted code.

    Every repository read/write is made with the request-time effective user.
    The wrapper is intentionally explicit; scripts do not receive repository
    objects or a database connection.
    """

    def __init__(
        self,
        *,
        case_repository: CaseRepository,
        activity_repository: ActivityRepository,
        artifact_repository: ArtifactRepository,
        audit_repository: AuditRepository,
        user: UserContext,
        run_id: UUID,
        now: datetime,
    ) -> None:
        self._case_repository = case_repository
        self._activity_repository = activity_repository
        self._artifact_repository = artifact_repository
        self._audit_repository = audit_repository
        self._user = user
        self._run_id = run_id
        self._now = now
        self._warning_message: str | None = None
        self._result: dict[str, object] = {}

    @property
    def warning_message(self) -> str | None:
        """Return the latest user-visible warning requested by the script."""
        return self._warning_message

    @property
    def result_value(self) -> dict[str, object]:
        """Return the JSON-compatible result requested by the script."""
        return self._result

    def list_cases(self) -> tuple[object, ...]:
        """List cases visible to the effective user."""
        return tuple(self._case_repository.list_cases(self._user))

    def get_case(self, case_id: UUID) -> object | None:
        """Read one case through the normal visibility-aware repository."""
        return self._case_repository.get_case(case_id, self._user)

    def list_artifacts(self) -> tuple[object, ...]:
        """List unassigned artifacts visible to the effective user."""
        return tuple(self._artifact_repository.list_unassigned_artifacts(self._user, limit=50))

    def create_activity(
        self,
        *,
        case_id: UUID,
        description: str,
        due_at: datetime,
    ) -> UUID:
        """Create an application-owned activity after rechecking visibility."""
        if self._case_repository.get_case(case_id, self._user) is None:
            raise PermissionError("The effective user cannot access this case.")
        activity = Activity(
            id=uuid4(),
            case_id=case_id,
            description=description.strip(),
            kind="follow_up",
            due_at=due_at,
            created_at=self._now,
            created_by=self._user.id,
        )
        if not activity.description:
            raise ValueError("Activity description is required.")
        self._activity_repository.save_activity(activity)
        self.audit("automation_activity_created", {"activity_id": str(activity.id)})
        return activity.id

    def warn(self, message: str) -> None:
        """Deliver a bounded warning to the automation owner."""
        normalized = message.strip()
        if normalized:
            self._warning_message = _bounded_text(normalized)

    def result(self, value: Mapping[str, object]) -> None:
        """Set a bounded JSON result for the run log."""
        self._result = _bounded_result(dict(value))

    def audit(self, event_type: str, payload: Mapping[str, object] | None = None) -> None:
        """Write an attributed audit event as the effective user."""
        self._audit_repository.save_event(
            AuditEvent(
                id=uuid4(),
                actor_user_id=self._user.id,
                event_type=event_type,
                subject_id=self._run_id,
                payload_json=dict(payload or {}),
                created_at=self._now,
            )
        )


class AutomationService:
    """Register, match, execute, and recover trusted automations."""

    def __init__(
        self,
        *,
        automation_repository: AutomationRepository,
        case_repository: CaseRepository,
        activity_repository: ActivityRepository,
        artifact_repository: ArtifactRepository,
        audit_repository: AuditRepository,
        runtime: TrustedPythonRuntime | None = None,
    ) -> None:
        self._repository = automation_repository
        self._case_repository = case_repository
        self._activity_repository = activity_repository
        self._artifact_repository = artifact_repository
        self._audit_repository = audit_repository
        self._runtime = runtime or InProcessTrustedPythonRuntime()

    def register_automation(
        self,
        *,
        name: str,
        code: str,
        filesystem_paths: Sequence[str],
        user: UserContext,
        now: datetime,
        automation_id: UUID | None = None,
    ) -> Automation:
        """Register or update code while preserving its SHA-256 provenance."""
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Automation name is required.")
        if not code.strip():
            raise ValueError("Automation code is required.")
        normalized_paths = _normalize_paths(filesystem_paths)
        existing = (
            self._repository.get_automation(automation_id, user)
            if automation_id is not None
            else None
        )
        if automation_id is not None and existing is None:
            raise LookupError("Automation not found.")
        automation = Automation(
            id=automation_id or uuid4(),
            owner_user_id=user.id,
            name=normalized_name,
            code=code,
            code_version=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            enabled=existing.enabled if existing is not None else True,
            effective_user_id=user.id,
            filesystem_paths=normalized_paths,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._repository.save_automation(automation)
        if existing is None:
            self.bind_trigger(
                automation_id=automation.id,
                kind="manual",
                user=user,
                now=now,
            )
        return automation

    def set_enabled(
        self,
        *,
        automation_id: UUID,
        enabled: bool,
        user: UserContext,
        now: datetime,
    ) -> Automation:
        """Enable or disable future runs without changing prior provenance."""
        automation = self._owned(automation_id, user)
        updated = Automation(
            id=automation.id,
            owner_user_id=automation.owner_user_id,
            name=automation.name,
            code=automation.code,
            code_version=automation.code_version,
            enabled=enabled,
            effective_user_id=automation.effective_user_id,
            filesystem_paths=automation.filesystem_paths,
            created_at=automation.created_at,
            updated_at=now,
        )
        self._repository.save_automation(updated)
        return updated

    def bind_trigger(
        self,
        *,
        automation_id: UUID,
        kind: AutomationTriggerKind,
        user: UserContext,
        now: datetime,
        trigger_id: UUID | None = None,
        enabled: bool = True,
        schedule_expression: str | None = None,
        schedule_timezone: str | None = None,
        missed_run_policy: str = "skip",
        overlap_policy: str = "skip",
        event_type: str | None = None,
        sender_filter: str = "",
        recipient_filter: str = "",
        subject_filter: str = "",
    ) -> AutomationTrigger:
        """Attach one explicit trigger to an owned automation."""
        self._owned(automation_id, user)
        if kind == "schedule":
            if not schedule_expression or not schedule_timezone:
                raise ValueError("A schedule needs a cron expression and timezone.")
            _validate_cron(schedule_expression)
            _validate_timezone(schedule_timezone)
        if kind not in {"manual", "schedule", "mail", "artifact"}:
            raise ValueError("Unsupported automation trigger.")
        if missed_run_policy not in {"skip", "run_once"}:
            raise ValueError("Unsupported missed-run policy.")
        if overlap_policy not in {"skip", "queue"}:
            raise ValueError("Unsupported overlap policy.")
        if kind == "mail" and event_type is None:
            event_type = "mail_received"
        if kind == "artifact" and event_type is None:
            event_type = "artifact_ingested"
        trigger = AutomationTrigger(
            id=trigger_id or uuid4(),
            automation_id=automation_id,
            kind=kind,
            enabled=enabled,
            schedule_expression=schedule_expression.strip() if schedule_expression else None,
            schedule_timezone=schedule_timezone,
            missed_run_policy=missed_run_policy,  # type: ignore[arg-type]
            overlap_policy=overlap_policy,  # type: ignore[arg-type]
            event_type=event_type,
            sender_filter=sender_filter.strip(),
            recipient_filter=recipient_filter.strip(),
            subject_filter=subject_filter.strip(),
            created_at=now,
            updated_at=now,
        )
        self._repository.save_trigger(trigger)
        return trigger

    def list_automations(self, *, user: UserContext) -> tuple[Automation, ...]:
        """List definitions owned by the current user."""
        return tuple(self._repository.list_automations(user))

    def get_automation(
        self,
        *,
        automation_id: UUID,
        user: UserContext,
    ) -> tuple[Automation, tuple[AutomationTrigger, ...], tuple[AutomationRun, ...]]:
        """Return the definition, triggers, and recent runs for an owner."""
        automation = self._owned(automation_id, user)
        return (
            automation,
            tuple(self._repository.list_triggers(automation.id, user)),
            tuple(self._repository.list_runs(automation.id, user)),
        )

    def run_manual(
        self,
        *,
        automation_id: UUID,
        user: UserContext,
        now: datetime,
        trigger_id: UUID | None = None,
    ) -> AutomationRun:
        """Run an enabled manual trigger through the durable run path."""
        automation = self._owned(automation_id, user)
        trigger = self._select_trigger(automation, user, trigger_id, "manual")
        return self._queue_and_execute(
            automation=automation,
            trigger=trigger,
            user=user,
            now=now,
            idempotency_key=f"manual:{uuid4()}",
            trigger_label="manual",
        )

    def handle_event(self, *, event: AutomationEvent) -> tuple[AutomationRun, ...]:
        """Run matching committed listeners exactly once per event delivery key."""
        if not event.committed:
            return ()
        # The repository only exposes owner-scoped rows, so the caller must
        # provide the current effective context through handle_event_for_user.
        return self._handle_event_for_user(event=event, user_id=event.user_id)

    def handle_event_for_user(
        self,
        *,
        event: AutomationEvent,
        user: UserContext,
    ) -> tuple[AutomationRun, ...]:
        """Handle one post-commit event using the current effective identity."""
        if not event.committed or event.user_id != user.id:
            return ()
        return self._handle_event_for_user(event=event, user_id=user.id, context=user)

    def run_scheduled(self, *, user: UserContext, now: datetime) -> tuple[AutomationRun, ...]:
        """Evaluate timezone-aware schedules for the current user."""
        runs: list[AutomationRun] = []
        for automation in self._repository.list_automations(user):
            if not automation.enabled:
                continue
            for trigger in self._repository.list_triggers(automation.id, user):
                if not trigger.enabled or trigger.kind != "schedule":
                    continue
                if trigger.schedule_expression is None or trigger.schedule_timezone is None:
                    continue
                local_now = now.astimezone(ZoneInfo(trigger.schedule_timezone))
                slot_time = local_now.replace(second=0, microsecond=0)
                if not _cron_matches(trigger.schedule_expression, slot_time):
                    if trigger.missed_run_policy != "run_once":
                        continue
                    slot_time = _latest_missed_slot(trigger.schedule_expression, slot_time)
                    if slot_time is None:
                        continue
                slot = slot_time.isoformat()
                runs.append(
                    self._queue_and_execute(
                        automation=automation,
                        trigger=trigger,
                        user=user,
                        now=now,
                        idempotency_key=f"schedule:{trigger.id}:{slot}",
                        trigger_label=(
                            f"schedule {trigger.schedule_expression} {trigger.schedule_timezone}"
                        ),
                    )
                )
        return tuple(runs)

    def retry_run(
        self,
        *,
        run_id: UUID,
        user: UserContext,
        now: datetime,
    ) -> AutomationRun:
        """Retry a failed/interrupted run as a new attributed delivery."""
        previous = self._repository.get_run(run_id, user)
        if previous is None:
            raise LookupError("Automation run not found.")
        automation = self._owned(previous.automation_id, user)
        if not automation.enabled:
            raise ValueError("Automation is disabled.")
        triggers = tuple(self._repository.list_triggers(automation.id, user))
        trigger = next(
            (candidate for candidate in triggers if candidate.id == previous.trigger_id), None
        )
        if trigger is None:
            raise LookupError("Automation trigger not found.")
        return self._queue_and_execute(
            automation=automation,
            trigger=trigger,
            user=user,
            now=now,
            idempotency_key=f"retry:{run_id}:{uuid4()}",
            trigger_label=f"retry of {run_id}",
        )

    def recover_after_restart(self, *, now: datetime) -> tuple[AutomationRun, ...]:
        """Mark in-flight work interrupted; operators can retry explicitly."""
        return tuple(self._repository.recover_interrupted_runs(now))

    def _handle_event_for_user(
        self,
        *,
        event: AutomationEvent,
        user_id: UUID,
        context: UserContext | None = None,
    ) -> tuple[AutomationRun, ...]:
        user = context or UserContext(id=user_id, email="", display_name="")
        runs: list[AutomationRun] = []
        for automation in self._repository.list_automations(user):
            if not automation.enabled:
                continue
            for trigger in self._repository.list_triggers(automation.id, user):
                if trigger.enabled and _trigger_matches_event(trigger, event):
                    runs.append(
                        self._queue_and_execute(
                            automation=automation,
                            trigger=trigger,
                            user=user,
                            now=event.occurred_at,
                            idempotency_key=f"event:{event.id}:{trigger.id}",
                            trigger_label=event.event_type,
                        )
                    )
        return tuple(runs)

    def _queue_and_execute(
        self,
        *,
        automation: Automation,
        trigger: AutomationTrigger,
        user: UserContext,
        now: datetime,
        idempotency_key: str,
        trigger_label: str,
    ) -> AutomationRun:
        if not automation.enabled:
            raise ValueError("Automation is disabled.")
        active_runs = tuple(self._repository.list_active_runs(automation.id))
        if active_runs and trigger.overlap_policy == "skip":
            return self._create_skipped_run(
                automation=automation,
                trigger=trigger,
                user=user,
                now=now,
                idempotency_key=idempotency_key,
                trigger_label=f"{trigger_label} (overlap skipped)",
            )
        run = AutomationRun(
            id=uuid4(),
            automation_id=automation.id,
            trigger_id=trigger.id,
            owner_user_id=automation.owner_user_id,
            effective_user_id=user.id,
            idempotency_key=idempotency_key,
            status="queued",
            code_version=automation.code_version,
            configured_paths=automation.filesystem_paths,
            trigger_label=trigger_label,
            queued_at=now,
            started_at=None,
            finished_at=None,
            stdout="",
            stderr="",
            result_json={},
            error_message=None,
            warning_message=None,
            attempt=1,
            created_at=now,
        )
        created = self._repository.create_run(run)
        if created.id != run.id:
            return created
        return self._execute(run=run, automation=automation, trigger=trigger, user=user, now=now)

    def _create_skipped_run(
        self,
        *,
        automation: Automation,
        trigger: AutomationTrigger,
        user: UserContext,
        now: datetime,
        idempotency_key: str,
        trigger_label: str,
    ) -> AutomationRun:
        run = AutomationRun(
            id=uuid4(),
            automation_id=automation.id,
            trigger_id=trigger.id,
            owner_user_id=automation.owner_user_id,
            effective_user_id=user.id,
            idempotency_key=idempotency_key,
            status="skipped",
            code_version=automation.code_version,
            configured_paths=automation.filesystem_paths,
            trigger_label=trigger_label,
            queued_at=now,
            started_at=None,
            finished_at=now,
            stdout="",
            stderr="",
            result_json={},
            error_message=None,
            warning_message="Skipped because another run is still active.",
            attempt=1,
            created_at=now,
        )
        return self._repository.create_run(run)

    def _execute(
        self,
        *,
        run: AutomationRun,
        automation: Automation,
        trigger: AutomationTrigger,
        user: UserContext,
        now: datetime,
    ) -> AutomationRun:
        running = _run_update(run, status="running", started_at=now)
        self._repository.update_run(running)
        api = AutomationAppApi(
            case_repository=self._case_repository,
            activity_repository=self._activity_repository,
            artifact_repository=self._artifact_repository,
            audit_repository=self._audit_repository,
            user=user,
            run_id=run.id,
            now=now,
        )
        try:
            stdout, stderr, result = self._runtime.run(
                automation.code,
                api=api,
                user=user,
                trigger=trigger,
            )
            if api.result_value:
                result = api.result_value
            status: AutomationRunStatus = "warning" if api.warning_message else "succeeded"
            finished = _run_update(
                running,
                status=status,
                finished_at=now,
                stdout=stdout,
                stderr=stderr,
                result_json=result,
                warning_message=api.warning_message,
            )
        except Exception as error:  # trusted script failures belong in the run log
            error_type = getattr(error, "original_type", type(error).__name__)
            captured_stdout = getattr(error, "captured_stdout", "")
            captured_stderr = getattr(error, "captured_stderr", "")
            finished = _run_update(
                running,
                status="failed",
                finished_at=now,
                stdout=captured_stdout,
                stderr=captured_stderr or _bounded_text(str(error)),
                error_message=f"{error_type}: {error}",
            )
        self._repository.update_run(finished)
        self._audit_repository.save_event(
            AuditEvent(
                id=uuid4(),
                actor_user_id=user.id,
                event_type="automation_run_finished",
                subject_id=run.id,
                payload_json={
                    "automation_id": str(automation.id),
                    "status": finished.status,
                    "trigger": trigger.kind,
                },
                created_at=now,
            )
        )
        return finished

    def _owned(self, automation_id: UUID, user: UserContext) -> Automation:
        automation = self._repository.get_automation(automation_id, user)
        if automation is None or automation.owner_user_id != user.id:
            raise LookupError("Automation not found.")
        return automation

    def _select_trigger(
        self,
        automation: Automation,
        user: UserContext,
        trigger_id: UUID | None,
        kind: AutomationTriggerKind,
    ) -> AutomationTrigger:
        triggers = tuple(self._repository.list_triggers(automation.id, user))
        trigger = next(
            (
                candidate
                for candidate in triggers
                if trigger_id is not None and candidate.id == trigger_id
            ),
            None,
        )
        if trigger is None:
            trigger = next((candidate for candidate in triggers if candidate.kind == kind), None)
        if trigger is None or not trigger.enabled:
            raise LookupError("Enabled automation trigger not found.")
        return trigger


def _run_update(run: AutomationRun, **changes: object) -> AutomationRun:
    values = {
        "id": run.id,
        "automation_id": run.automation_id,
        "trigger_id": run.trigger_id,
        "owner_user_id": run.owner_user_id,
        "effective_user_id": run.effective_user_id,
        "idempotency_key": run.idempotency_key,
        "status": run.status,
        "code_version": run.code_version,
        "configured_paths": run.configured_paths,
        "trigger_label": run.trigger_label,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "result_json": run.result_json,
        "error_message": run.error_message,
        "warning_message": run.warning_message,
        "attempt": run.attempt,
        "created_at": run.created_at,
    }
    values.update(changes)
    return AutomationRun(**values)  # type: ignore[arg-type]


def _normalize_paths(paths: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for path in paths:
        value = str(path).strip()
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _bounded_text(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return value
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore") + "\n[output truncated]"


def _bounded_result(value: dict[str, object]) -> dict[str, object]:
    try:
        encoded = json.dumps(value, default=str, ensure_ascii=False)
    except TypeError, ValueError:
        encoded = json.dumps({"value": str(value)}, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        return {"warning": "Automation result exceeded the bounded result size."}
    return value


def _validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown schedule timezone: {name}") from error


def _validate_cron(expression: str) -> None:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron expressions need five fields: minute hour day month weekday.")
    for field, bounds in zip(fields, _CRON_RANGES, strict=True):
        _parse_cron_field(field, bounds)


def _cron_matches(expression: str, value: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        return False
    values = (value.minute, value.hour, value.day, value.month, (value.weekday() + 1) % 7)
    matches = tuple(
        _cron_field_matches(field, current, bounds)
        for field, current, bounds in zip(fields, values, _CRON_RANGES, strict=True)
    )
    # Cron treats day-of-month and day-of-week as an OR when both are wildcards
    # replaced by a value.
    if fields[2] != "*" and fields[4] != "*":
        return matches[0] and matches[1] and matches[3] and (matches[2] or matches[4])
    return all(matches)


def _parse_cron_field(field: str, bounds: tuple[int, int]) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        base, _, raw_step = part.partition("/")
        step = int(raw_step) if raw_step else 1
        if step <= 0:
            raise ValueError("Cron steps must be positive.")
        if base == "*":
            start, end = bounds
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(base)
        if start < bounds[0] or end > bounds[1] or start > end:
            raise ValueError("Cron field is outside its allowed range.")
        values.update(range(start, end + 1, step))
    return values


def _cron_field_matches(field: str, value: int, bounds: tuple[int, int]) -> bool:
    candidates = _parse_cron_field(field, bounds)
    if bounds == (0, 7) and value == 0:
        return 0 in candidates or 7 in candidates
    return value in candidates


def _latest_missed_slot(expression: str, local_now: datetime) -> datetime | None:
    """Return the latest missed minute in the previous day for run-once policy."""
    candidate = local_now - timedelta(minutes=1)
    for _ in range(24 * 60):
        if _cron_matches(expression, candidate):
            return candidate
        candidate -= timedelta(minutes=1)
    return None


def _trigger_matches_event(trigger: AutomationTrigger, event: AutomationEvent) -> bool:
    if trigger.kind not in {"mail", "artifact"}:
        return False
    if trigger.kind == "mail" and event.event_type != (trigger.event_type or "mail_received"):
        return False
    if trigger.kind == "artifact" and event.event_type != (
        trigger.event_type or "artifact_ingested"
    ):
        return False
    metadata = event.mail_metadata
    if trigger.sender_filter:
        if (
            metadata is None
            or (metadata.sender_email or "").casefold() != trigger.sender_filter.casefold()
        ):
            return False
    if trigger.recipient_filter:
        recipients = {
            (recipient.email or "").casefold()
            for recipient in (metadata.recipients if metadata else ())
        }
        if trigger.recipient_filter.casefold() not in recipients:
            return False
    if trigger.subject_filter:
        if (
            metadata is None
            or trigger.subject_filter.casefold() not in (metadata.subject or "").casefold()
        ):
            return False
    return True

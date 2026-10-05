from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from goldenage.adapters.demo import (
    InMemoryActivityRepository,
    InMemoryArtifactRepository,
    InMemoryAuditRepository,
    InMemoryAutomationRepository,
    InMemoryCaseRepository,
    build_demo_state,
)
from goldenage.application.automations import AutomationEvent, AutomationService
from goldenage.domain.models import Artifact, ArtifactMailMetadata, MailParticipant

NOW = datetime(2026, 10, 5, 12, 30, tzinfo=UTC)


def build_automation_service():
    state, user = build_demo_state()
    case_repository = InMemoryCaseRepository(state)
    service = AutomationService(
        automation_repository=InMemoryAutomationRepository(state),
        case_repository=case_repository,
        activity_repository=InMemoryActivityRepository(state, case_repository),
        artifact_repository=InMemoryArtifactRepository(state, case_repository),
        audit_repository=InMemoryAuditRepository(state),
    )
    return service, state, user


def test_manual_run_persists_provenance_result_and_bounded_paths() -> None:
    service, state, user = build_automation_service()
    automation = service.register_automation(
        name="Daily check",
        code="print('ran'); result = {'answer': 42}",
        filesystem_paths=("/srv/report.pdf", "/srv/report.pdf"),
        user=user,
        now=NOW,
    )

    run = service.run_manual(automation_id=automation.id, user=user, now=NOW)

    assert run.status == "succeeded"
    assert run.stdout == "ran\n"
    assert run.result_json == {"answer": 42}
    assert run.owner_user_id == user.id
    assert run.effective_user_id == user.id
    assert run.configured_paths == ("/srv/report.pdf",)
    assert run.code_version == automation.code_version
    assert state.automation_runs[run.id] == run


def test_script_failure_and_warning_are_visible_and_retryable() -> None:
    service, _, user = build_automation_service()
    warning_automation = service.register_automation(
        name="Warn",
        code="automation.warn('Required field is missing')",
        filesystem_paths=(),
        user=user,
        now=NOW,
    )
    failed_automation = service.register_automation(
        name="Fail",
        code="raise RuntimeError('bad input')",
        filesystem_paths=(),
        user=user,
        now=NOW,
    )

    warning_run = service.run_manual(automation_id=warning_automation.id, user=user, now=NOW)
    failed_run = service.run_manual(automation_id=failed_automation.id, user=user, now=NOW)

    assert warning_run.status == "warning"
    assert warning_run.warning_message == "Required field is missing"
    assert failed_run.status == "failed"
    assert failed_run.error_message == "RuntimeError: bad input"
    retry = service.retry_run(run_id=failed_run.id, user=user, now=NOW)
    assert retry.id != failed_run.id
    assert retry.status == "failed"


def test_mail_event_filters_and_duplicate_delivery_are_idempotent() -> None:
    service, state, user = build_automation_service()
    automation = service.register_automation(
        name="Mail triage",
        code="result = {'matched': True}",
        filesystem_paths=(),
        user=user,
        now=NOW,
    )
    trigger = service.bind_trigger(
        automation_id=automation.id,
        kind="mail",
        event_type="mail_received",
        sender_filter="sender@example.com",
        recipient_filter="owner@example.com",
        subject_filter="invoice",
        user=user,
        now=NOW,
    )
    artifact = Artifact(
        id=uuid4(),
        file_name="mail.eml",
        media_type="message/rfc822",
        size_bytes=1,
        content_text="invoice",
        storage_key="mail.eml",
        uploaded_at=NOW,
        uploaded_by=user.id,
    )
    metadata = ArtifactMailMetadata(
        artifact_id=artifact.id,
        source_system="desktop_mail_client",
        message_format="rfc822_email",
        parse_status="parsed",
        external_message_id="external-1",
        rfc_message_id=None,
        source_account=None,
        source_mailbox="Inbox",
        subject="Invoice for October",
        sender_name="Sender",
        sender_email="sender@example.com",
        sender_domain="example.com",
        recipients=(MailParticipant(name="Owner", email="owner@example.com"),),
        sent_at=NOW,
        created_at=NOW,
    )
    event = AutomationEvent(
        id="mail-message-1",
        event_type="mail_received",
        user_id=user.id,
        occurred_at=NOW,
        artifact=artifact,
        mail_metadata=metadata,
    )

    first = service.handle_event_for_user(event=event, user=user)
    second = service.handle_event_for_user(event=event, user=user)

    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert first[0].trigger_id == trigger.id
    assert len(state.automation_runs) == 1


def test_schedule_uses_explicit_timezone_and_disabled_automation_does_not_run() -> None:
    service, _, user = build_automation_service()
    automation = service.register_automation(
        name="Berlin schedule",
        code="result = {'scheduled': True}",
        filesystem_paths=(),
        user=user,
        now=NOW,
    )
    service.bind_trigger(
        automation_id=automation.id,
        kind="schedule",
        schedule_expression="30 14 * * *",
        schedule_timezone="Europe/Berlin",
        user=user,
        now=NOW,
    )

    runs = service.run_scheduled(user=user, now=NOW)
    assert len(runs) == 1
    assert runs[0].status == "succeeded"

    service.set_enabled(automation_id=automation.id, enabled=False, user=user, now=NOW)
    try:
        service.run_manual(automation_id=automation.id, user=user, now=NOW)
    except ValueError as error:
        assert str(error) == "Automation is disabled."
    else:  # pragma: no cover - defensive assertion for the acceptance behavior
        raise AssertionError("disabled automation started a run")


def test_execution_rechecks_current_visibility_and_restart_marks_running_interrupted() -> None:
    service, state, user = build_automation_service()
    group_id = uuid4()
    case_id = next(iter(state.cases))
    state.cases[case_id] = replace(state.cases[case_id], visible_group_id=group_id)
    current_user = replace(user, visible_group_ids=frozenset({group_id}))
    automation = service.register_automation(
        name="Permission check",
        code="result = {'visible': len(automation.list_cases())}",
        filesystem_paths=(),
        user=user,
        now=NOW,
    )
    run = service.run_manual(automation_id=automation.id, user=current_user, now=NOW)
    assert run.result_json == {"visible": len(state.cases)}

    repository = state.automation_runs
    pending = replace(run, id=uuid4(), status="running", finished_at=None)
    repository[pending.id] = pending
    recovered = service.recover_after_restart(now=NOW)
    assert any(item.id == pending.id and item.status == "interrupted" for item in recovered)

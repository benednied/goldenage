from datetime import UTC, datetime
from uuid import UUID

import pytest

from goldenage.domain.models import Activity
from goldenage.domain.rules import ResolutionError, build_resolution_plan, due_label


def test_due_label_distinguishes_overdue_today_and_upcoming() -> None:
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

    assert due_label(datetime(2026, 4, 11, 18, 0, tzinfo=UTC), now) == "overdue"
    assert due_label(datetime(2026, 4, 12, 23, 0, tzinfo=UTC), now) == "today"
    assert due_label(datetime(2026, 4, 13, 9, 0, tzinfo=UTC), now) == "upcoming"


def test_resolution_requires_exactly_one_path() -> None:
    activity = Activity(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
        case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
        description="Review the latest incoming file.",
        kind="follow_up",
        due_at=datetime(2026, 4, 12, 8, 0, tzinfo=UTC),
        created_at=datetime(2026, 4, 11, 8, 0, tzinfo=UTC),
    )

    with pytest.raises(ResolutionError):
        build_resolution_plan(
            activity,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
            actor_user_id=None,
            next_step="",
            next_due_at=None,
            close_case=False,
            skip_follow_up=False,
        )


def test_resolution_rejects_completed_activity() -> None:
    activity = Activity(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
        case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
        description="Review the latest incoming file.",
        kind="follow_up",
        due_at=datetime(2026, 4, 12, 8, 0, tzinfo=UTC),
        created_at=datetime(2026, 4, 11, 8, 0, tzinfo=UTC),
        completed_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(ResolutionError, match="already completed"):
        build_resolution_plan(
            activity,
            now=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
            actor_user_id=None,
            next_step="Call Max",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            close_case=False,
            skip_follow_up=False,
        )


def test_resolution_rejects_conflicting_and_incomplete_follow_up_paths() -> None:
    activity = Activity(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
        case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
        description="Review the latest incoming file.",
        kind="follow_up",
        due_at=datetime(2026, 4, 12, 8, 0, tzinfo=UTC),
        created_at=datetime(2026, 4, 11, 8, 0, tzinfo=UTC),
    )
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

    with pytest.raises(ResolutionError, match="exactly one"):
        build_resolution_plan(
            activity,
            now=now,
            actor_user_id=None,
            next_step="Call Max",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            close_case=True,
            skip_follow_up=False,
        )

    with pytest.raises(ResolutionError, match="description"):
        build_resolution_plan(
            activity,
            now=now,
            actor_user_id=None,
            next_step=" ",
            next_due_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
            close_case=False,
            skip_follow_up=False,
        )

    with pytest.raises(ResolutionError, match="due date"):
        build_resolution_plan(
            activity,
            now=now,
            actor_user_id=None,
            next_step="Call Max",
            next_due_at=None,
            close_case=False,
            skip_follow_up=False,
        )


def test_resolution_returns_close_and_skip_paths_without_follow_up() -> None:
    activity = Activity(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
        case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
        description="Review the latest incoming file.",
        kind="follow_up",
        due_at=datetime(2026, 4, 12, 8, 0, tzinfo=UTC),
        created_at=datetime(2026, 4, 11, 8, 0, tzinfo=UTC),
    )
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

    close_plan = build_resolution_plan(
        activity,
        now=now,
        actor_user_id=None,
        next_step=None,
        next_due_at=None,
        close_case=True,
        skip_follow_up=False,
    )
    skip_plan = build_resolution_plan(
        activity,
        now=now,
        actor_user_id=None,
        next_step=None,
        next_due_at=None,
        close_case=False,
        skip_follow_up=True,
    )

    assert close_plan.close_case is True
    assert close_plan.follow_up_activity is None
    assert skip_plan.skip_follow_up is True
    assert skip_plan.follow_up_activity is None


def test_resolution_builds_follow_up_when_next_step_is_given() -> None:
    activity = Activity(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"),
        case_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
        description="Review the latest incoming file.",
        kind="follow_up",
        due_at=datetime(2026, 4, 12, 8, 0, tzinfo=UTC),
        created_at=datetime(2026, 4, 11, 8, 0, tzinfo=UTC),
    )
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

    plan = build_resolution_plan(
        activity,
        now=now,
        actor_user_id=UUID("11111111-1111-1111-1111-111111111111"),
        next_step="Send the revised clause language.",
        next_due_at=datetime(2026, 4, 14, 10, 0, tzinfo=UTC),
        close_case=False,
        skip_follow_up=False,
    )

    assert plan.follow_up_activity is not None
    assert plan.follow_up_activity.description == "Send the revised clause language."
    assert plan.completed_at == now

from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from goldenage.domain.models import Activity
from goldenage.domain.rules import ResolutionError, build_resolution_plan, due_label


def test_due_label_distinguishes_overdue_today_and_upcoming() -> None:
    now = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)

    assert due_label(datetime(2026, 4, 11, 18, 0, tzinfo=UTC), now) == "overdue"
    assert due_label(datetime(2026, 4, 12, 23, 0, tzinfo=UTC), now) == "today"
    assert due_label(datetime(2026, 4, 13, 9, 0, tzinfo=UTC), now) == "upcoming"


@pytest.mark.parametrize(
    ("now", "due_at", "expected"),
    [
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 1, 14, 22, 0, tzinfo=UTC),
            "overdue",
        ),
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 1, 15, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            "today",
        ),
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 1, 15, 5, 30, tzinfo=UTC),
            "today",
        ),
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 1, 15, 22, 0, tzinfo=UTC),
            "today",
        ),
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 1, 15, 23, 0, tzinfo=UTC),
            "upcoming",
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
            "overdue",
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 7, 15, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            "today",
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 7, 15, 6, 0, tzinfo=UTC),
            "today",
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 7, 15, 21, 0, tzinfo=UTC),
            "today",
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 7, 15, 22, 0, tzinfo=UTC),
            "upcoming",
        ),
        (
            datetime(2026, 3, 29, 4, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 3, 29, 0, 30, tzinfo=ZoneInfo("Europe/Berlin")),
            "today",
        ),
        (
            datetime(2026, 10, 25, 4, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 10, 25, 0, 30, tzinfo=ZoneInfo("Europe/Berlin")),
            "today",
        ),
        (
            datetime(2026, 10, 4, 23, 59, tzinfo=ZoneInfo("Europe/Berlin")),
            datetime(2026, 10, 4, 0, 30, tzinfo=ZoneInfo("Europe/Berlin")),
            "today",
        ),
    ],
)
def test_due_label_uses_frozen_local_calendar_date(
    now: datetime,
    due_at: datetime,
    expected: str,
) -> None:
    assert due_label(due_at, now) == expected


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

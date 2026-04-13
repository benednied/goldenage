"""Pure business rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from goldenage.domain.models import Activity, DueLabel


class ResolutionError(ValueError):
    """Raised when an activity resolution violates workflow rules."""


@dataclass(frozen=True, slots=True)
class ResolutionPlan:
    """Validated resolution intent for a due activity."""

    completed_at: datetime
    follow_up_activity: Activity | None
    close_case: bool
    skip_follow_up: bool


def due_label(due_at: datetime, now: datetime) -> DueLabel:
    """Map a due timestamp to the dashboard label."""
    due_date = due_at.astimezone(now.tzinfo).date()
    today = now.date()
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    return "upcoming"


def build_resolution_plan(
    activity: Activity,
    *,
    now: datetime,
    actor_user_id: UUID | None,
    next_step: str | None,
    next_due_at: datetime | None,
    close_case: bool,
    skip_follow_up: bool,
) -> ResolutionPlan:
    """Enforce that a due activity always resolves into a clear next state."""
    if activity.completed_at is not None:
        raise ResolutionError("This activity is already completed.")

    normalized_step = (next_step or "").strip()
    has_next_step = bool(normalized_step or next_due_at)

    selected_paths = sum([close_case, skip_follow_up, has_next_step])
    if selected_paths == 0:
        raise ResolutionError(
            "Resolving a due activity requires closing the case, scheduling the next step, "
            "or explicitly recording that no follow-up is needed."
        )
    if selected_paths > 1:
        raise ResolutionError("Choose exactly one resolution path.")

    if has_next_step and not normalized_step:
        raise ResolutionError("A follow-up activity needs a description.")
    if has_next_step and next_due_at is None:
        raise ResolutionError("A follow-up activity needs a due date.")

    follow_up_activity: Activity | None = None
    if has_next_step:
        follow_up_activity = Activity(
            id=uuid4(),
            case_id=activity.case_id,
            description=normalized_step,
            kind="follow_up",
            due_at=next_due_at,
            created_at=now,
            created_by=actor_user_id,
        )

    return ResolutionPlan(
        completed_at=now,
        follow_up_activity=follow_up_activity,
        close_case=close_case,
        skip_follow_up=skip_follow_up,
    )

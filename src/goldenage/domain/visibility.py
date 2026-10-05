"""Shared visibility rules for cases, intake artifacts, and conversations.

The persistence adapters repeat the SQL form of these rules at their query
boundaries.  Keeping the policy here as well gives the in-memory adapter and
application workflows one named definition to follow.
"""

from __future__ import annotations

from uuid import UUID

from goldenage.domain.models import Artifact, CaseFile, UserContext


def case_is_visible(case_file: CaseFile, user: UserContext) -> bool:
    """Return whether a case is public or belongs to one of the user's groups."""
    return (
        case_file.visible_group_id is None or case_file.visible_group_id in user.visible_group_ids
    )


def unassigned_artifact_is_visible(artifact: Artifact, user: UserContext) -> bool:
    """Return whether an unassigned artifact is shared or owned by the user."""
    return artifact.uploaded_by is None or artifact.uploaded_by == user.id


def new_case_visibility_group_id(user: UserContext) -> UUID | None:
    """Choose the stable group visibility for a case created by ``user``.

    ``CaseFile`` currently supports one visibility group, while a user may be
    a member of several groups.  A deterministic choice keeps a new case
    private to one of the creator's groups until case-sharing supports a
    multi-group relation.  With no groups, ``None`` preserves the existing
    shared/public case semantics.
    """
    return min(user.visible_group_ids, key=str) if user.visible_group_ids else None

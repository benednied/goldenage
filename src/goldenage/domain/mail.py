"""Shared mail identity and subject-normalization rules."""

from __future__ import annotations

import re
from collections.abc import Iterable

from goldenage.domain.models import MailParticipant

_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:aw|re|fw|fwd|wg|sv|antwort|reply)(?:\[\d+\])?\s*:(?:\s+|$)",
    re.IGNORECASE,
)
_MAX_SUBJECT_PREFIXES = 10


def strip_mail_subject_prefixes(subject: str) -> str:
    """Remove a bounded chain of localized reply/forward prefixes."""
    normalized = subject.strip()
    for _ in range(_MAX_SUBJECT_PREFIXES):
        stripped = _SUBJECT_PREFIX_RE.sub("", normalized, count=1)
        if stripped == normalized:
            break
        normalized = stripped.strip()
    return normalized


def normalize_mail_subject(subject: str | None) -> str | None:
    """Return a stable, case-insensitive subject identity."""
    if subject is None:
        return None
    normalized = strip_mail_subject_prefixes(subject)
    normalized = " ".join(normalized.split()).casefold()
    return normalized or None


def mail_participant_keys(participants: Iterable[MailParticipant]) -> tuple[str, ...]:
    """Return comparable participant identities, preferring email addresses."""
    keys: set[str] = set()
    for participant in participants:
        if participant.email and participant.email.strip():
            keys.add(participant.email.strip().casefold())
        elif participant.name and participant.name.strip():
            keys.add("name:" + " ".join(participant.name.split()).casefold())
    return tuple(sorted(keys))


def same_mail_participants(
    left: Iterable[MailParticipant],
    right: Iterable[MailParticipant],
) -> bool:
    """Return whether both messages have the same non-empty participant set."""
    left_keys = mail_participant_keys(left)
    return bool(left_keys) and left_keys == mail_participant_keys(right)

"""Windows classic Outlook mailbox ingestion scaffolding."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from goldenage.application.ports import MailboxSource
from goldenage.config import Settings
from goldenage.domain.models import ExtractedArtifactData, MailParticipant


class OutlookMailboxUnavailable(RuntimeError):
    """Raised when the local Outlook integration cannot run."""


@dataclass(frozen=True, slots=True)
class OutlookMailboxMessage:
    """Normalized local Outlook message payload."""

    account_name: str
    folder_key: str
    message_key: str
    conversation_key: str | None
    internet_message_id: str | None
    subject: str | None
    sender_name: str | None
    sender_email: str | None
    recipients: tuple[MailParticipant, ...]
    body_text: str
    sent_at: datetime | None
    received_at: datetime | None
    direction: str


def normalize_outlook_message(message: OutlookMailboxMessage) -> ExtractedArtifactData:
    """Map a local Outlook message into the shared extraction shape."""
    sender = None
    if message.sender_name or message.sender_email:
        sender = MailParticipant(name=message.sender_name, email=message.sender_email)
    return ExtractedArtifactData(
        source_kind="outlook_mailbox_message",
        parse_status="parsed",
        content_text=message.body_text,
        subject=message.subject,
        sender=sender,
        recipients=message.recipients,
        sent_at=message.sent_at,
        received_at=message.received_at,
        direction=message.direction,  # type: ignore[arg-type]
        source_account_id=message.account_name,
        source_folder_id=message.folder_key,
        source_message_id=message.message_key,
        conversation_id=message.conversation_key,
        internet_message_id=message.internet_message_id,
    )


class WindowsOutlookMailboxSource(MailboxSource):
    """Classic Outlook COM/MAPI watcher."""

    def __init__(self, settings: Settings, on_message: Callable[[OutlookMailboxMessage], None]) -> None:
        self._settings = settings
        self._on_message = on_message
        self._stop_event = threading.Event()

    def watch_forever(self) -> None:
        if sys.platform != "win32":
            raise OutlookMailboxUnavailable("Classic Outlook intake is only available on Windows.")
        if not self._settings.outlook_account_name:
            raise OutlookMailboxUnavailable("No Outlook account is configured.")
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:  # pragma: no cover - Windows-only guard.
            raise OutlookMailboxUnavailable(
                "pywin32 is required for classic Outlook mailbox intake."
            ) from exc

        pythoncom.CoInitialize()
        try:  # pragma: no cover - Windows-only runtime path.
            win32com.client.Dispatch("Outlook.Application")
            while not self._stop_event.is_set():
                time.sleep(max(self._settings.outlook_poll_seconds, 5))
        finally:
            pythoncom.CoUninitialize()

    def stop(self) -> None:
        self._stop_event.set()


class OutlookMailboxWorker:
    """Background wrapper around the Windows mailbox source."""

    def __init__(self, source: MailboxSource) -> None:
        self._source = source
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._source.watch_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        stop = getattr(self._source, "stop", None)
        if callable(stop):
            stop()

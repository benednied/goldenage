"""Windows classic Outlook mailbox ingestion."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any, Callable, cast

from goldenage.application.ports import MailboxSource
from goldenage.config import Settings
from goldenage.domain.models import ExtractedArtifactData, MailDirection, MailParticipant


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
            outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            store = _find_store(namespace, self._settings.outlook_account_name)
            folders = _watched_folders(store)
            seen_keys: set[str] = set()
            while not self._stop_event.is_set():
                for direction, folder in folders:
                    for message in _recent_messages(folder):
                        normalized = _normalize_com_message(
                            message,
                            account_name=store.DisplayName,
                            folder_key=_folder_key(folder),
                            direction=direction,
                        )
                        if normalized is None or normalized.message_key in seen_keys:
                            continue
                        seen_keys.add(normalized.message_key)
                        self._on_message(normalized)
                time.sleep(max(self._settings.outlook_poll_seconds, 5))
        finally:
            pythoncom.CoUninitialize()

    def stop(self) -> None:
        self._stop_event.set()


def _find_store(namespace: Any, account_name: str) -> Any:
    stores = namespace.Stores
    for index in range(1, int(stores.Count) + 1):
        store = stores.Item(index)
        if str(store.DisplayName).lower() == account_name.lower():
            return store
    raise OutlookMailboxUnavailable(f"Outlook account not found: {account_name}")


def _watched_folders(store: Any) -> tuple[tuple[str, Any], ...]:
    inbox = store.GetDefaultFolder(6)
    sent = store.GetDefaultFolder(5)
    return (("inbound", inbox), ("outbound", sent))


def _recent_messages(folder: Any, limit: int = 25) -> tuple[Any, ...]:
    items = folder.Items
    items.Sort("[ReceivedTime]", True)
    messages = []
    count = min(int(items.Count), limit)
    for index in range(1, count + 1):
        message = items.Item(index)
        if int(getattr(message, "Class", 0)) == 43:
            messages.append(message)
    return tuple(messages)


def _normalize_com_message(
    message: Any,
    *,
    account_name: str,
    folder_key: str,
    direction: str,
) -> OutlookMailboxMessage | None:
    message_key = _string_attr(message, "EntryID")
    if not message_key:
        return None
    sender_name = _string_attr(message, "SenderName")
    sender_email = _sender_email(message)
    return OutlookMailboxMessage(
        account_name=account_name,
        folder_key=folder_key,
        message_key=message_key,
        conversation_key=_string_attr(message, "ConversationID"),
        internet_message_id=_internet_message_id(message),
        subject=_string_attr(message, "Subject"),
        sender_name=sender_name,
        sender_email=sender_email,
        recipients=_recipients(message),
        body_text=_string_attr(message, "Body") or "",
        sent_at=_datetime_attr(message, "SentOn"),
        received_at=_datetime_attr(message, "ReceivedTime"),
        direction=cast(MailDirection, direction),
    )


def _folder_key(folder: Any) -> str:
    return _string_attr(folder, "EntryID") or _string_attr(folder, "Name") or "unknown"


def _recipients(message: Any) -> tuple[MailParticipant, ...]:
    recipients = getattr(message, "Recipients", None)
    if recipients is None:
        return ()
    parsed = []
    for index in range(1, int(recipients.Count) + 1):
        recipient = recipients.Item(index)
        name = _string_attr(recipient, "Name")
        email = _string_attr(recipient, "Address")
        parsed.append(MailParticipant(name=name, email=email))
    return tuple(parsed)


def _sender_email(message: Any) -> str | None:
    email = _string_attr(message, "SenderEmailAddress")
    sender_name, parsed_email = parseaddr(email or "")
    if "@" in parsed_email:
        return parsed_email
    if "@" in (email or ""):
        return email
    sender = getattr(message, "Sender", None)
    if sender is not None:
        exchange_user = getattr(sender, "GetExchangeUser", lambda: None)()
        if exchange_user is not None:
            smtp_address = _string_attr(exchange_user, "PrimarySmtpAddress")
            if smtp_address:
                return smtp_address
    return None


def _internet_message_id(message: Any) -> str | None:
    accessor = getattr(message, "PropertyAccessor", None)
    if accessor is None:
        return None
    try:
        return accessor.GetProperty(
            "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
        )
    except Exception:
        return None


def _string_attr(obj: Any, name: str) -> str | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime_attr(obj: Any, name: str) -> datetime | None:
    value = getattr(obj, name, None)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


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

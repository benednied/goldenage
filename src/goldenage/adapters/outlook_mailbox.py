"""Windows classic Outlook mailbox ingestion."""

from __future__ import annotations

import sys
import threading
import time  # noqa: F401 - retained as a patch seam for legacy Windows tests.
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any, Callable, Literal, cast

from goldenage.application.ports import MailboxCheckpointRepository, MailboxSource
from goldenage.config import Settings
from goldenage.domain.models import (
    ExtractedArtifactData,
    MailboxAccountConfig,
    MailboxSyncCheckpoint,
    MailDirection,
    MailParticipant,
)


class OutlookMailboxUnavailable(RuntimeError):
    """Raised when the local Outlook integration cannot run."""


class OutlookMailboxDeliveryDeferred(RuntimeError):
    """Raised by a callback when delivery should remain pending."""


MailboxWorkerState = Literal["stopped", "starting", "running", "stopping", "failed"]


@dataclass(frozen=True, slots=True)
class MailboxWorkerHealth:
    """Safe operational state for a mailbox worker.

    Only source identifiers and exception summaries are retained. Mail subjects and
    bodies are deliberately excluded from this status object.
    """

    state: MailboxWorkerState
    thread_alive: bool
    last_success_at: datetime | None = None
    last_success_folder_key: str | None = None
    last_success_message_key: str | None = None
    last_failure_at: datetime | None = None
    last_failure_folder_key: str | None = None
    last_failure_message_key: str | None = None
    last_failure_error: str | None = None
    quarantined_message_keys: tuple[str, ...] = ()
    restart_count: int = 0


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
        direction=cast(MailDirection, message.direction),
        source_account_id=message.account_name,
        source_folder_id=message.folder_key,
        source_message_id=message.message_key,
        conversation_id=message.conversation_key,
        internet_message_id=message.internet_message_id,
    )


class WindowsOutlookMailboxSource(MailboxSource):
    """Classic Outlook COM/MAPI watcher with durable delivery checkpoints."""

    def __init__(
        self,
        settings: Settings,
        on_message: Callable[[OutlookMailboxMessage], bool | None],
        *,
        checkpoint_repository: MailboxCheckpointRepository | None = None,
        account_config: MailboxAccountConfig | None = None,
    ) -> None:
        self._settings = settings
        self._on_message = on_message
        self._checkpoint_repository = checkpoint_repository
        self._account_config = account_config
        self._stop_event = threading.Event()
        self._health_lock = threading.Lock()
        self._last_success_at: datetime | None = None
        self._last_success_folder_key: str | None = None
        self._last_success_message_key: str | None = None
        self._last_failure_at: datetime | None = None
        self._last_failure_folder_key: str | None = None
        self._last_failure_message_key: str | None = None
        self._last_failure_error: str | None = None
        self._quarantined_message_keys: set[str] = set()
        self._legacy_seen_message_keys: set[str] = set()

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
            while not self._stop_event.is_set():
                checkpoints = self._load_checkpoints()
                for direction, folder in folders:
                    if self._stop_event.is_set():
                        break
                    self._process_folder(
                        folder=folder,
                        account_name=store.DisplayName,
                        direction=direction,
                        checkpoint=checkpoints.get(_folder_key(folder)),
                    )
                self._stop_event.wait(max(self._settings.outlook_poll_seconds, 1))
        finally:
            pythoncom.CoUninitialize()

    def stop(self) -> None:
        self._stop_event.set()

    def reset(self) -> None:
        """Allow a stopped source to be started again by its worker."""
        self._stop_event.clear()

    @property
    def health(self) -> MailboxWorkerHealth:
        """Return delivery state without exposing message content."""
        with self._health_lock:
            return MailboxWorkerHealth(
                state="running" if not self._stop_event.is_set() else "stopping",
                thread_alive=False,
                last_success_at=self._last_success_at,
                last_success_folder_key=self._last_success_folder_key,
                last_success_message_key=self._last_success_message_key,
                last_failure_at=self._last_failure_at,
                last_failure_folder_key=self._last_failure_folder_key,
                last_failure_message_key=self._last_failure_message_key,
                last_failure_error=self._last_failure_error,
                quarantined_message_keys=tuple(sorted(self._quarantined_message_keys)),
            )

    def _load_checkpoints(self) -> dict[str, MailboxSyncCheckpoint]:
        if self._checkpoint_repository is None or self._account_config is None:
            return {}
        checkpoints = self._checkpoint_repository.list_mailbox_sync_checkpoints(
            self._account_config.id
        )
        return {checkpoint.folder_key: checkpoint for checkpoint in checkpoints}

    def _process_folder(
        self,
        *,
        folder: Any,
        account_name: str,
        direction: str,
        checkpoint: MailboxSyncCheckpoint | None,
    ) -> None:
        folder_key = _folder_key(folder)
        pending = _pending_messages(
            folder,
            checkpoint=checkpoint,
            page_size=max(self._settings.outlook_scan_per_folder_limit, 1),
        )
        folder_blocked = False
        for raw_message in pending:
            if self._stop_event.is_set():
                return
            normalized = _normalize_com_message(
                raw_message,
                account_name=account_name,
                folder_key=folder_key,
                direction=direction,
            )
            if normalized is None:
                continue
            if normalized.message_key in self._quarantined_message_keys:
                folder_blocked = True
                continue
            if (
                self._checkpoint_repository is None or self._account_config is None
            ) and normalized.message_key in self._legacy_seen_message_keys:
                continue
            delivered = self._deliver(normalized)
            if not delivered:
                folder_blocked = True
                continue
            if folder_blocked:
                continue
            if self._checkpoint_repository is None or self._account_config is None:
                self._legacy_seen_message_keys.add(normalized.message_key)
                self._record_success(normalized)
                continue
            checkpoint = MailboxSyncCheckpoint(
                account_config_id=self._account_config.id,
                folder_key=folder_key,
                last_message_key=normalized.message_key,
                last_message_at=_message_timestamp(normalized),
                updated_at=datetime.now(UTC),
            )
            try:
                self._checkpoint_repository.save_mailbox_sync_checkpoint(checkpoint)
            except Exception as exc:
                self._record_failure(normalized, exc)
                folder_blocked = True
                continue
            self._record_success(normalized)

    def _deliver(self, message: OutlookMailboxMessage) -> bool:
        max_attempts = max(self._settings.outlook_delivery_max_attempts, 1)
        for attempt in range(max_attempts):
            if self._stop_event.is_set():
                return False
            try:
                accepted = self._on_message(message)
                if accepted is False:
                    raise OutlookMailboxDeliveryDeferred("callback deferred delivery")
            except Exception as exc:
                if attempt + 1 < max_attempts:
                    delay = self._settings.outlook_retry_backoff_seconds * (2**attempt)
                    self._stop_event.wait(delay)
                    continue
                self._record_failure(message, exc)
                with self._health_lock:
                    self._quarantined_message_keys.add(message.message_key)
                return False
            return True
        return False

    def _record_success(self, message: OutlookMailboxMessage) -> None:
        with self._health_lock:
            self._last_success_at = datetime.now(UTC)
            self._last_success_folder_key = message.folder_key
            self._last_success_message_key = message.message_key

    def _record_failure(self, message: OutlookMailboxMessage, exc: Exception) -> None:
        with self._health_lock:
            self._last_failure_at = datetime.now(UTC)
            self._last_failure_folder_key = message.folder_key
            self._last_failure_message_key = message.message_key
            self._last_failure_error = _safe_error(exc)


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


def _recent_messages(folder: Any, limit: int = 25, offset: int = 0) -> tuple[Any, ...]:
    """Return one bounded page of Outlook mail items, newest first."""
    items = folder.Items
    items.Sort("[ReceivedTime]", True)
    messages = []
    count = min(max(int(items.Count) - offset, 0), limit)
    for index in range(offset + 1, offset + count + 1):
        message = items.Item(index)
        if int(getattr(message, "Class", 0)) == 43:
            messages.append(message)
    return tuple(messages)


def _pending_messages(
    folder: Any,
    *,
    checkpoint: MailboxSyncCheckpoint | None,
    page_size: int,
) -> tuple[Any, ...]:
    """Page through the folder until the durable checkpoint or its end.

    Outlook exposes a sorted collection rather than a cursor. Pages are fetched
    newest-first, then reversed so delivery and checkpoint advancement are oldest
    first. This keeps a burst larger than one page from being silently skipped.
    """
    pages: list[Any] = []
    offset = 0
    checkpoint_index: int | None = None
    raw_count = int(folder.Items.Count)
    while True:
        page = _recent_messages(folder, limit=page_size, offset=offset)
        if not page:
            if offset + page_size >= raw_count:
                break
            offset += page_size
            continue
        pages.extend(page)
        if checkpoint is not None and checkpoint.last_message_key is not None:
            for index, message in enumerate(pages):
                if _string_attr(message, "EntryID") == checkpoint.last_message_key:
                    checkpoint_index = index
                    break
            if checkpoint_index is not None:
                break
        offset += page_size
        if offset >= raw_count:
            break

    if checkpoint_index is not None:
        pages = pages[:checkpoint_index]
    elif checkpoint is not None and checkpoint.last_message_at is not None:
        pages = [message for message in pages if _after_checkpoint(message, checkpoint)]
    pages.reverse()
    return tuple(pages)


def _message_timestamp(message: OutlookMailboxMessage) -> datetime | None:
    return message.received_at or message.sent_at


def _after_checkpoint(message: Any, checkpoint: MailboxSyncCheckpoint) -> bool:
    timestamp = _datetime_attr(message, "ReceivedTime") or _datetime_attr(message, "SentOn")
    if timestamp is None or checkpoint.last_message_at is None:
        return True
    return timestamp >= checkpoint.last_message_at


def _safe_error(exc: Exception) -> str:
    """Summarize a failure without retaining exception text from a mail callback."""
    error_type = type(exc).__name__
    return f"{error_type}: mailbox delivery failed"


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
        return accessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x1035001F")
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
    """Background wrapper with observable restart and bounded shutdown semantics."""

    def __init__(self, source: MailboxSource, *, shutdown_timeout_seconds: float = 5.0) -> None:
        self._source = source
        self._shutdown_timeout_seconds = max(shutdown_timeout_seconds, 0.0)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: MailboxWorkerState = "stopped"
        self._last_failure_error: str | None = None
        self._restart_count = 0
        self._has_started = False
        self._started_event: threading.Event | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            reset = getattr(self._source, "reset", None)
            if callable(reset):
                reset()
            self._state = "starting"
            self._last_failure_error = None
            if self._has_started:
                self._restart_count += 1
            self._has_started = True
            self._started_event = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            started_event = self._started_event
        if started_event is not None:
            started_event.wait(timeout=1.0)

    def stop(self) -> None:
        stop = getattr(self._source, "stop", None)
        if callable(stop):
            stop()
        with self._lock:
            thread = self._thread
            if thread is None:
                self._state = "stopped"
                return
            self._state = "stopping"
        thread.join(timeout=self._shutdown_timeout_seconds)
        with self._lock:
            if thread.is_alive():
                self._state = "stopping"
            else:
                self._thread = None
                self._state = "stopped"

    def restart(self) -> None:
        """Restart a dead or stopped worker after a failed source run."""
        self.stop()
        self.start()

    def get_health(self) -> MailboxWorkerHealth:
        """Return worker and source delivery health."""
        with self._lock:
            state = self._state
            thread = self._thread
            worker_error = self._last_failure_error
            restart_count = self._restart_count
        source_health = getattr(self._source, "health", None)
        if callable(source_health):
            source_health = source_health()
        if not isinstance(source_health, MailboxWorkerHealth):
            source_health = MailboxWorkerHealth(state=state, thread_alive=False)
        return replace(
            source_health,
            state=state,
            thread_alive=thread.is_alive() if thread is not None else False,
            last_failure_error=worker_error or source_health.last_failure_error,
            restart_count=restart_count,
        )

    @property
    def health(self) -> MailboxWorkerHealth:
        """Property alias for callers that expose health as a resource."""
        return self.get_health()

    def _run(self) -> None:
        with self._lock:
            self._state = "running"
            if self._started_event is not None:
                self._started_event.set()
        try:
            self._source.watch_forever()
        except Exception as exc:
            with self._lock:
                self._last_failure_error = _safe_error(exc)
                self._state = "failed"
            return
        with self._lock:
            if self._state != "stopping":
                self._last_failure_error = "Mailbox source terminated unexpectedly"
                self._state = "failed"

import sys
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from goldenage.adapters.outlook_mailbox import (
    OutlookMailboxMessage,
    OutlookMailboxUnavailable,
    OutlookMailboxWorker,
    WindowsOutlookMailboxSource,
    _datetime_attr,
    _find_store,
    _folder_key,
    _internet_message_id,
    _normalize_com_message,
    _recent_messages,
    _recipients,
    _sender_email,
    _string_attr,
    _watched_folders,
    normalize_outlook_message,
)
from goldenage.config import Settings
from goldenage.domain.models import MailboxAccountConfig, MailboxSyncCheckpoint, MailParticipant


def test_normalize_outlook_mailbox_message_maps_threading_fields() -> None:
    normalized = normalize_outlook_message(
        OutlookMailboxMessage(
            account_name="Mailbox - bened@example.com",
            folder_key="Inbox",
            message_key="abc123",
            conversation_key="conv-42",
            internet_message_id="<abc123@example.com>",
            subject="RE: Acme contract renewal",
            sender_name="Max Mustermann",
            sender_email="max@acme.example",
            recipients=(MailParticipant(name="Alex Example", email="alex@example.com"),),
            body_text="Please review the renewal changes.",
            sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
            received_at=datetime(2026, 4, 12, 9, 31, tzinfo=UTC),
            direction="inbound",
        )
    )

    assert normalized.source_kind == "outlook_mailbox_message"
    assert normalized.source_account_id == "Mailbox - bened@example.com"
    assert normalized.source_folder_id == "Inbox"
    assert normalized.source_message_id == "abc123"
    assert normalized.conversation_id == "conv-42"
    assert normalized.internet_message_id == "<abc123@example.com>"
    assert normalized.sender is not None
    assert normalized.sender.email == "max@acme.example"
    without_sender = normalize_outlook_message(
        OutlookMailboxMessage(
            account_name="Mailbox",
            folder_key="Inbox",
            message_key="without-sender",
            conversation_key=None,
            internet_message_id=None,
            subject=None,
            sender_name=None,
            sender_email=None,
            recipients=(),
            body_text="Body",
            sent_at=None,
            received_at=None,
            direction="inbound",
        )
    )
    assert without_sender.sender is None


def test_normalize_com_message_reads_outlook_mail_fields() -> None:
    class FakeCollection:
        def __init__(self, values) -> None:
            self._values = values
            self.Count = len(values)

        def Item(self, index: int):
            return self._values[index - 1]

    class FakeAccessor:
        def GetProperty(self, schema: str) -> str:
            assert schema.endswith("0x1035001F")
            return "<abc123@example.com>"

    message = SimpleNamespace(
        EntryID="message-entry",
        ConversationID="conversation-entry",
        Subject="RE: Acme contract renewal",
        SenderName="Max Mustermann",
        SenderEmailAddress="/O=EXAMPLE/OU=EXCHANGE/CN=MAX",
        Sender=SimpleNamespace(
            GetExchangeUser=lambda: SimpleNamespace(PrimarySmtpAddress="max@acme.example")
        ),
        Recipients=FakeCollection(
            (SimpleNamespace(Name="Alex Example", Address="alex@example.com"),)
        ),
        Body="Please review the renewal changes.",
        SentOn=datetime(2026, 4, 12, 9, 30),
        ReceivedTime=datetime(2026, 4, 12, 9, 31, tzinfo=UTC),
        PropertyAccessor=FakeAccessor(),
    )

    normalized = _normalize_com_message(
        message,
        account_name="Mailbox - bened@example.com",
        folder_key="inbox-entry",
        direction="inbound",
    )

    assert normalized is not None
    assert normalized.message_key == "message-entry"
    assert normalized.conversation_key == "conversation-entry"
    assert normalized.internet_message_id == "<abc123@example.com>"
    assert normalized.sender_email == "max@acme.example"
    assert normalized.recipients == (
        MailParticipant(name="Alex Example", email="alex@example.com"),
    )
    assert normalized.sent_at == datetime(2026, 4, 12, 9, 30, tzinfo=UTC)
    assert normalized.received_at == datetime(2026, 4, 12, 9, 31, tzinfo=UTC)


def test_outlook_mailbox_source_rejects_non_windows_and_missing_account(monkeypatch) -> None:
    settings = _settings(account_name="Mailbox")
    source = WindowsOutlookMailboxSource(settings, lambda message: None)
    monkeypatch.setattr(sys, "platform", "darwin")

    with pytest.raises(OutlookMailboxUnavailable, match="only available on Windows"):
        source.watch_forever()

    monkeypatch.setattr(sys, "platform", "win32")
    source = WindowsOutlookMailboxSource(_settings(account_name=None), lambda message: None)
    with pytest.raises(OutlookMailboxUnavailable, match="No Outlook account"):
        source.watch_forever()


def test_outlook_mailbox_source_runs_one_poll_and_stops(monkeypatch) -> None:
    received: list[OutlookMailboxMessage] = []

    class FakeCollection:
        def __init__(self, values) -> None:
            self._values = values
            self.Count = len(values)

        def Item(self, index: int):
            return self._values[index - 1]

    class FakeItems(FakeCollection):
        def Sort(self, field: str, descending: bool) -> None:
            assert field == "[ReceivedTime]"
            assert descending is True

    class FakeAccessor:
        def GetProperty(self, schema: str) -> str:
            del schema
            return "<message@example.com>"

    message = SimpleNamespace(
        Class=43,
        EntryID="message",
        ConversationID="conversation",
        Subject="Subject",
        SenderName="Max",
        SenderEmailAddress="max@example.com",
        Recipients=FakeCollection(()),
        Body="Body",
        SentOn=datetime(2026, 4, 12, 9, 30),
        ReceivedTime=datetime(2026, 4, 12, 9, 31),
        PropertyAccessor=FakeAccessor(),
    )
    folder = SimpleNamespace(
        EntryID="folder",
        Name="Inbox",
        Items=FakeItems((message, SimpleNamespace(Class=99))),
    )
    store = SimpleNamespace(
        DisplayName="Mailbox",
        GetDefaultFolder=lambda folder_id: folder,
    )
    namespace = SimpleNamespace(Stores=FakeCollection((store,)))
    outlook = SimpleNamespace(GetNamespace=lambda name: namespace)
    source = WindowsOutlookMailboxSource(_settings(account_name="Mailbox"), received.append)

    def on_message(message: OutlookMailboxMessage) -> None:
        received.append(message)
        source.stop()

    source = WindowsOutlookMailboxSource(_settings(account_name="Mailbox"), on_message)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "pythoncom",
        SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32com",
        SimpleNamespace(client=SimpleNamespace(Dispatch=lambda name: outlook)),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32com.client",
        SimpleNamespace(Dispatch=lambda name: outlook),
    )
    monkeypatch.setattr("goldenage.adapters.outlook_mailbox.time.sleep", lambda seconds: None)

    source.watch_forever()

    assert len(received) == 1
    assert received[0].message_key == "message"


def test_outlook_mailbox_helpers_cover_missing_and_fallback_values() -> None:
    class FakeCollection:
        def __init__(self, values) -> None:
            self._values = values
            self.Count = len(values)

        def Item(self, index: int):
            return self._values[index - 1]

    class GoodItems(FakeCollection):
        def Sort(self, field: str, descending: bool) -> None:
            assert field == "[ReceivedTime]"
            assert descending is True

    stores = SimpleNamespace(Stores=FakeCollection((SimpleNamespace(DisplayName="Other"),)))
    with pytest.raises(OutlookMailboxUnavailable, match="not found"):
        _find_store(stores, "Mailbox")

    store = SimpleNamespace(GetDefaultFolder=lambda folder_id: f"folder-{folder_id}")
    assert _watched_folders(store) == (("inbound", "folder-6"), ("outbound", "folder-5"))
    message = SimpleNamespace(Class=43)
    assert _recent_messages(SimpleNamespace(Items=GoodItems((message,)))) == (message,)
    assert (
        _normalize_com_message(
            SimpleNamespace(EntryID=""),
            account_name="Mailbox",
            folder_key="Inbox",
            direction="inbound",
        )
        is None
    )
    assert _folder_key(SimpleNamespace(EntryID="", Name="Inbox")) == "Inbox"
    assert _folder_key(SimpleNamespace(EntryID="", Name="")) == "unknown"
    assert _sender_email(SimpleNamespace(SenderEmailAddress='"Max" <max@example.com>')) == (
        "max@example.com"
    )
    assert _sender_email(SimpleNamespace(SenderEmailAddress="foo @")) == "foo @"
    assert _sender_email(SimpleNamespace(SenderEmailAddress="max@example.com")) == (
        "max@example.com"
    )
    assert _sender_email(SimpleNamespace(SenderEmailAddress="exchange", Sender=None)) is None
    assert (
        _sender_email(
            SimpleNamespace(
                SenderEmailAddress="exchange",
                Sender=SimpleNamespace(GetExchangeUser=lambda: None),
            )
        )
        is None
    )
    assert (
        _sender_email(
            SimpleNamespace(
                SenderEmailAddress="exchange",
                Sender=SimpleNamespace(
                    GetExchangeUser=lambda: SimpleNamespace(PrimarySmtpAddress="  ")
                ),
            )
        )
        is None
    )
    assert (
        _sender_email(
            SimpleNamespace(
                SenderEmailAddress="exchange",
                Sender=SimpleNamespace(
                    GetExchangeUser=lambda: SimpleNamespace(PrimarySmtpAddress="max@acme.example")
                ),
            )
        )
        == "max@acme.example"
    )
    assert _internet_message_id(SimpleNamespace(PropertyAccessor=None)) is None
    assert _internet_message_id(SimpleNamespace()) is None
    assert (
        _internet_message_id(
            SimpleNamespace(
                PropertyAccessor=SimpleNamespace(
                    GetProperty=lambda schema: (_ for _ in ()).throw(RuntimeError("missing"))
                )
            )
        )
        is None
    )
    assert _recipients(SimpleNamespace(Recipients=None)) == ()
    assert _string_attr(SimpleNamespace(value=None), "value") is None
    assert _string_attr(SimpleNamespace(value="  "), "value") is None
    assert _datetime_attr(SimpleNamespace(value="not datetime"), "value") is None


def test_outlook_mailbox_worker_starts_once_and_delegates_stop() -> None:
    calls: list[str] = []
    source = SimpleNamespace(
        watch_forever=lambda: calls.append("watch"),
        stop=lambda: calls.append("stop"),
    )
    worker = OutlookMailboxWorker(source)  # ty:ignore[invalid-argument-type]

    worker.start()
    worker.start()
    worker.stop()

    assert calls == ["watch", "stop"]

    worker_without_stop = OutlookMailboxWorker(
        SimpleNamespace(watch_forever=lambda: None)  # ty:ignore[invalid-argument-type]
    )
    worker_without_stop.stop()


def test_outlook_source_pages_through_backlog_and_resumes_from_checkpoint() -> None:
    messages = _fake_messages(100)
    folder = _FakeFolder(messages)
    repository = _CheckpointRepository()
    account = _mailbox_account_config()
    received: list[str] = []
    source = WindowsOutlookMailboxSource(
        _settings(account_name="Mailbox", scan_limit=25),
        lambda message: received.append(message.message_key),
        checkpoint_repository=repository,
        account_config=account,
    )

    source._process_folder(
        folder=folder,
        account_name="Mailbox",
        direction="inbound",
        checkpoint=None,
    )

    assert received == [f"message-{index:03d}" for index in range(100)]
    checkpoint = repository.checkpoints["folder"]
    assert checkpoint.last_message_key == "message-099"

    received.clear()
    resumed_source = WindowsOutlookMailboxSource(
        _settings(account_name="Mailbox", scan_limit=25),
        lambda message: received.append(message.message_key),
        checkpoint_repository=repository,
        account_config=account,
    )
    resumed_source._process_folder(
        folder=folder,
        account_name="Mailbox",
        direction="inbound",
        checkpoint=checkpoint,
    )
    assert received == []


def test_outlook_source_retries_failures_without_acknowledging_past_quarantine() -> None:
    messages = _fake_messages(5)
    repository = _CheckpointRepository()
    attempts: list[str] = []

    def on_message(message: OutlookMailboxMessage) -> None:
        attempts.append(message.message_key)
        if message.message_key == "message-002":
            raise RuntimeError("mail body must not appear in health")

    source = WindowsOutlookMailboxSource(
        _settings(account_name="Mailbox", scan_limit=25, max_attempts=2),
        on_message,
        checkpoint_repository=repository,
        account_config=_mailbox_account_config(),
    )
    source._process_folder(
        folder=_FakeFolder(messages),
        account_name="Mailbox",
        direction="inbound",
        checkpoint=None,
    )

    assert attempts == [
        "message-000",
        "message-001",
        "message-002",
        "message-002",
        "message-003",
        "message-004",
    ]
    assert repository.checkpoints["folder"].last_message_key == "message-001"
    health = source.health
    assert health.last_failure_message_key == "message-002"
    assert health.quarantined_message_keys == ("message-002",)
    assert "mail body" not in (health.last_failure_error or "")
    source._process_folder(
        folder=_FakeFolder(messages),
        account_name="Mailbox",
        direction="inbound",
        checkpoint=repository.checkpoints["folder"],
    )
    assert repository.checkpoints["folder"].last_message_key == "message-001"


def test_outlook_worker_restart_joins_old_source_and_exposes_dead_state() -> None:
    source = _BlockingSource()
    worker = OutlookMailboxWorker(source, shutdown_timeout_seconds=1)

    worker.start()
    assert worker.health.state == "running"
    worker.stop()
    assert worker.health.state == "stopped"
    assert worker.health.thread_alive is False

    worker.start()
    assert worker.health.state == "running"
    worker.restart()
    assert worker.health.state == "running"
    worker.stop()
    assert source.starts == 3


def _settings(
    account_name: str | None,
    *,
    scan_limit: int = 25,
    max_attempts: int = 3,
) -> Settings:
    return Settings(
        database_url=None,
        local_first_mode=None,
        sqlite_path=None,
        artifact_dir=None,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        profile_dir=None,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        local_timezone="UTC",
        mail_client_mode=None,
        mail_fixture_path=None,
        outlook_scan_per_folder_limit=scan_limit,
        outlook_sync_enabled=True,
        outlook_account_name=account_name,
        outlook_poll_seconds=1,
        apple_mail_client_mode=None,
        apple_mail_fixture_path=None,
        auth_secret="secret",
        auth_cookie_secure=False,
        outlook_delivery_max_attempts=max_attempts,
        outlook_retry_backoff_seconds=0,
    )


class _FakeItems:
    def __init__(self, messages: tuple[SimpleNamespace, ...]) -> None:
        self._messages = messages
        self.Count = len(messages)

    def Sort(self, field: str, descending: bool) -> None:
        assert field == "[ReceivedTime]"
        assert descending is True

    def Item(self, index: int) -> SimpleNamespace:
        return self._messages[index - 1]


class _FakeFolder:
    EntryID = "folder"

    def __init__(self, messages: tuple[SimpleNamespace, ...]) -> None:
        self.Items = _FakeItems(messages)


class _CheckpointRepository:
    def __init__(self) -> None:
        self.checkpoints: dict[str, MailboxSyncCheckpoint] = {}

    def save_mailbox_account_config(self, config: MailboxAccountConfig) -> None:
        del config

    def list_mailbox_sync_checkpoints(
        self,
        account_config_id: UUID,
    ) -> tuple[MailboxSyncCheckpoint, ...]:
        del account_config_id
        return tuple(self.checkpoints.values())

    def save_mailbox_sync_checkpoint(self, checkpoint: MailboxSyncCheckpoint) -> None:
        self.checkpoints[checkpoint.folder_key] = checkpoint


class _BlockingSource:
    def __init__(self) -> None:
        self.started = False
        self.starts = 0
        self._stop_event = threading.Event()

    def reset(self) -> None:
        self._stop_event.clear()

    def watch_forever(self) -> None:
        self.started = True
        self.starts += 1
        while not self._stop_event.is_set():
            self._stop_event.wait(0.001)

    def stop(self) -> None:
        self._stop_event.set()


def _fake_messages(count: int) -> tuple[SimpleNamespace, ...]:
    return tuple(
        SimpleNamespace(
            Class=43,
            EntryID=f"message-{index:03d}",
            Body=f"body-{index}",
            SentOn=datetime(2026, 4, 12, 9, index % 60, tzinfo=UTC),
            ReceivedTime=datetime(2026, 4, 12, 9, index % 60, tzinfo=UTC),
        )
        for index in reversed(range(count))
    )


def _mailbox_account_config() -> MailboxAccountConfig:
    now = datetime(2026, 4, 12, 9, 30, tzinfo=UTC)
    return MailboxAccountConfig(
        id=uuid4(),
        user_id=None,
        source_kind="outlook_mailbox_message",
        account_key="Mailbox",
        outlook_store_name="Mailbox",
        inbox_folder_key="folder",
        sent_folder_key=None,
        polling_interval_seconds=1,
        active=True,
        created_at=now,
        updated_at=now,
    )

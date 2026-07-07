import sys
from datetime import UTC, datetime
from types import SimpleNamespace

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
from goldenage.domain.models import MailParticipant


def test_normalize_outlook_mailbox_message_maps_threading_fields() -> None:
    normalized = normalize_outlook_message(
        OutlookMailboxMessage(
            account_name="Mailbox - user.fixture@example.test",
            folder_key="Inbox",
            message_key="abc123",
            conversation_key="conv-42",
            internet_message_id="<abc123@example.com>",
            subject="RE: Vendor contract renewal",
            sender_name="Sender Fixture",
            sender_email="sender.fixture@vendor.example.test",
            recipients=(MailParticipant(name="Fixture User", email="user.fixture@example.test"),),
            body_text="Please review the renewal changes.",
            sent_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
            received_at=datetime(2026, 4, 12, 9, 31, tzinfo=UTC),
            direction="inbound",
        )
    )

    assert normalized.source_kind == "outlook_mailbox_message"
    assert normalized.source_account_id == "Mailbox - user.fixture@example.test"
    assert normalized.source_folder_id == "Inbox"
    assert normalized.source_message_id == "abc123"
    assert normalized.conversation_id == "conv-42"
    assert normalized.internet_message_id == "<abc123@example.com>"
    assert normalized.sender is not None
    assert normalized.sender.email == "sender.fixture@vendor.example.test"


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
        Subject="RE: Vendor contract renewal",
        SenderName="Sender Fixture",
        SenderEmailAddress="/O=EXAMPLE/OU=EXCHANGE/CN=MAX",
        Sender=SimpleNamespace(
            GetExchangeUser=lambda: SimpleNamespace(
                PrimarySmtpAddress="sender.fixture@vendor.example.test"
            )
        ),
        Recipients=FakeCollection(
            (SimpleNamespace(Name="Fixture User", Address="user.fixture@example.test"),)
        ),
        Body="Please review the renewal changes.",
        SentOn=datetime(2026, 4, 12, 9, 30),
        ReceivedTime=datetime(2026, 4, 12, 9, 31, tzinfo=UTC),
        PropertyAccessor=FakeAccessor(),
    )

    normalized = _normalize_com_message(
        message,
        account_name="Mailbox - user.fixture@example.test",
        folder_key="inbox-entry",
        direction="inbound",
    )

    assert normalized is not None
    assert normalized.message_key == "message-entry"
    assert normalized.conversation_key == "conversation-entry"
    assert normalized.internet_message_id == "<abc123@example.com>"
    assert normalized.sender_email == "sender.fixture@vendor.example.test"
    assert normalized.recipients == (
        MailParticipant(name="Fixture User", email="user.fixture@example.test"),
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
        SenderName="Sender",
        SenderEmailAddress="sender.fixture@example.test",
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
    assert _sender_email(
        SimpleNamespace(SenderEmailAddress='"Sender" <sender.fixture@example.test>')
    ) == ("sender.fixture@example.test")
    assert _sender_email(SimpleNamespace(SenderEmailAddress="foo @")) == "foo @"
    assert _sender_email(SimpleNamespace(SenderEmailAddress="sender.fixture@example.test")) == (
        "sender.fixture@example.test"
    )
    assert _sender_email(SimpleNamespace(SenderEmailAddress="exchange", Sender=None)) is None
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


def _settings(account_name: str | None) -> Settings:
    return Settings(
        database_url=None,
        local_first_mode=None,
        sqlite_path=None,
        artifact_dir=None,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        profile_dir=None,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        local_timezone="UTC",
        mail_client_mode=None,
        mail_fixture_path=None,
        outlook_scan_per_folder_limit=25,
        outlook_sync_enabled=True,
        outlook_account_name=account_name,
        outlook_poll_seconds=1,
        apple_mail_client_mode=None,
        apple_mail_fixture_path=None,
        gisela_http_url=None,
        elizabethan_http_url=None,
        agent_http_timeout_seconds=10.0,
        auth_secret="secret",
        auth_cookie_secure=False,
    )

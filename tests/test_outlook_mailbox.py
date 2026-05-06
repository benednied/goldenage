from datetime import UTC, datetime
from types import SimpleNamespace

from goldenage.adapters.outlook_mailbox import (
    OutlookMailboxMessage,
    _normalize_com_message,
    normalize_outlook_message,
)
from goldenage.domain.models import MailParticipant


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

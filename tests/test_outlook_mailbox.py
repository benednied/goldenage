from datetime import UTC, datetime

from goldenage.adapters.outlook_mailbox import OutlookMailboxMessage, normalize_outlook_message
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

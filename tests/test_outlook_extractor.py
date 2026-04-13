from datetime import UTC, datetime
import sys
from types import SimpleNamespace

from goldenage.adapters.demo import OutlookMsgExtractor


def test_outlook_msg_extractor_maps_subject_sender_and_recipients(monkeypatch) -> None:
    class FakeRecipient:
        def __init__(self, name: str, email_address: str) -> None:
            self.name = name
            self.email_address = email_address

    class FakeMessage:
        subject = "RE: Acme contract renewal"
        sender = '"Max Mustermann" <max@acme.example>'
        recipients = (FakeRecipient("Alex Example", "alex@example.com"),)
        body = "Please review the discount request."
        html_body = None
        sent_date = datetime(2026, 4, 12, 9, 30, tzinfo=UTC)

        @classmethod
        def load(cls, content: bytes):
            assert content == b"msg-bytes"
            return cls()

    monkeypatch.setitem(sys.modules, "oxmsg", SimpleNamespace(Message=FakeMessage))

    extracted = OutlookMsgExtractor().extract(
        file_name="renewal.msg",
        media_type="application/vnd.ms-outlook",
        content=b"msg-bytes",
    )

    assert extracted.subject == "RE: Acme contract renewal"
    assert extracted.sender is not None
    assert extracted.sender.name == "Max Mustermann"
    assert extracted.sender.email == "max@acme.example"
    assert extracted.recipients[0].email == "alex@example.com"
    assert extracted.content_text == "Please review the discount request."

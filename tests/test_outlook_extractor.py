import sys
from datetime import UTC, datetime
from types import SimpleNamespace

from goldenage.adapters.demo import OutlookMsgExtractor


def test_outlook_msg_extractor_maps_subject_sender_and_recipients(monkeypatch) -> None:
    class FakeRecipient:
        def __init__(self, name: str, email_address: str) -> None:
            self.name = name
            self.email_address = email_address

    class FakeMessage:
        subject = "RE: Vendor contract renewal"
        sender = '"Sender Fixture" <sender.fixture@vendor.example.test>'
        recipients = (FakeRecipient("Fixture User", "user.fixture@example.test"),)
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

    assert extracted.message_format == "outlook_msg"
    assert extracted.subject == "RE: Vendor contract renewal"
    assert extracted.sender is not None
    assert extracted.sender.name == "Sender Fixture"
    assert extracted.sender.email == "sender.fixture@vendor.example.test"
    assert extracted.recipients[0].email == "user.fixture@example.test"
    assert extracted.content_text == "Please review the discount request."

import json
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from goldenage.adapters import mail
from goldenage.domain.models import MailSelector


class StubExtractor:
    def __init__(self, label: str) -> None:
        self.label = label

    def extract(self, file_name: str, media_type: str, content: bytes):
        return (self.label, file_name, media_type, content)


def test_multiplexed_extractor_routes_outlook_and_rfc822_messages() -> None:
    extractor = mail.MultiplexedArtifactExtractor(
        outlook_extractor=StubExtractor("outlook"),
        rfc822_extractor=StubExtractor("rfc822"),
    )

    assert extractor.extract("mail.msg", "application/octet-stream", b"msg")[0] == "outlook"  # ty:ignore[not-subscriptable]
    assert extractor.extract("mail.eml", "message/rfc822", b"eml")[0] == "rfc822"  # ty:ignore[not-subscriptable]
    assert extractor.extract("mail.bin", "application/vnd.ms-outlook", b"msg")[0] == "outlook"  # ty:ignore[not-subscriptable]


def test_multiplexed_extractor_defers_pdf_and_images_to_ocr() -> None:
    extractor = mail.MultiplexedArtifactExtractor(
        outlook_extractor=StubExtractor("outlook"),
        rfc822_extractor=StubExtractor("rfc822"),
    )

    extracted = extractor.extract("scan.pdf", "application/pdf", b"%PDF")

    assert extracted.message_format == "binary_document"
    assert extracted.parse_status == "ocr_required"
    assert extracted.subject == "scan.pdf"
    assert extracted.content_text == ""


def test_rfc822_extractor_handles_headers_body_and_invalid_dates() -> None:
    extracted = mail.Rfc822EmailExtractor().extract(
        "fallback.eml",
        "message/rfc822",
        (
            b"From: Sender Fixture <sender.fixture@vendor.example.test>\n"
            b"To: Fixture User <user.fixture@example.test>, team@example.com\n"
            b"Date: not-a-date\n"
            b"Message-ID: <msg-1@example.com>\n"
            b"\n"
            b"Body   text\n"
        ),
    )

    assert extracted.subject == "fallback.eml"
    assert extracted.sender is not None
    assert extracted.sender.email == "sender.fixture@vendor.example.test"
    assert [recipient.email for recipient in extracted.recipients] == [
        "user.fixture@example.test",
        "team@example.com",
    ]
    assert extracted.sent_at is None
    assert extracted.content_text == "Body text"


def test_fixture_client_filters_sorts_fetches_and_reports_invalid_fixtures(tmp_path) -> None:
    fixture_path = tmp_path / "mail.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "old",
                    "account_name": "iCloud",
                    "mailbox_name": "Inbox",
                    "subject": "Other",
                    "sender_name": "Sender",
                    "sender_email": "sender.fixture@example.test",
                    "sent_at": "2026-04-10T09:00:00+00:00",
                    "preview_text": "old",
                    "unread": False,
                    "raw_source": "old raw",
                },
                {
                    "candidate_id": "new",
                    "account_name": "iCloud",
                    "mailbox_name": "Inbox",
                    "subject": "Nürnberg renewal",
                    "sender_name": "Sender",
                    "sender_email": "sender.fixture@example.test",
                    "sent_at": "2026-04-12T09:00:00+00:00",
                    "preview_text": "new",
                    "unread": True,
                    "raw_source": "new raw",
                    "rfc_message_id": "<new@example.com>",
                },
            ]
        ),
        encoding="utf-8",
    )

    client = mail.FixtureMailImportClient(fixture_path)
    candidates = client.search_candidates(
        MailSelector(
            account_name="icloud",
            mailbox_name="inbox",
            unread_only=True,
            sender_filter="sender.fixture@example.test",
            subject_filter="Nuernberg",
            sent_after=datetime(2026, 4, 11, tzinfo=UTC),
            result_limit=5,
        )
    )
    payload = client.fetch_message("new")

    assert [candidate.candidate_id for candidate in candidates] == ["new"]
    assert payload.file_name == "nurnberg-renewal.eml"
    assert payload.content == b"new raw"
    assert payload.rfc_message_id == "<new@example.com>"

    with pytest.raises(mail.MailImportClientError, match="not found"):
        client.fetch_message("missing")

    fixture_path.write_text("{}", encoding="utf-8")
    with pytest.raises(mail.MailImportClientError, match="JSON array"):
        client.search_candidates(MailSelector())

    fixture_path.write_text(json.dumps([{"candidate_id": "bad"}]), encoding="utf-8")
    with pytest.raises(mail.MailImportClientError, match="candidate_id and raw_source"):
        client.search_candidates(MailSelector())

    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "wrong-account",
                    "account_name": "Other",
                    "mailbox_name": "Inbox",
                    "subject": "Renewal",
                    "sender_name": "Sender",
                    "sender_email": "sender.fixture@example.test",
                    "sent_at": "2026-04-12T09:00:00+00:00",
                    "preview_text": "",
                    "unread": True,
                    "raw_source": "raw",
                },
                {
                    "candidate_id": "wrong-mailbox",
                    "account_name": "iCloud",
                    "mailbox_name": "Archive",
                    "subject": "Renewal",
                    "sender_name": "Sender",
                    "sender_email": "sender.fixture@example.test",
                    "sent_at": "2026-04-12T09:00:00+00:00",
                    "preview_text": "",
                    "unread": True,
                    "raw_source": "raw",
                },
                {
                    "candidate_id": "wrong-sender",
                    "account_name": "iCloud",
                    "mailbox_name": "Inbox",
                    "subject": "Renewal",
                    "sender_name": "Other",
                    "sender_email": "other@example.com",
                    "sent_at": "2026-04-12T09:00:00+00:00",
                    "preview_text": "",
                    "unread": True,
                    "raw_source": "raw",
                },
                {
                    "candidate_id": "wrong-subject",
                    "account_name": "iCloud",
                    "mailbox_name": "Inbox",
                    "subject": "Other",
                    "sender_name": "Sender",
                    "sender_email": "sender.fixture@example.test",
                    "sent_at": "2026-04-12T09:00:00+00:00",
                    "preview_text": "",
                    "unread": True,
                    "raw_source": "raw",
                },
                {
                    "candidate_id": "too-old",
                    "account_name": "iCloud",
                    "mailbox_name": "Inbox",
                    "subject": "Renewal",
                    "sender_name": "Sender",
                    "sender_email": "sender.fixture@example.test",
                    "sent_at": None,
                    "preview_text": "",
                    "unread": True,
                    "raw_source": "raw",
                },
            ]
        ),
        encoding="utf-8",
    )
    assert (
        client.search_candidates(
            MailSelector(
                account_name="iCloud",
                mailbox_name="Inbox",
                sender_filter="Sender",
                subject_filter="Renewal",
                sent_after=datetime(2026, 4, 11, tzinfo=UTC),
            )
        )
        == ()
    )

    fixture_path.write_text(
        json.dumps([{"candidate_id": "blank", "raw_source": " "}]), encoding="utf-8"
    )
    with pytest.raises(mail.MailImportClientError, match="no raw source"):
        client.fetch_message("blank")


def test_osascript_client_filters_results_fetches_payload_and_reports_errors(monkeypatch) -> None:
    calls: list[dict[str, object | None]] = []

    def fake_run(args, *, input, text, capture_output, check, env):
        del args, input, text, capture_output, check
        payload = json.loads(env["GOLDENAGE_APPLE_MAIL_QUERY"])
        calls.append(payload)
        if "candidateId" in payload:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "candidateId": "apple-1",
                        "accountName": "iCloud",
                        "mailboxName": "Inbox",
                        "subject": "Renewal",
                        "rawSource": "raw message",
                        "unread": True,
                        "rfcMessageId": "<apple-1@example.com>",
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "candidateId": "skip-read",
                        "subject": "Renewal",
                        "sender": "Sender <sender.fixture@example.test>",
                        "sentAt": "2026-04-12T08:00:00+00:00",
                        "previewText": "read",
                        "unread": False,
                    },
                    {
                        "candidateId": "apple-1",
                        "accountName": "iCloud",
                        "mailboxName": "Inbox",
                        "subject": "Renewal",
                        "sender": "Sender <sender.fixture@example.test>",
                        "sentAt": "2026-04-12T09:00:00+00:00",
                        "previewText": "unread",
                        "unread": True,
                        "rfcMessageId": "<apple-1@example.com>",
                    },
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(mail.subprocess, "run", fake_run)
    client = mail.OsaScriptAppleMailImportClient(executable="osascript")

    candidates = client.search_candidates(
        MailSelector(
            unread_only=True,
            sender_filter="sender",
            subject_filter="renewal",
            sent_after=datetime(2026, 4, 12, 8, 30, tzinfo=UTC),
        )
    )
    payload = client.fetch_message("apple-1")

    assert [candidate.candidate_id for candidate in candidates] == ["apple-1"]
    assert payload.file_name == "renewal.eml"
    assert payload.content == b"raw message"
    assert calls[0]["unreadOnly"] is True

    monkeypatch.setattr(
        mail.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with pytest.raises(mail.MailImportClientError, match="boom"):
        client.search_candidates(MailSelector())

    monkeypatch.setattr(
        mail.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"candidateId": "apple-2", "rawSource": ""}),
            stderr="",
        ),
    )
    with pytest.raises(mail.MailImportClientError, match="raw message source"):
        client.fetch_message("apple-2")


def test_mail_client_builders_cover_fixture_disabled_platform_and_invalid_modes(
    monkeypatch,
    tmp_path,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("[]", encoding="utf-8")

    assert isinstance(
        mail.build_apple_mail_import_client(fixture_path=fixture, client_mode=None),
        mail.FixtureMailImportClient,
    )
    assert mail.build_desktop_mail_import_client(fixture_path=None, client_mode=None) is None
    assert mail.build_apple_mail_import_client(fixture_path=None, client_mode="disabled") is None
    monkeypatch.setattr(mail.shutil, "which", lambda executable: None)
    assert mail.build_apple_mail_import_client(fixture_path=None, client_mode="auto") is None
    assert mail.build_desktop_mail_import_client(fixture_path=None, client_mode="disabled") is None
    assert (
        mail.build_desktop_mail_import_client(fixture_path=None, client_mode="apple-mail") is None
    )
    with pytest.raises(mail.MailImportClientError, match="Unsupported"):
        mail.build_desktop_mail_import_client(fixture_path=None, client_mode="bogus")

    monkeypatch.setattr(mail.shutil, "which", lambda executable: "/usr/bin/osascript")
    assert isinstance(
        mail.build_apple_mail_import_client(fixture_path=None, client_mode="auto"),
        mail.OsaScriptAppleMailImportClient,
    )
    assert isinstance(
        mail.build_desktop_mail_import_client(fixture_path=None, client_mode="apple_mail"),
        mail.OsaScriptAppleMailImportClient,
    )
    monkeypatch.setattr(mail.platform, "system", lambda: "Darwin")
    assert isinstance(
        mail.build_desktop_mail_import_client(fixture_path=None, client_mode="auto"),
        mail.OsaScriptAppleMailImportClient,
    )
    assert isinstance(
        mail.build_desktop_mail_import_client(fixture_path=None, client_mode="outlook-windows"),
        mail.OutlookWindowsImportClient,
    )
    monkeypatch.setattr(mail.platform, "system", lambda: "Linux")
    assert mail.build_desktop_mail_import_client(fixture_path=None, client_mode="auto") is None
    monkeypatch.setattr(mail.platform, "system", lambda: "Windows")
    assert isinstance(
        mail.build_desktop_mail_import_client(fixture_path=None, client_mode="auto"),
        mail.OutlookWindowsImportClient,
    )


def test_outlook_helpers_encode_decode_filter_and_handle_com_edge_cases() -> None:
    encoded = mail._encode_outlook_candidate_id(
        store_id="store",
        entry_id="entry",
        account_name="Account",
        mailbox_name="Inbox",
    )
    assert mail._decode_outlook_candidate_id(encoded) == {
        "store_id": "store",
        "entry_id": "entry",
        "account_name": "Account",
        "mailbox_name": "Inbox",
    }
    with pytest.raises(mail.MailImportClientError, match="malformed"):
        mail._decode_outlook_candidate_id("%%%")
    with pytest.raises(mail.MailImportClientError, match="incomplete"):
        mail._decode_outlook_candidate_id(mail.urlsafe_b64encode(b"{}").decode("ascii"))

    candidate = mail.MailCandidate(
        candidate_id="1",
        source_system=mail.DESKTOP_MAIL_SOURCE,
        account_name="Account",
        mailbox_name="Inbox",
        subject="Nürnberg renewal",
        sender_name="Sender",
        sender_email="sender.fixture@example.test",
        sent_at=datetime(2026, 4, 12, tzinfo=UTC),
        preview_text="preview",
        unread=True,
    )
    assert mail._outlook_candidate_matches(
        candidate,
        MailSelector(
            unread_only=True,
            sender_filter="sender",
            subject_filter="Nuernberg",
            sent_after=datetime(2026, 4, 11, tzinfo=UTC),
        ),
    )
    assert not mail._outlook_candidate_matches(candidate, MailSelector(sender_filter="other"))
    assert not mail._outlook_candidate_matches(candidate, MailSelector(subject_filter="other"))
    assert not mail._outlook_candidate_matches(
        candidate,
        MailSelector(sent_after=datetime(2026, 4, 13, tzinfo=UTC)),
    )

    assert mail._com_collection(None) == ()
    assert mail._com_collection(SimpleNamespace(Count=2, Item=lambda index: f"item-{index}")) == (
        "item-1",
        "item-2",
    )
    assert mail._com_collection(SimpleNamespace(Count="bad")) == ()
    assert mail._com_get(property_raising_object(), "broken") is None
    assert (
        mail._outlook_property(SimpleNamespace(PropertyAccessor=property_raising_object()), "x")
        is None
    )
    assert mail._parse_optional_outlook_datetime(None) is None
    assert mail._parse_optional_outlook_datetime("bad") is None
    assert mail._parse_optional_datetime(datetime(2026, 4, 12, tzinfo=UTC)) == datetime(
        2026, 4, 12, tzinfo=UTC
    )
    with pytest.raises(mail.MailImportClientError, match="Unsupported datetime"):
        mail._parse_optional_datetime(42)
    assert mail._candidate_file_name(None) == "mail-message.eml"
    assert mail._outlook_file_name(None) == "mail-message.msg"
    assert mail._comparison_forms(None) == frozenset()

    class FailingSortItems:
        Count = 3

        def Sort(self, field: str, descending: bool) -> None:
            del field, descending
            raise RuntimeError("sort")

        def Item(self, index: int):
            return (SimpleNamespace(Class=99), SimpleNamespace(Class=43), SimpleNamespace())[
                index - 1
            ]

    assert mail._iter_outlook_mail_items(SimpleNamespace(DefaultItemType=2), limit=1) == ()
    assert (
        mail._iter_outlook_mail_items(SimpleNamespace(DefaultItemType=0, Items=None), limit=1) == ()
    )
    assert (
        len(
            mail._iter_outlook_mail_items(
                SimpleNamespace(DefaultItemType=0, Items=FailingSortItems()), limit=1
            )
        )
        == 1
    )
    client = mail.OutlookWindowsImportClient(
        dispatch_factory=lambda name: SimpleNamespace(Session=SimpleNamespace()),
    )
    with pytest.raises(mail.MailImportClientError, match="Configure a mail address"):
        client.search_candidates(MailSelector())

    skipped_folder = SimpleNamespace(Name="Archive")
    selected_folder = SimpleNamespace(Name="Inbox", DefaultItemType=0, Items=())
    store = SimpleNamespace(
        StoreID="store",
        GetRootFolder=lambda: SimpleNamespace(Folders=(skipped_folder, selected_folder)),
    )
    namespace = SimpleNamespace(
        Stores=(store,),
        Accounts=(SimpleNamespace(SmtpAddress="Mailbox", DeliveryStore=store),),
    )
    client = mail.OutlookWindowsImportClient(
        dispatch_factory=lambda name: SimpleNamespace(Session=namespace),
    )
    assert (
        client.search_candidates(MailSelector(account_name="Mailbox", mailbox_name="Inbox")) == ()
    )

    store = SimpleNamespace(DisplayName="Mailbox")
    namespace = SimpleNamespace(
        Stores=(store,),
        Accounts=(SimpleNamespace(SmtpAddress="other@example.com", DisplayName="Other"),),
    )
    assert mail._find_outlook_store(namespace, "Mailbox") == store
    with pytest.raises(mail.MailImportClientError, match="was not found"):
        mail._find_outlook_store(namespace, "missing@example.com")

    fake_outlook = object()
    with mail._com_session(lambda name: fake_outlook) as outlook:
        assert outlook is fake_outlook
    with pytest.raises(mail.MailImportClientError, match="requires pywin32"):
        with mail._com_session(None):
            pass

    calls: list[str] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(
        sys.modules,
        "pythoncom",
        SimpleNamespace(
            CoInitialize=lambda: calls.append("init"), CoUninitialize=lambda: calls.append("uninit")
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32com.client",
        SimpleNamespace(Dispatch=lambda name: fake_outlook),
    )
    with mail._com_session(None) as outlook:
        assert outlook is fake_outlook
    monkeypatch.undo()
    assert calls == ["init", "uninit"]

    class MultipartWithoutPreferredBody:
        def get_body(self, preferencelist):
            del preferencelist
            return None

        def is_multipart(self):
            return True

        def walk(self):
            return (
                SimpleNamespace(
                    get_content=lambda: "plain",
                    get_content_type=lambda: "text/plain",
                    get=lambda name: None,
                ),
                SimpleNamespace(
                    get_content=lambda: "ignored",
                    get_content_type=lambda: "text/plain",
                    get=lambda name: "attachment",
                ),
            )

    class SinglepartWithoutPreferredBody:
        def get_body(self, preferencelist):
            del preferencelist
            return None

        def is_multipart(self):
            return False

        def get_content(self):
            return "plain body"

    assert mail._extract_body_text(MultipartWithoutPreferredBody()) == "plain"
    assert mail._extract_body_text(SinglepartWithoutPreferredBody()) == "plain body"
    no_date = EmailMessage()
    assert mail._parse_sent_at(no_date.get("Date")) is None


def property_raising_object():
    class Raising:
        @property
        def broken(self):
            raise RuntimeError("broken")

        def GetProperty(self, schema: str):
            del schema
            raise RuntimeError("broken")

    return Raising()

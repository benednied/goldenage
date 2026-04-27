from datetime import UTC, datetime

from goldenage.adapters.mail import OutlookWindowsImportClient
from goldenage.domain.models import MailSelector


class FakePropertyAccessor:
    def __init__(self, message_id: str | None) -> None:
        self._message_id = message_id

    def GetProperty(self, schema: str) -> str | None:
        del schema
        return self._message_id


class FakeMailItem:
    Class = 43

    def __init__(
        self,
        *,
        entry_id: str,
        subject: str,
        sender_name: str,
        sender_email: str,
        sent_on: datetime,
        body: str,
        unread: bool,
        message_id: str | None,
    ) -> None:
        self.EntryID = entry_id
        self.Subject = subject
        self.SenderName = sender_name
        self.SenderEmailAddress = sender_email
        self.SentOn = sent_on
        self.Body = body
        self.UnRead = unread
        self.PropertyAccessor = FakePropertyAccessor(message_id)

    def SaveAs(self, path: str, save_as_type: int) -> None:
        del save_as_type
        with open(path, "wb") as handle:
            handle.write(f"MSG:{self.Subject}".encode("utf-8"))


class FakeItems:
    def __init__(self, items: tuple[FakeMailItem, ...]) -> None:
        self._items = items

    def Sort(self, field: str, descending: bool) -> None:
        del field
        self._items = tuple(sorted(self._items, key=lambda item: item.SentOn, reverse=descending))

    def __iter__(self):
        return iter(self._items)


class FakeFolder:
    DefaultItemType = 0

    def __init__(
        self,
        name: str,
        items: tuple[FakeMailItem, ...] = (),
        folders: tuple["FakeFolder", ...] = (),
    ) -> None:
        self.Name = name
        self.Items = FakeItems(items)
        self.Folders = folders


class FakeStore:
    StoreID = "store-1"
    DisplayName = "Work Mailbox"

    def __init__(self, root: FakeFolder) -> None:
        self._root = root

    def GetRootFolder(self) -> FakeFolder:
        return self._root


class FakeAccount:
    DisplayName = "Work Mailbox"
    SmtpAddress = "configured@example.com"

    def __init__(self, store: FakeStore) -> None:
        self.DeliveryStore = store


class FakeSession:
    def __init__(self, store: FakeStore, items_by_id: dict[str, FakeMailItem]) -> None:
        self.Stores = (store,)
        self.Accounts = (FakeAccount(store),)
        self._items_by_id = items_by_id

    def GetItemFromID(self, entry_id: str, store_id: str) -> FakeMailItem:
        assert store_id == "store-1"
        return self._items_by_id[entry_id]


class FakeOutlookApp:
    def __init__(self, session: FakeSession) -> None:
        self.Session = session


def test_outlook_windows_searches_configured_account_all_folders() -> None:
    matching = FakeMailItem(
        entry_id="message-1",
        subject="Acme contract renewal",
        sender_name="Max Mustermann",
        sender_email="max@acme.example",
        sent_on=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
        body="Please review the latest renewal draft.",
        unread=True,
        message_id="<message-1@example.com>",
    )
    read_message = FakeMailItem(
        entry_id="message-2",
        subject="Acme contract renewal",
        sender_name="Max Mustermann",
        sender_email="max@acme.example",
        sent_on=datetime(2026, 4, 12, 8, 30, tzinfo=UTC),
        body="Already read.",
        unread=False,
        message_id="<message-2@example.com>",
    )
    root = FakeFolder("Root", folders=(FakeFolder("Project Folder", (matching, read_message)),))
    store = FakeStore(root)
    session = FakeSession(store, {"message-1": matching, "message-2": read_message})
    client = OutlookWindowsImportClient(
        dispatch_factory=lambda _: FakeOutlookApp(session),
    )

    candidates = client.search_candidates(
        MailSelector(
            account_name="configured@example.com",
            unread_only=True,
            sender_filter="max@acme.example",
            subject_filter="renewal",
        )
    )

    assert len(candidates) == 1
    assert candidates[0].account_name == "Work Mailbox"
    assert candidates[0].mailbox_name == "Project Folder"
    assert candidates[0].rfc_message_id == "<message-1@example.com>"


def test_outlook_windows_fetches_candidate_as_msg_payload(tmp_path) -> None:
    message = FakeMailItem(
        entry_id="message-1",
        subject="Acme contract renewal",
        sender_name="Max Mustermann",
        sender_email="max@acme.example",
        sent_on=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
        body="Please review the latest renewal draft.",
        unread=True,
        message_id="<message-1@example.com>",
    )
    root = FakeFolder("Root", folders=(FakeFolder("Project Folder", (message,)),))
    store = FakeStore(root)
    session = FakeSession(store, {"message-1": message})
    client = OutlookWindowsImportClient(
        dispatch_factory=lambda _: FakeOutlookApp(session),
        temp_dir=tmp_path,
    )
    candidate = client.search_candidates(MailSelector(account_name="configured@example.com"))[0]

    payload = client.fetch_message(candidate.candidate_id)

    assert payload.source_system == "desktop_mail_client"
    assert payload.file_name == "acme-contract-renewal.msg"
    assert payload.media_type == "application/vnd.ms-outlook"
    assert payload.content == b"MSG:Acme contract renewal"
    assert payload.rfc_message_id == "<message-1@example.com>"

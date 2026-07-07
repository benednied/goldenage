"""Mail-source adapters and source-neutral message extractors."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import unicodedata
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from importlib import import_module
from pathlib import Path

from goldenage.application.ports import ArtifactContentExtractor, MailImportClient
from goldenage.domain.models import (
    ExtractedArtifactData,
    ImportedMailPayload,
    MailCandidate,
    MailParticipant,
    MailSelector,
)

WHITESPACE_RE = re.compile(r"\s+")
FILENAME_RE = re.compile(r"[^a-z0-9]+")
DESKTOP_MAIL_SOURCE = "desktop_mail_client"
OL_MAIL_ITEM = 0
OL_MSG_UNICODE = 9


class MailImportClientError(RuntimeError):
    """Raised when a mailbox-backed import client cannot satisfy a request."""


class MultiplexedArtifactExtractor(ArtifactContentExtractor):
    """Choose a parser based on media type and file extension."""

    def __init__(
        self,
        *,
        outlook_extractor: ArtifactContentExtractor,
        rfc822_extractor: ArtifactContentExtractor,
    ) -> None:
        self._outlook_extractor = outlook_extractor
        self._rfc822_extractor = rfc822_extractor

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        normalized_name = file_name.lower()
        normalized_type = (media_type or "").lower()
        if normalized_name.endswith(".msg") or "ms-outlook" in normalized_type:
            return self._outlook_extractor.extract(file_name, media_type, content)
        if _requires_ocr(normalized_name, normalized_type):
            return ExtractedArtifactData(
                message_format="binary_document",
                parse_status="ocr_required",
                rfc_message_id=None,
                content_text="",
                subject=file_name,
                sender=None,
                recipients=(),
                sent_at=None,
            )
        return self._rfc822_extractor.extract(file_name, media_type, content)


class Rfc822EmailExtractor(ArtifactContentExtractor):
    """Extract normalized text and envelope fields from an RFC822 message."""

    def extract(self, file_name: str, media_type: str, content: bytes) -> ExtractedArtifactData:
        del media_type
        message = BytesParser(policy=policy.default).parsebytes(content)
        sender_name, sender_email = parseaddr(message.get("from", ""))
        recipients = tuple(
            MailParticipant(name=name or None, email=email or None)
            for name, email in getaddresses(message.get_all("to", []))
        )
        body_text = _extract_body_text(message)
        sent_at = _parse_sent_at(message.get("date"))
        subject = message.get("subject") or file_name
        rfc_message_id = message.get("message-id")
        return ExtractedArtifactData(
            message_format="rfc822_email",
            parse_status="parsed",
            rfc_message_id=rfc_message_id,
            content_text=body_text,
            subject=subject,
            sender=MailParticipant(name=sender_name or None, email=sender_email or None),
            recipients=recipients,
            sent_at=sent_at,
        )


def _requires_ocr(file_name: str, media_type: str) -> bool:
    """Return whether a document should wait for a later OCR pipeline."""
    return file_name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")) or media_type in {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
    }


class FixtureMailImportClient(MailImportClient):
    """Fixture-backed desktop mail client used for tests and local demos."""

    def __init__(self, fixture_path: Path | str) -> None:
        self._fixture_path = Path(fixture_path)

    def search_candidates(self, selector: MailSelector) -> tuple[MailCandidate, ...]:
        messages = _load_fixture_messages(self._fixture_path)
        results: list[MailCandidate] = []
        for message in messages:
            if selector.account_name and not _same_text(
                message.account_name, selector.account_name
            ):
                continue
            if selector.mailbox_name and not _same_text(
                message.mailbox_name, selector.mailbox_name
            ):
                continue
            if selector.unread_only and not message.unread:
                continue
            sender_text = " ".join(
                part for part in (message.sender_name, message.sender_email) if part
            )
            if selector.sender_filter and not _contains_text(sender_text, selector.sender_filter):
                continue
            if selector.subject_filter and not _contains_text(
                message.subject, selector.subject_filter
            ):
                continue
            if selector.sent_after and (
                message.sent_at is None or message.sent_at < selector.sent_after
            ):
                continue
            results.append(message)
        results.sort(
            key=lambda candidate: candidate.sent_at.isoformat() if candidate.sent_at else "",
            reverse=True,
        )
        return tuple(results[: selector.result_limit])

    def fetch_message(self, candidate_id: str) -> ImportedMailPayload:
        for raw_message in _load_fixture_raw_messages(self._fixture_path):
            if raw_message["candidate_id"] != candidate_id:
                continue
            raw_source = _coerce_optional_string(raw_message.get("raw_source"))
            if raw_source is None:
                raise MailImportClientError("Mail fixture candidate has no raw source.")
            return ImportedMailPayload(
                source_system=DESKTOP_MAIL_SOURCE,
                external_message_id=str(raw_message["candidate_id"]),
                rfc_message_id=_coerce_optional_string(raw_message.get("rfc_message_id")),
                account_name=_coerce_optional_string(raw_message.get("account_name")),
                mailbox_name=_coerce_optional_string(raw_message.get("mailbox_name")),
                file_name=_candidate_file_name(_coerce_optional_string(raw_message.get("subject"))),
                media_type="message/rfc822",
                content=raw_source.encode("utf-8"),
                unread=bool(raw_message.get("unread", False)),
            )
        raise MailImportClientError("Mail candidate not found in the fixture source.")


class OsaScriptAppleMailImportClient(MailImportClient):
    """Best-effort Apple Mail client backed by macOS Automation via osascript."""

    def __init__(self, executable: str = "osascript") -> None:
        self._executable = executable

    def search_candidates(self, selector: MailSelector) -> tuple[MailCandidate, ...]:
        payload: dict[str, object | None] = {
            "accountName": selector.account_name,
            "mailboxName": selector.mailbox_name,
            "unreadOnly": selector.unread_only,
            "senderFilter": selector.sender_filter,
            "subjectFilter": selector.subject_filter,
            "sentAfter": selector.sent_after.isoformat() if selector.sent_after else None,
            "resultLimit": selector.result_limit,
        }
        records = json.loads(self._run_jxa(_search_script(), payload))
        candidates = tuple(_candidate_from_osascript(record) for record in records)
        filtered = [
            candidate
            for candidate in candidates
            if (not selector.unread_only or candidate.unread)
            and (
                not selector.sender_filter
                or _contains_text(
                    " ".join(
                        part for part in (candidate.sender_name, candidate.sender_email) if part
                    ),
                    selector.sender_filter,
                )
            )
            and (
                not selector.subject_filter
                or _contains_text(candidate.subject, selector.subject_filter)
            )
            and (
                selector.sent_after is None
                or (candidate.sent_at is not None and candidate.sent_at >= selector.sent_after)
            )
        ]
        filtered.sort(
            key=lambda candidate: (
                candidate.sent_at.isoformat() if candidate.sent_at is not None else ""
            ),
            reverse=True,
        )
        return tuple(filtered[: selector.result_limit])

    def fetch_message(self, candidate_id: str) -> ImportedMailPayload:
        record = json.loads(self._run_jxa(_fetch_script(), {"candidateId": candidate_id}))
        raw_source = _coerce_optional_string(record.get("rawSource"))
        if not raw_source:
            raise MailImportClientError("Apple Mail did not return raw message source.")
        return ImportedMailPayload(
            source_system=DESKTOP_MAIL_SOURCE,
            external_message_id=str(record["candidateId"]),
            rfc_message_id=_coerce_optional_string(record.get("rfcMessageId")),
            account_name=_coerce_optional_string(record.get("accountName")),
            mailbox_name=_coerce_optional_string(record.get("mailboxName")),
            file_name=_candidate_file_name(_coerce_optional_string(record.get("subject"))),
            media_type="message/rfc822",
            content=raw_source.encode("utf-8"),
            unread=bool(record.get("unread", False)),
        )

    def _run_jxa(self, script: str, payload: dict[str, object | None]) -> str:
        env = os.environ.copy()
        env["GOLDENAGE_APPLE_MAIL_QUERY"] = json.dumps(payload)
        result = subprocess.run(
            [self._executable, "-l", "JavaScript", "-"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "unknown osascript error"
            raise MailImportClientError(f"Apple Mail automation failed: {stderr}")
        return result.stdout.strip()


def build_apple_mail_import_client(
    *,
    fixture_path: Path | None,
    client_mode: str | None,
) -> MailImportClient | None:
    """Build the best available Apple Mail client for the current runtime."""
    if fixture_path is not None:
        return FixtureMailImportClient(fixture_path)
    normalized_mode = (client_mode or "auto").lower()
    if normalized_mode == "disabled":
        return None
    if shutil.which("osascript") is None:
        return None
    return OsaScriptAppleMailImportClient()


def build_desktop_mail_import_client(
    *,
    fixture_path: Path | None,
    client_mode: str | None,
    outlook_scan_per_folder_limit: int = 250,
) -> MailImportClient | None:
    """Build the best available desktop mail client for the current runtime."""
    if fixture_path is not None:
        return FixtureMailImportClient(fixture_path)

    normalized_mode = (client_mode or "disabled").strip().lower()
    if normalized_mode == "disabled":
        return None
    if normalized_mode in {"apple-mail", "apple_mail"}:
        if shutil.which("osascript") is None:
            return None
        return OsaScriptAppleMailImportClient()
    if normalized_mode in {"outlook-windows", "outlook_windows"}:
        return OutlookWindowsImportClient(scan_per_folder_limit=outlook_scan_per_folder_limit)
    if normalized_mode != "auto":
        raise MailImportClientError(f"Unsupported mail client mode: {client_mode}")

    system_name = platform.system().lower()
    if system_name == "darwin" and shutil.which("osascript") is not None:
        return OsaScriptAppleMailImportClient()
    if system_name == "windows":
        return OutlookWindowsImportClient(scan_per_folder_limit=outlook_scan_per_folder_limit)
    return None


class OutlookWindowsImportClient(MailImportClient):
    """Classic Outlook desktop client backed by Windows COM automation."""

    def __init__(
        self,
        *,
        scan_per_folder_limit: int = 250,
        dispatch_factory=None,
        temp_dir: Path | None = None,
    ) -> None:
        self._scan_per_folder_limit = max(scan_per_folder_limit, 1)
        self._dispatch_factory = dispatch_factory
        self._temp_dir = temp_dir

    def search_candidates(self, selector: MailSelector) -> tuple[MailCandidate, ...]:
        if not selector.account_name:
            raise MailImportClientError("Configure a mail address before searching Outlook.")

        with _com_session(self._dispatch_factory) as outlook:
            namespace = outlook.Session
            store = _find_outlook_store(namespace, selector.account_name)
            root_folder = store.GetRootFolder()
            candidates: list[MailCandidate] = []
            for folder in _walk_outlook_folders(root_folder):
                folder_name = _coerce_optional_string(_com_get(folder, "Name"))
                if selector.mailbox_name and not _same_text(folder_name, selector.mailbox_name):
                    continue
                for item in _iter_outlook_mail_items(folder, self._scan_per_folder_limit):
                    candidate = _candidate_from_outlook_item(
                        item=item,
                        store_id=str(_com_get(store, "StoreID") or ""),
                        account_name=_store_display_name(store),
                        mailbox_name=folder_name,
                    )
                    if _outlook_candidate_matches(candidate, selector):
                        candidates.append(candidate)

        candidates.sort(
            key=lambda candidate: (
                candidate.sent_at.isoformat() if candidate.sent_at is not None else ""
            ),
            reverse=True,
        )
        return tuple(candidates[: selector.result_limit])

    def fetch_message(self, candidate_id: str) -> ImportedMailPayload:
        token = _decode_outlook_candidate_id(candidate_id)
        with _com_session(self._dispatch_factory) as outlook:
            namespace = outlook.Session
            item = namespace.GetItemFromID(token["entry_id"], token["store_id"])
            subject = _coerce_optional_string(_com_get(item, "Subject"))
            with tempfile.NamedTemporaryFile(
                suffix=".msg",
                dir=self._temp_dir,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
            try:
                item.SaveAs(str(temp_path), OL_MSG_UNICODE)
                content = temp_path.read_bytes()
            finally:
                temp_path.unlink(missing_ok=True)
            return ImportedMailPayload(
                source_system=DESKTOP_MAIL_SOURCE,
                external_message_id=candidate_id,
                rfc_message_id=_coerce_optional_string(
                    _outlook_property(item, "http://schemas.microsoft.com/mapi/proptag/0x1035001F")
                ),
                account_name=_coerce_optional_string(token.get("account_name")),
                mailbox_name=_coerce_optional_string(token.get("mailbox_name")),
                file_name=_outlook_file_name(subject),
                media_type="application/vnd.ms-outlook",
                content=content,
                unread=bool(_com_get(item, "UnRead")),
            )


def _load_fixture_messages(fixture_path: Path) -> tuple[MailCandidate, ...]:
    records = []
    for raw_message in _load_fixture_raw_messages(fixture_path):
        records.append(
            MailCandidate(
                candidate_id=str(raw_message["candidate_id"]),
                source_system=DESKTOP_MAIL_SOURCE,
                account_name=_coerce_optional_string(raw_message.get("account_name")),
                mailbox_name=_coerce_optional_string(raw_message.get("mailbox_name")),
                subject=_coerce_optional_string(raw_message.get("subject")),
                sender_name=_coerce_optional_string(raw_message.get("sender_name")),
                sender_email=_coerce_optional_string(raw_message.get("sender_email")),
                sent_at=_parse_optional_datetime(raw_message.get("sent_at")),
                preview_text=_coerce_optional_string(raw_message.get("preview_text")) or "",
                unread=bool(raw_message.get("unread", False)),
                rfc_message_id=_coerce_optional_string(raw_message.get("rfc_message_id")),
            )
        )
    return tuple(records)


def _load_fixture_raw_messages(fixture_path: Path) -> tuple[dict[str, object], ...]:
    raw_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, list):
        raise MailImportClientError("Mail fixture must be a JSON array.")
    records: list[dict[str, object]] = []
    for item in raw_payload:
        if not isinstance(item, dict) or "candidate_id" not in item or "raw_source" not in item:
            raise MailImportClientError(
                "Each mail fixture record must include candidate_id and raw_source."
            )
        records.append(item)
    return tuple(records)


def _candidate_from_osascript(record: dict[str, object]) -> MailCandidate:
    sender_name, sender_email = parseaddr(_coerce_optional_string(record.get("sender")) or "")
    return MailCandidate(
        candidate_id=str(record["candidateId"]),
        source_system=DESKTOP_MAIL_SOURCE,
        account_name=_coerce_optional_string(record.get("accountName")),
        mailbox_name=_coerce_optional_string(record.get("mailboxName")),
        subject=_coerce_optional_string(record.get("subject")),
        sender_name=sender_name or None,
        sender_email=sender_email or None,
        sent_at=_parse_optional_datetime(record.get("sentAt")),
        preview_text=_coerce_optional_string(record.get("previewText")) or "",
        unread=bool(record.get("unread", False)),
        rfc_message_id=_coerce_optional_string(record.get("rfcMessageId")),
    )


class _com_session:
    def __init__(self, dispatch_factory) -> None:
        self._dispatch_factory = dispatch_factory
        self._pythoncom = None
        self._outlook = None

    def __enter__(self):
        if self._dispatch_factory is not None:
            self._outlook = self._dispatch_factory("Outlook.Application")
            return self._outlook
        try:
            pythoncom = import_module("pythoncom")
            win32com_client = import_module("win32com.client")
        except ImportError as error:
            raise MailImportClientError(
                "Windows Outlook import requires pywin32 and classic Outlook desktop."
            ) from error
        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        self._outlook = win32com_client.Dispatch("Outlook.Application")
        return self._outlook

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        if self._pythoncom is not None:
            self._pythoncom.CoUninitialize()


def _find_outlook_store(namespace, configured_address: str):
    stores = _com_collection(namespace.Stores)
    account_matches = []
    for account in _com_collection(namespace.Accounts):
        smtp_address = _coerce_optional_string(_com_get(account, "SmtpAddress"))
        display_name = _coerce_optional_string(_com_get(account, "DisplayName"))
        if _same_text(smtp_address, configured_address) or _same_text(
            display_name, configured_address
        ):
            delivery_store = _com_get(account, "DeliveryStore")
            if delivery_store is not None:
                account_matches.append(delivery_store)
    if account_matches:
        return account_matches[0]

    for store in stores:
        display_name = _store_display_name(store)
        if _same_text(display_name, configured_address):
            return store

    available = ", ".join(_store_display_name(store) or "(unnamed)" for store in stores)
    raise MailImportClientError(
        f'Outlook mail address "{configured_address}" was not found. Available stores: {available}'
    )


def _walk_outlook_folders(root_folder) -> tuple[object, ...]:
    folders = [root_folder]
    index = 0
    while index < len(folders):
        folder = folders[index]
        index += 1
        for child in _com_collection(_com_get(folder, "Folders")):
            folders.append(child)
    return tuple(folders)


def _iter_outlook_mail_items(folder, limit: int) -> tuple[object, ...]:
    if _com_get(folder, "DefaultItemType") not in {None, OL_MAIL_ITEM}:
        return ()
    items = _com_get(folder, "Items")
    if items is None:
        return ()
    try:
        items.Sort("[ReceivedTime]", True)
    except Exception:
        pass
    mail_items = []
    for item in _com_collection(items):
        if _com_get(item, "Class") not in {None, 43}:
            continue
        mail_items.append(item)
        if len(mail_items) >= limit:
            break
    return tuple(mail_items)


def _candidate_from_outlook_item(
    *,
    item,
    store_id: str,
    account_name: str | None,
    mailbox_name: str | None,
) -> MailCandidate:
    sender_name = _coerce_optional_string(_com_get(item, "SenderName"))
    sender_email = _coerce_optional_string(_com_get(item, "SenderEmailAddress"))
    sent_at = _parse_optional_outlook_datetime(
        _com_get(item, "SentOn") or _com_get(item, "ReceivedTime")
    )
    subject = _coerce_optional_string(_com_get(item, "Subject"))
    preview_text = _normalize_text(_coerce_optional_string(_com_get(item, "Body")) or "")[:280]
    rfc_message_id = _coerce_optional_string(
        _outlook_property(item, "http://schemas.microsoft.com/mapi/proptag/0x1035001F")
    )
    return MailCandidate(
        candidate_id=_encode_outlook_candidate_id(
            store_id=store_id,
            entry_id=str(_com_get(item, "EntryID") or ""),
            account_name=account_name,
            mailbox_name=mailbox_name,
        ),
        source_system=DESKTOP_MAIL_SOURCE,
        account_name=account_name,
        mailbox_name=mailbox_name,
        subject=subject,
        sender_name=sender_name,
        sender_email=sender_email,
        sent_at=sent_at,
        preview_text=preview_text,
        unread=bool(_com_get(item, "UnRead")),
        rfc_message_id=rfc_message_id,
    )


def _outlook_candidate_matches(candidate: MailCandidate, selector: MailSelector) -> bool:
    if selector.unread_only and not candidate.unread:
        return False
    sender_text = " ".join(part for part in (candidate.sender_name, candidate.sender_email) if part)
    if selector.sender_filter and not _contains_text(sender_text, selector.sender_filter):
        return False
    if selector.subject_filter and not _contains_text(candidate.subject, selector.subject_filter):
        return False
    return not (
        selector.sent_after
        and (candidate.sent_at is None or candidate.sent_at < selector.sent_after)
    )


def _com_collection(collection) -> tuple[object, ...]:
    if collection is None:
        return ()
    try:
        return tuple(collection)
    except TypeError:
        pass
    try:
        count = int(collection.Count)
    except Exception:
        return ()
    return tuple(collection.Item(index) for index in range(1, count + 1))


def _com_get(item, name: str):
    try:
        return getattr(item, name)
    except Exception:
        return None


def _outlook_property(item, schema: str):
    try:
        return item.PropertyAccessor.GetProperty(schema)
    except Exception:
        return None


def _store_display_name(store) -> str | None:
    return _coerce_optional_string(
        _com_get(store, "DisplayName") or _com_get(store, "FilePath") or _com_get(store, "StoreID")
    )


def _encode_outlook_candidate_id(
    *,
    store_id: str,
    entry_id: str,
    account_name: str | None,
    mailbox_name: str | None,
) -> str:
    payload = {
        "store_id": store_id,
        "entry_id": entry_id,
        "account_name": account_name,
        "mailbox_name": mailbox_name,
    }
    encoded = urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def _decode_outlook_candidate_id(candidate_id: str) -> dict[str, str | None]:
    padding = "=" * (-len(candidate_id) % 4)
    try:
        payload = json.loads(urlsafe_b64decode(f"{candidate_id}{padding}").decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as error:
        raise MailImportClientError("Outlook candidate id is malformed.") from error
    if not isinstance(payload, dict) or not payload.get("store_id") or not payload.get("entry_id"):
        raise MailImportClientError("Outlook candidate id is incomplete.")
    return {
        "store_id": str(payload["store_id"]),
        "entry_id": str(payload["entry_id"]),
        "account_name": _coerce_optional_string(payload.get("account_name")),
        "mailbox_name": _coerce_optional_string(payload.get("mailbox_name")),
    }


def _parse_optional_outlook_datetime(raw_value: object) -> datetime | None:
    if isinstance(raw_value, datetime):
        return raw_value
    if raw_value is None:
        return None
    try:
        return datetime.fromisoformat(str(raw_value))
    except ValueError:
        return None


def _extract_body_text(message) -> str:
    preferred_part = message.get_body(preferencelist=("plain", "html"))
    if preferred_part is not None:
        body_text = preferred_part.get_content()
        return _normalize_text(str(body_text))

    if message.is_multipart():
        parts = [
            part.get_content()
            for part in message.walk()
            if part.get_content_type() == "text/plain"
            and "attachment" not in (part.get("Content-Disposition") or "").lower()
        ]
        return _normalize_text("\n".join(str(part) for part in parts))

    return _normalize_text(str(message.get_content()))


def _parse_sent_at(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        return parsedate_to_datetime(raw_value)
    except TypeError, ValueError:
        return None


def _parse_optional_datetime(raw_value: object) -> datetime | None:
    if raw_value is None or raw_value == "":
        return None
    if isinstance(raw_value, datetime):
        return raw_value
    if isinstance(raw_value, str):
        return datetime.fromisoformat(raw_value)
    raise MailImportClientError("Unsupported datetime value in Apple Mail data.")


def _coerce_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = _sanitize_text(str(value)).strip()
    return text or None


def _sanitize_text(value: str) -> str:
    """Normalize Apple Mail text and replace malformed surrogate output."""
    text = value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return unicodedata.normalize("NFC", text)


def _contains_text(value: str | None, needle: str) -> bool:
    haystack_forms = _comparison_forms(value)
    needle_forms = _comparison_forms(needle)
    return any(
        needle_form in haystack_form
        for haystack_form in haystack_forms
        for needle_form in needle_forms
    )


def _same_text(left: str | None, right: str | None) -> bool:
    return bool(_comparison_forms(left) & _comparison_forms(right))


def _comparison_forms(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    normalized = _sanitize_text(value).casefold()
    forms = {
        normalized,
        _strip_diacritics(normalized),
        _german_transliteration(normalized),
    }
    return frozenset(form for form in forms if form)


def _strip_diacritics(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def _german_transliteration(value: str) -> str:
    return value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def _normalize_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", _sanitize_text(value)).strip()


def _candidate_file_name(subject: str | None) -> str:
    ascii_subject = (
        _strip_diacritics(_sanitize_text(subject or "mail-message"))
        .encode(
            "ascii",
            "ignore",
        )
        .decode("ascii")
    )
    stem = FILENAME_RE.sub("-", ascii_subject.casefold()).strip("-")
    return f"{stem or 'mail-message'}.eml"


def _outlook_file_name(subject: str | None) -> str:
    stem = _candidate_file_name(subject).removesuffix(".eml")
    return f"{stem or 'outlook-message'}.msg"


def _search_script() -> str:
    return """
ObjC.import("stdlib");
const params = JSON.parse($.getenv("GOLDENAGE_APPLE_MAIL_QUERY"));
const mail = Application("/System/Applications/Mail.app");
const records = [];
const senderFilter = String(params.senderFilter || "");
const subjectFilter = String(params.subjectFilter || "");
const sentAfter = params.sentAfter ? new Date(params.sentAfter) : null;
const resultLimit = Math.max(1, Number(params.resultLimit || 25));

function safeCall(fn, fallback) {
  try {
    return fn();
  } catch (error) {
    return fallback;
  }
}

function normalizeText(value) {
  return String(value || "").normalize("NFC").toLowerCase();
}

function stripDiacritics(value) {
  return value.normalize("NFKD").replace(/[\\u0300-\\u036f]/g, "");
}

function germanTransliteration(value) {
  return value
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/ß/g, "ss");
}

function comparisonForms(value) {
  const normalized = normalizeText(value);
  return [normalized, stripDiacritics(normalized), germanTransliteration(normalized)]
    .filter(form => form);
}

function containsText(value, needle) {
  const haystackForms = comparisonForms(value);
  const needleForms = comparisonForms(needle);
  return haystackForms.some(haystack => needleForms.some(item => haystack.indexOf(item) !== -1));
}

function sameText(left, right) {
  const leftForms = comparisonForms(left);
  const rightForms = comparisonForms(right);
  return leftForms.some(leftItem => rightForms.some(rightItem => leftItem === rightItem));
}

function previewText(message) {
  const content = safeCall(() => message.content(), "");
  return String(content || "").replace(/\\s+/g, " ").trim().slice(0, 280);
}

function namesOf(items) {
  return items.map(item => safeCall(() => item.name(), "")).filter(name => name);
}

function selectByName(items, wanted, label) {
  if (!wanted) {
    return items;
  }
  const selected = items.filter(item => {
    const name = safeCall(() => item.name(), "");
    return sameText(name, wanted);
  });
  if (selected.length) {
    return selected;
  }
  throw new Error(
    `${label} "${wanted}" was not found. Available ${label}s: ${namesOf(items).join(", ")}`
  );
}

function filteredMessages(mailbox) {
  const conditions = [];
  if (params.unreadOnly) {
    conditions.push({readStatus: false});
  }
  if (sentAfter) {
    conditions.push({dateSent: {_greaterThan: sentAfter}});
  }
  if (conditions.length === 0) {
    return mailbox.messages();
  }
  if (conditions.length === 1) {
    return mailbox.messages.whose(conditions[0])();
  }
  return mailbox.messages.whose({_and: conditions})();
}

outer:
for (const account of selectByName(mail.accounts(), params.accountName, "account")) {
  const accountName = safeCall(() => account.name(), null);
  for (const mailbox of selectByName(account.mailboxes(), params.mailboxName, "mailbox")) {
    const mailboxName = safeCall(() => mailbox.name(), null);
    for (const message of filteredMessages(mailbox)) {
      const readStatus = safeCall(() => message.readStatus(), false);
      const subject = safeCall(() => message.subject(), null);
      const sender = safeCall(() => message.sender(), null);
      if (senderFilter && !containsText(sender, senderFilter)) {
        continue;
      }
      if (subjectFilter && !containsText(subject, subjectFilter)) {
        continue;
      }
      const sentAt = safeCall(() => message.dateSent(), null);
      records.push({
        candidateId: String(safeCall(() => message.id(), "")),
        accountName,
        mailboxName,
        subject,
        sender,
        sentAt: sentAt ? sentAt.toISOString() : null,
        previewText: previewText(message),
        unread: !readStatus,
        rfcMessageId: safeCall(() => message.messageId(), null),
      });
      if (records.length >= resultLimit) {
        break outer;
      }
    }
  }
}

JSON.stringify(records);
"""


def _fetch_script() -> str:
    return """
ObjC.import("stdlib");
const params = JSON.parse($.getenv("GOLDENAGE_APPLE_MAIL_QUERY"));
const targetId = String(params.candidateId);
const mail = Application("/System/Applications/Mail.app");
let found = null;

function safeCall(fn, fallback) {
  try {
    return fn();
  } catch (error) {
    return fallback;
  }
}

for (const account of mail.accounts()) {
  const accountName = safeCall(() => account.name(), null);
  for (const mailbox of account.mailboxes()) {
    const mailboxName = safeCall(() => mailbox.name(), null);
    for (const message of mailbox.messages()) {
      const messageId = String(safeCall(() => message.id(), ""));
      if (messageId !== targetId) {
        continue;
      }
      const sentAt = safeCall(() => message.dateSent(), null);
      found = {
        candidateId: messageId,
        accountName,
        mailboxName,
        subject: safeCall(() => message.subject(), null),
        rawSource: safeCall(() => message.source(), null),
        unread: !safeCall(() => message.readStatus(), false),
        rfcMessageId: safeCall(() => message.messageId(), null),
        sentAt: sentAt ? sentAt.toISOString() : null,
      };
    }
  }
}

if (!found) {
  throw new Error("Mail message not found");
}

JSON.stringify(found);
"""

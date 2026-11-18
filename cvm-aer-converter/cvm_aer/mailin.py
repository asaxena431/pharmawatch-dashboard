"""E-mail intake: each case e-mail becomes one ZIP in the inbound folder.

One e-mail is one case, and the ZIP keeps the sender's file name: an attached
ZIP's own name, or the form PDF's (``N141562.pdf`` -> ``N141562.zip``).  With ``[intake] subject_contains`` set, only e-mails
whose subject holds one of its words are read; every other e-mail is left alone.  A lone ZIP attachment is put in inbound as it is;
otherwise every attachment (the form and its supporting documents) is zipped
into one ZIP.  The e-mail is then moved to ``[intake] processed_folder`` - or,
when it has nothing to take, to ``rejected_folder`` with the [email] note - and
its Message-ID is written to the ledger, so no e-mail is ever taken twice even
if it could not be moved.

    python -m cvm_aer intake          # read the mailbox once
    python -m cvm_aer                 # the service reads it on every sweep
"""

from __future__ import annotations

import email
import email.policy
import fnmatch
import imaplib
import io
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .service import (
    SOURCE_FOLDER,
    SOURCE_GRAPH,
    SOURCE_IMAP,
    SOURCE_OUTLOOK,
    IntakeSettings,
    ServiceConfig,
    _move,
    send_note,
)

LOGGER = logging.getLogger("cvm_aer.mailin")

OL_FOLDER_INBOX = 6
OL_MAIL_ITEM = 43
PR_INTERNET_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"


class Rejected(ValueError):
    """The e-mail does not carry one case the service can take."""


@dataclass
class Mail:
    key: str
    subject: str
    sender: str
    received: datetime
    attachments: List[Tuple[str, bytes]]


@dataclass
class Collected:
    """What became of one e-mail."""

    subject: str
    sender: str
    archive: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _slug(text: str, limit: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text or "").strip("-.")[:limit].strip("-.")


def _unique(name: str, used: set) -> str:
    stem, extension = os.path.splitext(name)
    candidate, index = name, 2
    while candidate.lower() in used:
        candidate = f"{stem}-{index}{extension}"
        index += 1
    used.add(candidate.lower())
    return candidate


def case_files(mail: Mail, ignore: Sequence[str]) -> List[Tuple[str, bytes]]:
    """The attachments that belong to the case: signature images and empty parts left out."""
    kept = []
    for name, payload in mail.attachments:
        name = os.path.basename((name or "").replace("\\", "/"))
        if not name or not payload:
            continue
        if any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in ignore):
            LOGGER.info("left out %s from %r (matches [intake] ignore)", name, mail.subject)
            continue
        kept.append((name, payload))
    return kept


def _case_name(files: Sequence[Tuple[str, bytes]]) -> str:
    """The ZIP keeps the sender's file name: the attached ZIP's, else the form PDF's."""
    names = [name for name, _ in files]
    chosen = next((name for name in names if name.lower().endswith(".zip")), None) or \
        next((name for name in names if name.lower().endswith(".pdf")), names[0])
    # only what Windows cannot have in a file name is replaced
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.splitext(chosen)[0]).strip(" .")
    return stem or "case"


def build_zip(mail: Mail, inbound: str, ignore: Sequence[str] = ()) -> str:
    """Put the e-mail's case in inbound as one ZIP and return its path."""
    files = case_files(mail, ignore)
    if not files:
        raise Rejected("the e-mail has no attachments to take as a case")
    zips = [(name, payload) for name, payload in files if name.lower().endswith(".zip")]
    if len(zips) > 1:
        raise Rejected(f"the e-mail has {len(zips)} ZIP files; one e-mail must carry one case")
    for name, payload in zips:
        if not zipfile.is_zipfile(io.BytesIO(payload)):
            raise Rejected(f"{name} is not a readable ZIP")

    os.makedirs(inbound, exist_ok=True)
    stem = _case_name(files)
    target, index = os.path.join(inbound, f"{stem}.zip"), 2
    while os.path.exists(target):
        target = os.path.join(inbound, f"{stem}-{index}.zip")
        index += 1
    # Written under another name and renamed, so the sweep never sees half a ZIP.
    partial = target + ".part"
    if len(files) == 1 and zips:
        with open(partial, "wb") as handle:
            handle.write(zips[0][1])
    else:
        used: set = set()
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in files:
                if not name.lower().endswith(".zip"):
                    archive.writestr(_unique(name, used), payload)
                    continue
                with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                    for info in inner.infolist():
                        member = os.path.basename(info.filename)
                        if not info.is_dir() and member and not member.startswith("."):
                            archive.writestr(_unique(member, used), inner.read(info))
    os.replace(partial, target)
    return target


class Ledger:
    """Message-IDs already taken in, one per line: key, time, outcome."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self.entries: Dict[str, str] = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    parts = line.rstrip("\n").split("\t")
                    if parts and parts[0]:
                        self.entries[parts[0]] = parts[2] if len(parts) > 2 else ""

    def get(self, key: str) -> Optional[str]:
        return self.entries.get(key)

    def add(self, key: str, outcome: str) -> None:
        self.entries[key] = outcome
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(f"{key}\t{datetime.now().isoformat(timespec='seconds')}\t{outcome}\n")


def parse_rfc822(raw: bytes, fallback_key: str) -> Mail:
    """A Mail from an e-mail's raw bytes (IMAP, or a saved .eml file)."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        received = parsedate_to_datetime(str(message["Date"]))
        received = received.astimezone().replace(tzinfo=None) if received.tzinfo else received
    except (TypeError, ValueError):
        received = datetime.now()
    attachments = []
    for part in message.walk():
        if part.is_multipart() or not part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append((part.get_filename(), payload))
    return Mail(
        key=str(message.get("Message-ID") or "").strip() or fallback_key,
        subject=str(message.get("Subject") or ""),
        sender=str(message.get("From") or ""),
        received=received,
        attachments=attachments,
    )


class FolderSource:
    """Saved .eml files in ``eml_dir``; filed into its processed / rejected subfolders."""

    def __init__(self, settings: IntakeSettings):
        self.settings = settings
        self.directory = settings.eml_dir or "."

    def messages(self) -> Iterator[Tuple[Mail, Any]]:
        if not os.path.isdir(self.directory):
            return
        for name in sorted(os.listdir(self.directory)):
            path = os.path.join(self.directory, name)
            if name.lower().endswith(".eml") and os.path.isfile(path):
                with open(path, "rb") as handle:
                    yield parse_rfc822(handle.read(), f"file:{name}"), path

    def file(self, path: str, ok: bool) -> None:
        folder = self.settings.processed_folder if ok else self.settings.rejected_folder
        _move(path, os.path.join(self.directory, folder))

    def close(self) -> None:
        pass


def _imap_name(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapSource:
    """A mail server over IMAP; e-mails are moved to the processed / rejected folders."""

    def __init__(self, settings: IntakeSettings):
        self.settings = settings
        connect = imaplib.IMAP4_SSL if settings.use_ssl else imaplib.IMAP4
        self.connection = connect(settings.host, settings.port)
        self.connection.login(settings.username, settings.password or "")
        for folder in (settings.processed_folder, settings.rejected_folder):
            try:
                self.connection.create(_imap_name(folder))
            except imaplib.IMAP4.error:  # already there
                pass
        status, detail = self.connection.select(_imap_name(settings.folder))
        if status != "OK":
            raise RuntimeError(f"cannot open the IMAP folder {settings.folder}: {detail}")

    def messages(self) -> Iterator[Tuple[Mail, Any]]:
        status, data = self.connection.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise RuntimeError(f"cannot list {self.settings.folder}: {data}")
        for uid in (data[0] or b"").split():
            status, parts = self.connection.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((part[1] for part in parts or () if isinstance(part, tuple)), None)
            if status == "OK" and raw:
                yield parse_rfc822(raw, f"imap:{self.settings.folder}:{uid.decode()}"), uid

    def file(self, uid: bytes, ok: bool) -> None:
        folder = _imap_name(self.settings.processed_folder if ok else self.settings.rejected_folder)
        if "MOVE" in getattr(self.connection, "capabilities", ()):
            self.connection.uid("MOVE", uid, folder)
            return
        self.connection.uid("COPY", uid, folder)
        self.connection.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        self.connection.expunge()

    def close(self) -> None:
        try:
            self.connection.logout()
        except Exception:
            pass


def _outlook_folders(parent: Any) -> List[Any]:
    folders = parent.Folders
    return [folders.Item(index) for index in range(1, folders.Count + 1)]


def _outlook_names(folders: Sequence[Any]) -> str:
    return ", ".join(repr(str(folder.Name)) for folder in folders) or "none"


def _outlook_child(parent: Any, name: str, create: bool) -> Any:
    """``parent``'s subfolder ``name``, whatever its case; Outlook's own lookup fails with "Array index out of bounds"."""
    children = _outlook_folders(parent)
    for child in children:
        if str(child.Name).lower() == name.lower():
            return child
    if create:
        return parent.Folders.Add(name)
    raise RuntimeError(
        f"Outlook has no folder {name!r} in {str(getattr(parent, 'Name', parent))!r} "
        f"(it has: {_outlook_names(children)}); check [intake] folder"
    )


def _outlook_shared_inbox(namespace: Any, mailbox: str, mailboxes: Sequence[Any]) -> Any:
    """The Inbox of a mailbox that is not in Outlook's folder list but this account may open."""
    try:
        recipient = namespace.CreateRecipient(mailbox)
        recipient.Resolve()
        if recipient.Resolved:
            return namespace.GetSharedDefaultFolder(recipient, OL_FOLDER_INBOX)
    except Exception as exc:
        LOGGER.debug("opening %r as a shared mailbox failed: %s: %s", mailbox, type(exc).__name__, exc)
    raise RuntimeError(
        f"Outlook has no mailbox {mailbox!r} (the mailboxes in this Outlook: {_outlook_names(mailboxes)}); "
        "set [intake] mailbox to one of them, or leave it empty for your own"
    )


def _outlook_watched(namespace: Any, settings: IntakeSettings) -> Any:
    parts = [part for part in re.split(r"[\\/]", settings.folder or "Inbox") if part]
    inbox_first = bool(parts) and parts[0].lower() == "inbox"
    if settings.mailbox:
        mailboxes = _outlook_folders(namespace)
        folder = next((box for box in mailboxes if str(box.Name).lower() == settings.mailbox.lower()), None)
        if folder is None:
            if not inbox_first:
                raise RuntimeError(
                    f"Outlook has no mailbox {settings.mailbox!r} (the mailboxes in this Outlook: "
                    f"{_outlook_names(mailboxes)}); set [intake] mailbox to one of them, or leave it empty for your own"
                )
            folder, parts = _outlook_shared_inbox(namespace, settings.mailbox, mailboxes), parts[1:]
    elif inbox_first:
        folder, parts = namespace.GetDefaultFolder(OL_FOLDER_INBOX), parts[1:]
    else:
        folder = namespace.GetDefaultFolder(OL_FOLDER_INBOX).Parent
    for part in parts:
        folder = _outlook_child(folder, part, create=False)
    return folder


def _outlook_time(value: Any) -> datetime:
    try:
        return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    except Exception:
        return datetime.now()


class OutlookSource:
    """The Outlook signed in on this PC; e-mails are moved to subfolders of the watched one."""

    def __init__(self, settings: IntakeSettings):
        import win32com.client  # pywin32, and Windows: only this source needs it

        self.settings = settings

        namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        self.folder = folder = _outlook_watched(namespace, settings)
        self.processed = _outlook_child(folder, settings.processed_folder, create=True)
        self.rejected = _outlook_child(folder, settings.rejected_folder, create=True)

    def messages(self) -> Iterator[Tuple[Mail, Any]]:
        items = self.folder.Items
        # Taken up front: moving an item renumbers the collection.  The count can drop
        # while they are taken (an Outlook rule, a sync), so a vanished item is skipped.
        taken = []
        for index in range(1, items.Count + 1):
            try:
                taken.append(items.Item(index))
            except Exception:
                break
        for item in taken:
            try:
                # The subject is checked first so other e-mails' attachments are never opened.
                if getattr(item, "Class", None) != OL_MAIL_ITEM or not self.settings.wants(item.Subject):
                    continue
                mail = self._mail(item)
            except Exception as exc:  # left where it is, to be tried again next sweep
                LOGGER.error("an Outlook item could not be read: %s: %s", type(exc).__name__, exc)
                continue
            yield mail, item

    @staticmethod
    def _mail(item: Any) -> Mail:
        try:
            key = str(item.PropertyAccessor.GetProperty(PR_INTERNET_MESSAGE_ID) or "").strip()
        except Exception:
            key = ""
        attachments = []
        with tempfile.TemporaryDirectory(prefix="cvm-aer-mail-") as workspace:
            for index in range(1, item.Attachments.Count + 1):
                attachment = item.Attachments.Item(index)
                path = os.path.join(workspace, f"{index}-{_slug(attachment.FileName, 120) or 'file'}")
                attachment.SaveAsFile(path)
                with open(path, "rb") as handle:
                    attachments.append((attachment.FileName, handle.read()))
        return Mail(
            key=key or f"outlook:{item.EntryID}",
            subject=str(item.Subject or ""),
            sender=str(getattr(item, "SenderEmailAddress", "") or ""),
            received=_outlook_time(item.ReceivedTime),
            attachments=attachments,
        )

    def file(self, item: Any, ok: bool) -> None:
        item.UnRead = False
        item.Move(self.processed if ok else self.rejected)

    def close(self) -> None:
        pass


GRAPH_URL = "https://graph.microsoft.com/v1.0"
GRAPH_LOGIN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_FILE = "#microsoft.graph.fileAttachment"
GRAPH_ITEM = "#microsoft.graph.itemAttachment"
GRAPH_TRIES = 4


class GraphSource:
    """A Microsoft 365 mailbox over Microsoft Graph, signed in as the app (tenant, client ID, secret).

    The app needs the application permission Mail.ReadWrite with admin consent; an
    Exchange application access policy can keep it to the case mailbox.  E-mails are
    moved to subfolders of the watched one, as with Outlook.  An e-mail forwarded as an
    attachment gives up the attachments of the e-mail inside it.
    """

    def __init__(self, settings: IntakeSettings):
        self.settings = settings
        self.mailbox = urllib.parse.quote(settings.mailbox or "", safe="@")
        self._access: Optional[str] = None
        self._expires = 0.0
        self.folder = self._folder_path(settings.folder)
        self.processed = self._child(self.folder, settings.processed_folder, create=True)
        self.rejected = self._child(self.folder, settings.rejected_folder, create=True)

    def _token(self) -> str:
        if self._access and time.time() < self._expires - 60:
            return self._access
        form = urllib.parse.urlencode({
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        }).encode()
        request = urllib.request.Request(GRAPH_LOGIN.format(tenant=self.settings.tenant_id), data=form, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read() or b"{}").get("error_description", "") if exc.fp else ""
            first = detail.splitlines()[0] if detail else ""
            raise RuntimeError(f"Microsoft sign-in for the app failed (HTTP {exc.code}): {first}") from None
        self._access = answer["access_token"]
        self._expires = time.time() + float(answer.get("expires_in", 3600))
        return self._access

    def _call(self, method: str, path: str, body: Optional[dict] = None, raw: bool = False) -> Any:
        url = path if path.startswith("https://") else f"{GRAPH_URL}/users/{self.mailbox}{path}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(GRAPH_TRIES):
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("Authorization", f"Bearer {self._token()}")
            if data is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    content = response.read()
                return content if raw else (json.loads(content) if content else {})
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503, 504) and attempt < GRAPH_TRIES - 1:
                    time.sleep(float(exc.headers.get("Retry-After") or 2 ** attempt))
                    continue
                if exc.code == 404:
                    raise LookupError(url) from None
                detail = json.loads(exc.read() or b"{}").get("error", {}).get("message", "") if exc.fp else ""
                raise RuntimeError(f"Microsoft Graph {method} {url} failed (HTTP {exc.code}): {detail}") from None
        raise RuntimeError(f"Microsoft Graph {method} {url} kept failing")

    def _pages(self, path: str) -> Iterator[dict]:
        page = self._call("GET", path)
        while True:
            yield from page.get("value", [])
            if not page.get("@odata.nextLink"):
                return
            page = self._call("GET", page["@odata.nextLink"])

    def _child(self, parent: Optional[str], name: str, create: bool = False) -> str:
        """The ID of folder ``name`` under ``parent`` (the mailbox's top when None), matched in any case."""
        base = f"/mailFolders/{parent}/childFolders" if parent else "/mailFolders"
        folders = list(self._pages(f"{base}?$select=id,displayName&$top=100&includeHiddenFolders=true"))
        found = next((f["id"] for f in folders if (f.get("displayName") or "").lower() == name.lower()), None)
        if found:
            return found
        if create:
            return self._call("POST", base, {"displayName": name})["id"]
        names = ", ".join(sorted(f.get("displayName") or "" for f in folders))
        raise RuntimeError(f"the folder {name!r} is not in {self.settings.mailbox}; it has: {names}")

    def _folder_path(self, path: str) -> str:
        """``Inbox`` or ``Inbox/CVM-AER``: the first part may be a well-known name in any language."""
        parts = [part for part in re.split(r"[\\/]", path or "Inbox") if part]
        try:
            folder = self._call("GET", f"/mailFolders/{urllib.parse.quote(parts[0].lower())}?$select=id")["id"]
        except LookupError:
            folder = self._child(None, parts[0])
        for part in parts[1:]:
            folder = self._child(folder, part)
        return folder

    def messages(self) -> Iterator[Tuple[Mail, Any]]:
        fields = "id,subject,from,receivedDateTime,internetMessageId"
        # Taken up front: moving a message would shift the pages still to come.
        listed = list(self._pages(f"/mailFolders/{self.folder}/messages?$select={fields}&$top=50"))
        for item in listed:
            if not self.settings.wants(item.get("subject")):
                continue  # other e-mails' attachments are never opened
            try:
                mail = self._mail(item)
            except Exception as exc:  # left where it is, to be tried again next sweep
                LOGGER.error("the e-mail %r could not be read: %s: %s", item.get("subject"), type(exc).__name__, exc)
                continue
            yield mail, item["id"]

    def _mail(self, item: dict) -> Mail:
        attachments: List[Tuple[str, bytes]] = []
        for attachment in self._pages(f"/messages/{item['id']}/attachments?$select=id,name"):
            kind = attachment.get("@odata.type")
            content = f"/messages/{item['id']}/attachments/{attachment['id']}/$value"
            if kind == GRAPH_FILE:
                attachments.append((attachment.get("name") or "", self._call("GET", content, raw=True)))
            elif kind == GRAPH_ITEM:
                inner = parse_rfc822(self._call("GET", content, raw=True), attachment["id"])
                attachments.extend(inner.attachments)
            else:
                LOGGER.warning("left out %s from %r: a %s is a link, not a file",
                               attachment.get("name"), item.get("subject"), kind)
        sender = ((item.get("from") or {}).get("emailAddress") or {}).get("address", "")
        try:
            received = datetime.fromisoformat((item.get("receivedDateTime") or "").replace("Z", "+00:00"))
            received = received.astimezone().replace(tzinfo=None)
        except ValueError:
            received = datetime.now()
        return Mail(
            key=(item.get("internetMessageId") or "").strip() or f"graph:{item['id']}",
            subject=str(item.get("subject") or ""),
            sender=sender,
            received=received,
            attachments=attachments,
        )

    def file(self, message_id: str, ok: bool) -> None:
        self._call("PATCH", f"/messages/{message_id}", {"isRead": True})
        self._call("POST", f"/messages/{message_id}/move", {"destinationId": self.processed if ok else self.rejected})

    def close(self) -> None:
        pass


SOURCE_CLASSES = {
    SOURCE_GRAPH: GraphSource,
    SOURCE_OUTLOOK: OutlookSource,
    SOURCE_IMAP: ImapSource,
    SOURCE_FOLDER: FolderSource,
}


def open_source(settings: IntakeSettings):
    return SOURCE_CLASSES[settings.source](settings)


def _file(source, handle: Any, ok: bool, mail: Mail) -> None:
    try:
        source.file(handle, ok)
    except Exception as exc:  # the ledger still keeps it from being taken twice
        LOGGER.warning("could not move the e-mail %r: %s: %s", mail.subject, type(exc).__name__, exc)


def _tell_rejected(config: ServiceConfig, mail: Mail, reason: str) -> None:
    subject = f"{config.email.subject_prefix} e-mail \"{mail.subject}\" was not taken in"
    body = (
        f"The e-mail below was not turned into a case: {reason}.\n"
        f"It was moved to the mail folder {config.intake.rejected_folder}.\n\n"
        f"From: {mail.sender}\n"
        f"Subject: {mail.subject}\n"
        f"Received: {mail.received.isoformat(timespec='seconds')}\n"
        f"Attachments: {', '.join(name for name, _ in mail.attachments) or 'none'}\n"
    )
    send_note(config, subject, body, f"e-mail {_slug(mail.subject) or mail.key}")


def collect(config: ServiceConfig, source=None) -> List[Collected]:
    """Turn every new case e-mail into a ZIP in inbound, then file the e-mail away."""
    settings = config.intake
    source = source or open_source(settings)
    ledger = Ledger(settings.ledger)
    done: List[Collected] = []
    try:
        for mail, handle in source.messages():
            if not settings.wants(mail.subject):
                continue  # not a case e-mail: left where it is, untouched
            seen = ledger.get(mail.key)
            if seen is not None:
                LOGGER.info("e-mail %r was already taken in (%s); filing it again", mail.subject, seen or "done")
                _file(source, handle, seen != "rejected", mail)
                continue
            try:
                archive = build_zip(mail, config.inbound, settings.ignore)
            except Rejected as exc:
                LOGGER.error(
                    "e-mail %r from %s not taken in: %s (attachments: %s)",
                    mail.subject,
                    mail.sender,
                    exc,
                    ", ".join(name or "?" for name, _ in mail.attachments) or "none",
                )
                ledger.add(mail.key, "rejected")
                _file(source, handle, False, mail)
                _tell_rejected(config, mail, str(exc))
                done.append(Collected(mail.subject, mail.sender, error=str(exc)))
                continue
            except Exception as exc:  # left where it is, to be tried again next sweep
                LOGGER.error("e-mail %r could not be read: %s: %s", mail.subject, type(exc).__name__, exc)
                done.append(Collected(mail.subject, mail.sender, error=f"{type(exc).__name__}: {exc}"))
                continue
            ledger.add(mail.key, os.path.basename(archive))
            LOGGER.info("e-mail %r from %s -> %s", mail.subject, mail.sender, archive)
            _file(source, handle, True, mail)
            done.append(Collected(mail.subject, mail.sender, archive=archive))
    finally:
        source.close()
    return done

"""E-mail intake: one case e-mail -> one ZIP in inbound, the e-mail filed away."""

import io
import os
import sys
import types
import zipfile
from datetime import datetime
from email.message import EmailMessage

import pytest
from test_xfa_1932a import _xfa_pdf

from cvm_aer import mailin, service


@pytest.fixture(scope="module")
def form_pdf(tmp_path_factory) -> bytes:
    path = _xfa_pdf(str(tmp_path_factory.mktemp("forms") / "case.pdf"))
    return open(path, "rb").read()


def _zip_bytes(files) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _eml(directory, name, subject, attachments, message_id=None) -> str:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "vet@example.com"
    message["To"] = "cvm@example.gov"
    message["Date"] = "Mon, 26 Oct 2026 13:31:43 -0400"
    message["Message-ID"] = message_id or f"<{name}@example.com>"
    message.set_content("case attached")
    for filename, payload in attachments:
        maintype, subtype = ("application", "zip") if filename.endswith(".zip") else ("application", "octet-stream")
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(bytes(message))
    return path


def _config(tmp_path, **email) -> service.ServiceConfig:
    config = service.ServiceConfig(
        inbound=str(tmp_path / "inbound"),
        outbound=str(tmp_path / "outbound"),
        processed=str(tmp_path / "processed"),
        error=str(tmp_path / "error"),
        settle_seconds=0,
        intake=service.IntakeSettings(
            source=service.SOURCE_FOLDER,
            eml_dir=str(tmp_path / "mail"),
            ledger=str(tmp_path / "ledger.txt"),
        ),
        email=service.EmailSettings(**email),
    )
    for folder in config.folders:
        os.makedirs(folder, exist_ok=True)
    return config


def _members(path):
    with zipfile.ZipFile(path) as archive:
        return sorted(archive.namelist())


def test_several_attachments_are_zipped_into_one_case(tmp_path):
    config = _config(tmp_path)
    _eml(config.intake.eml_dir, "a.eml", "N141562 Jordan Librela",
         [("form.pdf", b"%PDF-1"), ("lab.pdf", b"%PDF-2"), ("image001.png", b"logo")])

    done = mailin.collect(config)

    assert [entry.ok for entry in done] == [True]
    # named by the received time in this machine's zone
    assert os.path.basename(done[0].archive).endswith("-N141562-Jordan-Librela.zip")
    assert _members(done[0].archive) == ["form.pdf", "lab.pdf"]
    assert os.listdir(os.path.join(config.intake.eml_dir, "CVM-AER Processed")) == ["a.eml"]
    assert not [name for name in os.listdir(config.inbound) if name.endswith(".part")]


def test_a_single_zip_is_taken_as_it_is(tmp_path):
    config = _config(tmp_path)
    payload = _zip_bytes([("form.pdf", b"%PDF-1"), ("att 1.pdf", b"%PDF-2")])
    _eml(config.intake.eml_dir, "a.eml", "case", [("case.zip", payload)])

    (entry,) = mailin.collect(config)

    assert open(entry.archive, "rb").read() == payload


def test_a_zip_with_loose_files_is_merged_into_one_case(tmp_path):
    config = _config(tmp_path)
    _eml(config.intake.eml_dir, "a.eml", "case",
         [("case.zip", _zip_bytes([("form.pdf", b"1")])), ("extra.pdf", b"2")])

    (entry,) = mailin.collect(config)

    assert _members(entry.archive) == ["extra.pdf", "form.pdf"]


@pytest.mark.parametrize("attachments, reason", [
    ([], "no attachments"),
    ([("image001.png", b"logo")], "no attachments"),
    ([("a.zip", _zip_bytes([("x.pdf", b"1")])), ("b.zip", _zip_bytes([("y.pdf", b"1")]))], "2 ZIP files"),
    ([("a.zip", b"not a zip")], "not a readable ZIP"),
])
def test_an_email_without_one_case_is_rejected_with_a_note(tmp_path, attachments, reason):
    config = _config(tmp_path, transport=service.TRANSPORT_EML, recipients=["cvm@example.gov"],
                     drop_dir=str(tmp_path / "notes"))
    _eml(config.intake.eml_dir, "a.eml", "no case here", attachments)

    (entry,) = mailin.collect(config)

    assert not entry.ok and reason in entry.error
    assert os.listdir(config.inbound) == []
    assert os.listdir(os.path.join(config.intake.eml_dir, "CVM-AER Rejected")) == ["a.eml"]
    (note,) = os.listdir(tmp_path / "notes")
    text = open(tmp_path / "notes" / note, encoding="utf-8").read()
    assert "no case here" in text and "vet@example.com" in text


def test_an_email_is_never_taken_twice(tmp_path):
    config = _config(tmp_path)
    path = _eml(config.intake.eml_dir, "a.eml", "case", [("form.pdf", b"1")])
    copy = open(path, "rb").read()
    mailin.collect(config)
    # the same message turns up again - a failed move, or someone copied it back
    with open(path, "wb") as handle:
        handle.write(copy)

    assert mailin.collect(config) == []
    assert len(os.listdir(config.inbound)) == 1
    assert sorted(os.listdir(os.path.join(config.intake.eml_dir, "CVM-AER Processed"))) == ["a-2.eml", "a.eml"]
    assert "<a.eml@example.com>" in open(config.intake.ledger, encoding="utf-8").read()


def test_the_sweep_reads_the_mailbox_then_converts_the_case(tmp_path, form_pdf):
    config = _config(tmp_path)
    _eml(config.intake.eml_dir, "a.eml", "N141562", [("case.pdf", form_pdf), ("att 1.txt", b"notes")])

    (processed,) = service.run_once(config)

    assert processed.ok, processed.error
    assert os.listdir(config.outbound) == [os.path.basename(processed.xml_path)]
    assert os.listdir(config.processed) == [os.path.basename(processed.archive)]


def test_a_mailbox_that_is_down_does_not_stop_the_sweep(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(mailin, "open_source", lambda settings: (_ for _ in ()).throw(OSError("offline")))
    assert service.run_once(config) == []


def test_intake_settings_are_read_from_the_ini(tmp_path):
    path = tmp_path / "service.ini"
    path.write_text(
        "[folders]\ninbound = /srv/x/inbound\noutbound = o\nprocessed = p\nerror = e\n"
        "[intake]\nsource = outlook\nmailbox = CVM Shared\nfolder = Inbox/CVM\nignore = *.png\n",
        encoding="utf-8",
    )
    intake = service.load_config(str(path)).intake
    assert (intake.source, intake.mailbox, intake.folder, intake.ignore) == ("outlook", "CVM Shared", "Inbox/CVM", ["*.png"])
    assert intake.ledger == os.path.join(os.path.abspath("/srv/x"), "cvm-aer-intake-ledger.txt")
    assert intake.processed_folder == "CVM-AER Processed"


@pytest.mark.parametrize("section, complaint", [
    ("source = pop3\n", "source"),
    ("source = imap\n", "host, username"),
    ("source = folder\n", "eml_dir"),
])
def test_incomplete_intake_settings_are_refused(tmp_path, section, complaint):
    path = tmp_path / "service.ini"
    path.write_text("[folders]\ninbound = i\noutbound = o\nprocessed = p\nerror = e\n[intake]\n" + section,
                    encoding="utf-8")
    with pytest.raises(service.ConfigError, match=complaint):
        service.load_config(str(path))


def test_no_intake_section_means_no_intake(tmp_path):
    path = tmp_path / "service.ini"
    path.write_text("[folders]\ninbound = i\noutbound = o\nprocessed = p\nerror = e\n", encoding="utf-8")
    assert not service.load_config(str(path)).intake.enabled


class _Collection:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, key):
        if isinstance(key, int):
            return self.items[key - 1]
        for item in self.items:
            if item.Name == key:
                return item
        raise LookupError(key)


class _Folder:
    def __init__(self, name, items=()):
        self.Name = name
        self.Items = _Collection(list(items))
        self.Folders = _Collection([])
        self.Folders.Add = self._add

    def _add(self, name):
        child = _Folder(name)
        self.Folders.items.append(child)
        return child


class _Attachment:
    def __init__(self, name, payload):
        self.FileName, self.payload = name, payload

    def SaveAsFile(self, path):
        with open(path, "wb") as handle:
            handle.write(self.payload)


class _MailItem:
    Class = 43

    def __init__(self, inbox, subject, attachments):
        self.inbox, self.Subject = inbox, subject
        self.SenderEmailAddress = "vet@example.com"
        self.ReceivedTime = datetime(2026, 10, 26, 13, 31, 43)
        self.EntryID = "0001"
        self.UnRead = True
        self.Attachments = _Collection([_Attachment(*pair) for pair in attachments])
        self.PropertyAccessor = types.SimpleNamespace(GetProperty=lambda tag: "<m1@example.com>")

    def Move(self, folder):
        self.inbox.Items.items.remove(self)
        folder.Items.items.append(self)


def _fake_outlook(monkeypatch, inbox):
    namespace = types.SimpleNamespace(GetDefaultFolder=lambda kind: inbox, Folders=_Collection([]))
    application = types.SimpleNamespace(GetNamespace=lambda name: namespace)
    client = types.ModuleType("win32com.client")
    client.Dispatch = lambda name: application
    package = types.ModuleType("win32com")
    package.client = client
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", client)


def test_outlook_mail_is_taken_and_moved_to_the_processed_subfolder(monkeypatch, tmp_path):
    inbox = _Folder("Inbox")
    inbox.Items.items.append(_MailItem(inbox, "N141562 Jordan", [("form.pdf", b"1"), ("att.pdf", b"2")]))
    _fake_outlook(monkeypatch, inbox)
    config = _config(tmp_path)
    config.intake = service.IntakeSettings(source=service.SOURCE_OUTLOOK, ledger=str(tmp_path / "ledger.txt"))

    (entry,) = mailin.collect(config)

    assert os.path.basename(entry.archive) == "20261026-133143-N141562-Jordan.zip"
    assert _members(entry.archive) == ["att.pdf", "form.pdf"]
    processed = inbox.Folders.Item("CVM-AER Processed")
    assert inbox.Items.Count == 0 and processed.Items.Count == 1
    assert processed.Items.Item(1).UnRead is False


class _FakeImap:
    capabilities = ("IMAP4REV1", "MOVE")

    def __init__(self, raw_messages):
        self.raw = dict(raw_messages)
        self.moved = []

    def login(self, user, password):
        self.user = user

    def create(self, name):
        return "OK", [b""]

    def select(self, name):
        return "OK", [str(len(self.raw)).encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(self.raw)]
        if command == "FETCH":
            return "OK", [(b"1 (BODY[] {n}", self.raw[args[0]]), b")"]
        if command == "MOVE":
            self.moved.append((args[0], args[1]))
            del self.raw[args[0]]
            return "OK", [b""]
        raise AssertionError(command)

    def logout(self):
        pass


def test_imap_mail_is_taken_and_moved_on_the_server(monkeypatch, tmp_path):
    raw = open(_eml(tmp_path / "src", "a.eml", "case 7", [("form.pdf", b"1")]), "rb").read()
    server = _FakeImap({b"7": raw})
    monkeypatch.setattr(mailin.imaplib, "IMAP4_SSL", lambda host, port: server)
    config = _config(tmp_path)
    config.intake = service.IntakeSettings(source=service.SOURCE_IMAP, host="imap.example.gov", username="u",
                                           ledger=str(tmp_path / "ledger.txt"))

    (entry,) = mailin.collect(config)

    assert entry.ok and _members(entry.archive) == ["form.pdf"]
    assert server.moved == [(b"7", '"CVM-AER Processed"')]


def test_the_intake_command_reads_the_mailbox_once(tmp_path, monkeypatch):
    from cvm_aer.cli import main

    _eml(tmp_path / "mail", "a.eml", "case", [("form.pdf", b"1")])
    (tmp_path / "cvm-aer-service.ini").write_text(
        f"[folders]\ninbound = {tmp_path / 'inbound'}\noutbound = o\nprocessed = p\nerror = e\n"
        f"[intake]\nsource = folder\neml_dir = {tmp_path / 'mail'}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["intake"]) == 0
    assert len(os.listdir(tmp_path / "inbound")) == 1

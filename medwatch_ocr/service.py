"""A folder service: ZIPs in, XML out.

One ZIP is one case: the form PDF inside it becomes the message and every other
file in it becomes an attachment the message embeds.  The service watches the
inbound folder, writes the generated XML to the outbound folder and moves the
ZIP to the processed folder, or to the error folder with an e-mail when the case
cannot be converted.  Every folder, and the format, poll interval and mail
server, come from a configuration file - see ``medwatch-service.ini.sample``.

    python -m medwatch_ocr.cli service --config medwatch-service.ini
    python -m medwatch_ocr.cli service --config medwatch-service.ini --once
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil
import smtplib
import tempfile
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Dict, List, Optional, Sequence, Tuple

from .delivery import unique_name
from .models import CENTER_CDER, CENTER_CDRH, CENTER_CVM
from .pipeline import ENGINE_TEXT_LAYER, LAYOUT_AUTO, convert_pdf

LOGGER = logging.getLogger("medwatch_ocr.service")

SECTION_FOLDERS = "folders"
SECTION_CONVERSION = "conversion"
SECTION_SERVICE = "service"
SECTION_EMAIL = "email"

# How the note leaves the machine.  A server that may not talk to a mail relay -
# the usual case on a locked-down network - can hand the note to the Outlook the
# operator is already signed in to, or write it as a .eml file to be picked up.
TRANSPORT_SMTP = "smtp"
TRANSPORT_OUTLOOK = "outlook"
TRANSPORT_EML = "eml"
TRANSPORTS = (TRANSPORT_SMTP, TRANSPORT_OUTLOOK, TRANSPORT_EML)

# A ZIP still being copied into the inbound folder must not be picked up, so its
# size has to stay unchanged for this long before it is taken.
SETTLE_SECONDS = 5.0
POLL_SECONDS = 30.0


class ConfigError(RuntimeError):
    """The configuration file is missing something the service needs."""


@dataclass
class EmailSettings:
    """Where to send the note about a case that failed."""

    host: Optional[str] = None
    port: int = 25
    sender: Optional[str] = None
    recipients: List[str] = field(default_factory=list)
    username: Optional[str] = None
    password: Optional[str] = None
    use_tls: bool = False
    subject_prefix: str = "[medwatch-ocr]"
    # Send the case ZIP itself with the note, so the reader has the input to hand.
    attach_zip: bool = True
    # Mail servers refuse large messages, so a ZIP over this is only named.
    max_attachment_mb: float = 10.0
    transport: str = TRANSPORT_SMTP
    # Where the ``eml`` transport leaves the note; the error folder if unset.
    drop_dir: Optional[str] = None

    @property
    def configured(self) -> bool:
        """True once enough is set for this transport to deliver the note.

        Outlook knows the account itself and a dropped file needs no server, so
        only ``smtp`` asks for a host and a sender.
        """
        if not self.recipients:
            return False
        if self.transport == TRANSPORT_SMTP:
            return bool(self.host and self.sender)
        return True


@dataclass
class ServiceConfig:
    inbound: str
    outbound: str
    processed: str
    error: str
    output_format: Optional[str] = None
    # The format per center, for an inbound folder holding more than one kind of
    # case: a CDRH ZIP and a CVM ZIP cannot become the same message.
    formats_by_center: Dict[str, str] = field(default_factory=dict)
    center: Optional[str] = None
    stage: Optional[str] = None
    # The reporting facility's FDA registration number, which an eMDR report
    # number is built from.
    uf_fei: Optional[str] = None
    layout: str = LAYOUT_AUTO
    engine: str = ENGINE_TEXT_LAYER
    dpi: int = 200
    lang: str = "en"
    poll_seconds: float = POLL_SECONDS
    settle_seconds: float = SETTLE_SECONDS
    email: EmailSettings = field(default_factory=EmailSettings)

    @property
    def folders(self) -> Tuple[str, str, str, str]:
        return self.inbound, self.outbound, self.processed, self.error


def load_config(path: str) -> ServiceConfig:
    """Read the service's configuration file."""
    if not os.path.exists(path):
        raise ConfigError(f"no configuration file at {path}")
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section(SECTION_FOLDERS):
        raise ConfigError(f"{path} has no [{SECTION_FOLDERS}] section")
    folders = parser[SECTION_FOLDERS]
    missing = [name for name in ("inbound", "outbound", "processed", "error") if not folders.get(name)]
    if missing:
        raise ConfigError(f"[{SECTION_FOLDERS}] in {path} needs: {', '.join(missing)}")

    conversion = parser[SECTION_CONVERSION] if parser.has_section(SECTION_CONVERSION) else {}
    wanted = str(conversion.get("format", "")).strip()
    if wanted.lower() in ("", "auto"):
        wanted = ""
    by_center = {
        center: str(conversion.get(key, "")).strip()
        for center, key in ((CENTER_CDER, "format_cder"), (CENTER_CDRH, "format_cdrh"), (CENTER_CVM, "format_cvm"))
        if str(conversion.get(key, "")).strip()
    }

    service = parser[SECTION_SERVICE] if parser.has_section(SECTION_SERVICE) else {}
    mail = parser[SECTION_EMAIL] if parser.has_section(SECTION_EMAIL) else {}
    recipients = [address.strip() for address in str(mail.get("recipients", "")).replace(";", ",").split(",") if address.strip()]

    return ServiceConfig(
        inbound=os.path.expanduser(folders["inbound"]),
        outbound=os.path.expanduser(folders["outbound"]),
        processed=os.path.expanduser(folders["processed"]),
        error=os.path.expanduser(folders["error"]),
        output_format=wanted or None,
        formats_by_center=by_center,
        center=conversion.get("center") or None,
        stage=conversion.get("stage") or None,
        uf_fei=conversion.get("uf_fei") or None,
        layout=conversion.get("layout") or LAYOUT_AUTO,
        engine=conversion.get("engine") or ENGINE_TEXT_LAYER,
        dpi=int(conversion.get("dpi", 200) or 200),
        lang=conversion.get("lang") or "en",
        poll_seconds=float(service.get("poll_seconds", POLL_SECONDS) or POLL_SECONDS),
        settle_seconds=float(service.get("settle_seconds", SETTLE_SECONDS) or SETTLE_SECONDS),
        email=EmailSettings(
            host=mail.get("host") or None,
            port=int(mail.get("port", 25) or 25),
            sender=mail.get("sender") or None,
            recipients=recipients,
            username=mail.get("username") or None,
            password=mail.get("password") or None,
            use_tls=str(mail.get("use_tls", "false")).strip().lower() in ("1", "true", "yes", "on"),
            attach_zip=str(mail.get("attach_zip", "true")).strip().lower() in ("1", "true", "yes", "on"),
            max_attachment_mb=float(mail.get("max_attachment_mb", 10) or 10),
            subject_prefix=mail.get("subject_prefix") or "[medwatch-ocr]",
            transport=_transport(mail.get("transport"), path),
            drop_dir=os.path.expanduser(mail.get("drop_dir", "") or "") or None,
        ),
    )


def _transport(named: Optional[str], path: str) -> str:
    """The transport the file asks for, defaulting to a mail server."""
    wanted = (named or TRANSPORT_SMTP).strip().lower()
    if wanted not in TRANSPORTS:
        raise ConfigError(f"[{SECTION_EMAIL}] transport in {path} must be one of: {', '.join(TRANSPORTS)}")
    return wanted


def _settled(path: str, settle_seconds: float) -> bool:
    """True once the file has stopped growing, i.e. the copy into inbound is done."""
    try:
        first = os.path.getsize(path)
    except OSError:
        return False
    if settle_seconds <= 0:
        return True
    time.sleep(settle_seconds)
    try:
        return os.path.getsize(path) == first
    except OSError:
        return False


def pending_zips(inbound: str) -> List[str]:
    """The ZIPs waiting in the inbound folder, oldest first."""
    if not os.path.isdir(inbound):
        return []
    paths = [
        os.path.join(inbound, name)
        for name in os.listdir(inbound)
        if name.lower().endswith(".zip") and os.path.isfile(os.path.join(inbound, name))
    ]
    return sorted(paths, key=lambda path: (os.path.getmtime(path), path))


def _extract(archive_path: str, directory: str) -> List[str]:
    """Unpack the case's files, flattened, and return them in the ZIP's order."""
    paths: List[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = os.path.basename(info.filename)
            if not name or name.startswith("."):
                continue
            path = os.path.join(directory, name)
            stem, extension = os.path.splitext(name)
            index = 2
            while os.path.exists(path):
                path = os.path.join(directory, f"{stem}-{index}{extension}")
                index += 1
            with archive.open(info) as source, open(path, "wb") as target:
                shutil.copyfileobj(source, target)
            paths.append(path)
    return paths


def _is_form(path: str) -> bool:
    from .form_extract import is_official_form
    from .vet_extract import is_1932_form
    from .xfa_1932a import is_1932a_form

    try:
        return is_1932a_form(path) or is_official_form(path) or is_1932_form(path)
    except Exception:  # a damaged or non-PDF file is simply not the form
        return False


def split_case(paths: Sequence[str]) -> Tuple[str, List[str]]:
    """The report PDF and the files that go with it as attachments.

    The report is the one file recognised as an FDA form; failing that - a
    scanned copy of a revision the recognisers do not know, say - it is the
    first PDF in the ZIP, which is the order a submitter builds the case in.
    """
    pdfs = [path for path in paths if path.lower().endswith(".pdf")]
    if not pdfs:
        raise ConfigError("the ZIP holds no PDF to read the report from")
    form = next((path for path in pdfs if _is_form(path)), pdfs[0])
    return form, [path for path in paths if path != form]


def _move(path: str, directory: str) -> str:
    """Move the ZIP into ``directory``, keeping any file already there."""
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, os.path.basename(path))
    stem, extension = os.path.splitext(os.path.basename(path))
    index = 2
    while os.path.exists(target):
        target = os.path.join(directory, f"{stem}-{index}{extension}")
        index += 1
    shutil.move(path, target)
    return target


def _attach_archive(message: EmailMessage, path: str) -> Optional[str]:
    """Put the case ZIP in the message."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        LOGGER.warning("could not attach %s: %s", name, exc)
        return None
    message.add_attachment(payload, maintype="application", subtype="zip", filename=name)
    return name


def _mailable(path: str, limit_mb: float) -> Optional[str]:
    """The ZIP's path if it can travel with the note, else None."""
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        LOGGER.warning("could not attach %s: %s", name, exc)
        return None
    if limit_mb and size > limit_mb * 1024 * 1024:
        LOGGER.warning("%s is %.1f MB, over the %.1f MB mail limit, so it was not attached", name, size / 1048576, limit_mb)
        return None
    return path


def _send_smtp(settings: EmailSettings, message: EmailMessage) -> None:
    """Hand the note to the mail server named in the configuration."""
    with smtplib.SMTP(settings.host, settings.port, timeout=30) as server:
        if settings.use_tls:
            server.starttls()
        if settings.username:
            server.login(settings.username, settings.password or "")
        server.send_message(message)


def _send_outlook(settings: EmailSettings, subject: str, body: str, attachment: Optional[str]) -> None:
    """Send through the Outlook this Windows account is already signed in to.

    Nothing is configured here beyond the recipients: Outlook holds the account,
    the server and the credentials, so a machine that may not reach a mail relay
    itself can still get the note out.
    """
    import win32com.client  # pywin32, and Windows: only this transport needs it

    item = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
    item.Subject = subject
    item.Body = body
    item.To = "; ".join(settings.recipients)
    if attachment:
        item.Attachments.Add(os.path.abspath(attachment))
    item.Send()


def _write_eml(config: ServiceConfig, message: EmailMessage, archive: str) -> str:
    """Leave the note, attachment and all, as a .eml file to be picked up."""
    directory = config.email.drop_dir or os.path.join(config.error, "notices")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, unique_name(archive, extension=".eml"))
    with open(path, "wb") as handle:
        handle.write(bytes(message))
    return path


def send_failure_email(config: ServiceConfig, archive: str, reason: str, attachment: Optional[str] = None) -> bool:
    """Tell the addresses in the configuration about a case that failed.

    ``attachment`` is the ZIP as it now sits in the error folder; it travels with
    the note unless ``[email] attach_zip`` says otherwise.  ``[email] transport``
    chooses how the note leaves: a mail server, the local Outlook, or a .eml file
    written for something else to send.
    """
    settings = config.email
    name = os.path.basename(archive)
    if not settings.configured:
        LOGGER.warning("no [email] settings, so no note was sent about %s", name)
        return False
    subject = f"{settings.subject_prefix} {name} could not be processed"
    body = (
        f"{name} was moved to the error folder {config.error}.\n\n"
        f"Inbound folder: {config.inbound}\n"
        f"Time: {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"{reason}\n"
    )
    zip_path = _mailable(attachment or archive, settings.max_attachment_mb) if settings.attach_zip else None
    try:
        if settings.transport == TRANSPORT_OUTLOOK:
            _send_outlook(settings, subject, body, zip_path)
        else:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = settings.sender or "medwatch-ocr@localhost"
            message["To"] = ", ".join(settings.recipients)
            message.set_content(body)
            if zip_path:
                _attach_archive(message, zip_path)
            if settings.transport == TRANSPORT_EML:
                LOGGER.info("the note about %s was written to %s", name, _write_eml(config, message, archive))
            else:
                _send_smtp(settings, message)
    except Exception as exc:  # a mail failure must not lose the case
        LOGGER.error("could not send the note about %s: %s: %s", name, type(exc).__name__, exc)
        return False
    return True


@dataclass
class Processed:
    """What became of one ZIP."""

    archive: str
    xml_path: Optional[str] = None
    output_format: Optional[str] = None
    moved_to: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def process_zip(archive: str, config: ServiceConfig) -> Processed:
    """Convert one case ZIP, then file the ZIP under processed or error."""
    LOGGER.info("processing %s", os.path.basename(archive))
    try:
        with tempfile.TemporaryDirectory(prefix="medwatch-case-") as workspace:
            paths = _extract(archive, workspace)
            form, attachments = split_case(paths)
            result = convert_pdf(
                form,
                center=config.center,
                stage=config.stage,
                output_format=config.output_format,
                formats_by_center=config.formats_by_center,
                engine=config.engine,
                dpi=config.dpi,
                lang=config.lang,
                layout=config.layout,
                attachments=attachments,
                uf_fei=config.uf_fei,
            )
            os.makedirs(config.outbound, exist_ok=True)
            xml_path = os.path.join(config.outbound, unique_name(archive))
            with open(xml_path, "w", encoding="utf-8") as handle:
                handle.write(result.xml)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        LOGGER.error("%s failed: %s: %s", os.path.basename(archive), type(exc).__name__, exc)
        moved = _move(archive, config.error)
        send_failure_email(config, archive, reason, attachment=moved)
        return Processed(archive=archive, moved_to=moved, error=f"{type(exc).__name__}: {exc}")

    moved = _move(archive, config.processed)
    LOGGER.info("%s -> %s (%s)", os.path.basename(archive), xml_path, result.output_format)
    return Processed(archive=archive, xml_path=xml_path, output_format=result.output_format, moved_to=moved)


def run_once(config: ServiceConfig) -> List[Processed]:
    """Process everything settled in the inbound folder and return."""
    for folder in config.folders:
        os.makedirs(folder, exist_ok=True)
    done: List[Processed] = []
    for archive in pending_zips(config.inbound):
        if not _settled(archive, config.settle_seconds):
            LOGGER.info("%s is still being written, leaving it", os.path.basename(archive))
            continue
        done.append(process_zip(archive, config))
    return done


def run_forever(config: ServiceConfig) -> int:
    """Watch the inbound folder until the service is stopped."""
    LOGGER.info(
        "watching %s every %.0fs; XML -> %s, ZIPs -> %s / %s",
        config.inbound,
        config.poll_seconds,
        config.outbound,
        config.processed,
        config.error,
    )
    while True:
        try:
            run_once(config)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # a folder that went away must not stop the service
            LOGGER.error("the sweep of %s failed: %s", config.inbound, exc)
        time.sleep(config.poll_seconds)

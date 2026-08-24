"""The folder service: a case ZIP in, one XML out, the ZIP filed away."""

import os
import zipfile

import pytest

from medwatch_ocr import service
from medwatch_ocr.models import CENTER_CDRH, CENTER_CVM
from medwatch_ocr.pipeline import FORMAT_EMDR_HL7, FORMAT_VICH_HL7


def _config(tmp_path, **overrides) -> service.ServiceConfig:
    settings = dict(
        inbound=str(tmp_path / "inbound"),
        outbound=str(tmp_path / "outbound"),
        processed=str(tmp_path / "processed"),
        error=str(tmp_path / "error"),
        settle_seconds=0,
    )
    settings.update(overrides)
    config = service.ServiceConfig(**settings)
    for folder in config.folders:
        os.makedirs(folder, exist_ok=True)
    return config


def _zip(path, files) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files:
            archive.writestr(name, payload)
    return str(path)


@pytest.fixture(scope="module")
def form_pdf(tmp_path_factory) -> bytes:
    """A genuine, filled FDA 3500A the service can read."""
    from medwatch_ocr.official_form import generate_official_samples

    samples = generate_official_samples(str(tmp_path_factory.mktemp("forms")))
    return open(samples["cdrh_postmarket"], "rb").read()


def test_the_ini_file_states_every_folder_and_the_mail_server(tmp_path):
    path = tmp_path / "service.ini"
    path.write_text(
        "[folders]\ninbound = in\noutbound = out\nprocessed = done\nerror = bad\n"
        "[conversion]\nformat = emdr-hl7\n"
        "[service]\npoll_seconds = 5\nsettle_seconds = 1\n"
        "[email]\nhost = smtp.example.org\nsender = a@example.org\nrecipients = b@example.org; c@example.org\n",
        encoding="utf-8",
    )
    config = service.load_config(str(path))
    assert config.folders == ("in", "out", "done", "bad")
    assert config.output_format == FORMAT_EMDR_HL7
    assert (config.poll_seconds, config.settle_seconds) == (5, 1)
    assert config.email.recipients == ["b@example.org", "c@example.org"]
    assert config.email.configured


def test_a_configuration_missing_a_folder_says_which(tmp_path):
    path = tmp_path / "service.ini"
    path.write_text("[folders]\ninbound = in\noutbound = out\n", encoding="utf-8")
    with pytest.raises(service.ConfigError, match="processed, error"):
        service.load_config(str(path))


def test_the_form_in_the_zip_is_the_report_and_the_rest_are_attachments(tmp_path, form_pdf):
    form = tmp_path / "case.pdf"
    form.write_bytes(form_pdf)
    first = tmp_path / "a.pdf"
    first.write_bytes(b"%PDF-1.4 first")
    note = tmp_path / "note.txt"
    note.write_text("supporting", encoding="utf-8")

    report, attachments = service.split_case([str(first), str(form), str(note)])
    assert report == str(form)
    assert attachments == [str(first), str(note)]


def test_a_zip_without_a_pdf_cannot_be_a_case(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("supporting", encoding="utf-8")
    with pytest.raises(service.ConfigError, match="no PDF"):
        service.split_case([str(note)])


def test_a_case_zip_becomes_one_xml_and_the_zip_moves_to_processed(tmp_path, form_pdf):
    config = _config(tmp_path, output_format=FORMAT_EMDR_HL7)
    archive = _zip(
        os.path.join(config.inbound, "case-1.zip"),
        [("case.pdf", form_pdf), ("attachment_1.pdf", b"%PDF-1.4 attachment")],
    )

    done = service.run_once(config)

    assert len(done) == 1 and done[0].ok, done
    xml = open(done[0].xml_path, encoding="utf-8").read()
    assert done[0].output_format == FORMAT_EMDR_HL7
    assert "PORR_IN040001UV01" in xml
    # both the form and the file that came with it are embedded
    assert xml.count("representation=\"B64\"") == 2
    assert "attachment_1.pdf" in xml
    assert not os.path.exists(archive)
    assert os.listdir(config.processed) == ["case-1.zip"]
    assert os.listdir(config.error) == []


def test_a_mixed_inbound_folder_writes_the_format_named_for_each_center(tmp_path, form_pdf):
    path = tmp_path / "service.ini"
    path.write_text(
        "[folders]\ninbound = in\noutbound = out\nprocessed = done\nerror = bad\n"
        "[conversion]\nformat = auto\nformat_cdrh = emdr-hl7\nformat_cvm = vich-hl7\n",
        encoding="utf-8",
    )
    parsed = service.load_config(str(path))
    assert parsed.output_format is None
    assert parsed.formats_by_center == {CENTER_CDRH: FORMAT_EMDR_HL7, CENTER_CVM: FORMAT_VICH_HL7}

    config = _config(tmp_path, formats_by_center=dict(parsed.formats_by_center))
    _zip(os.path.join(config.inbound, "device-case.zip"), [("case.pdf", form_pdf)])

    done = service.run_once(config)

    # the device report becomes CDRH's message, not the veterinary one asked for CVM
    assert len(done) == 1 and done[0].ok, done
    assert done[0].output_format == FORMAT_EMDR_HL7
    assert "PORR_IN040001UV01" in open(done[0].xml_path, encoding="utf-8").read()


def test_the_command_line_overrides_the_folders_and_formats_in_the_file(tmp_path):
    from medwatch_ocr.cli import _override_service_config, build_parser

    path = tmp_path / "service.ini"
    path.write_text(
        "[folders]\ninbound = in\noutbound = out\nprocessed = done\nerror = bad\n[conversion]\nformat = auto\n",
        encoding="utf-8",
    )
    config = service.load_config(str(path))
    args = build_parser().parse_args(
        ["service", "--config", str(path), "--format", "emdr-hl7", "--format-cvm", "vich-hl7", "--outbound", "elsewhere"]
    )
    _override_service_config(config, args)

    assert config.output_format == FORMAT_EMDR_HL7
    assert config.formats_by_center[CENTER_CVM] == FORMAT_VICH_HL7
    assert config.outbound == "elsewhere"
    assert config.inbound == "in"


def test_a_zip_that_cannot_be_read_moves_to_error_and_is_mailed(tmp_path, monkeypatch):
    config = _config(tmp_path, output_format=FORMAT_EMDR_HL7)
    _zip(os.path.join(config.inbound, "broken.zip"), [("note.txt", "no form here")])
    sent = []
    monkeypatch.setattr(service, "send_failure_email", lambda config, archive, reason: sent.append((archive, reason)))

    done = service.run_once(config)

    assert len(done) == 1 and not done[0].ok
    assert os.listdir(config.error) == ["broken.zip"]
    assert os.listdir(config.processed) == []
    assert os.listdir(config.outbound) == []
    assert len(sent) == 1 and "no PDF" in sent[0][1]


def test_a_second_zip_of_the_same_name_does_not_overwrite_the_first(tmp_path, form_pdf):
    config = _config(tmp_path, output_format=FORMAT_EMDR_HL7)
    for _ in range(2):
        _zip(os.path.join(config.inbound, "case.zip"), [("case.pdf", form_pdf)])
        service.run_once(config)
    assert sorted(os.listdir(config.processed)) == ["case-2.zip", "case.zip"]
    assert len(os.listdir(config.outbound)) == 2


def test_the_mail_reports_the_zip_the_folder_and_the_reason(monkeypatch, tmp_path):
    config = _config(
        tmp_path,
        email=service.EmailSettings(host="smtp.example.org", sender="a@example.org", recipients=["b@example.org"]),
    )
    messages = []

    class Server:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def send_message(self, message):
            messages.append(message)

    monkeypatch.setattr(service.smtplib, "SMTP", lambda *args, **kwargs: Server())
    assert service.send_failure_email(config, "/in/case.zip", "ValueError: unreadable")

    body = messages[0].get_content()
    assert "case.zip" in messages[0]["Subject"]
    assert messages[0]["To"] == "b@example.org"
    assert "ValueError: unreadable" in body and config.error in body


def test_nothing_is_mailed_when_no_server_is_configured(tmp_path):
    assert not service.send_failure_email(_config(tmp_path), "/in/case.zip", "boom")

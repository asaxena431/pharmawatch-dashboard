import io

import pytest

from doc_redactor.web_app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Redaction rules" in r.data


def test_upload_and_download(client):
    r = client.post("/redact", data={"document": (io.BytesIO(b"SSN 123-45-6789"), "a.txt")},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and b"ssn: 1" in r.data
    token = r.data.decode().split("/download/")[1].split('"')[0]
    d = client.get(f"/download/{token}")
    assert d.data == b"SSN [SSN]" and "a_redacted.txt" in d.headers["Content-Disposition"]
    assert client.get(f"/report/{token}").status_code == 200


def test_sample(client):
    r = client.post("/redact", data={"sample": "sample_case_report.pdf"})
    assert r.status_code == 200 and b"data:image/png" in r.data


def test_bad_rules_and_bad_file(client):
    r = client.post("/redact", data={"rules_text": "rules: [{type: nope}]",
                                     "document": (io.BytesIO(b"x"), "a.txt")}, content_type="multipart/form-data")
    assert r.status_code == 400 and b"Rules error" in r.data
    r = client.post("/redact", data={"document": (io.BytesIO(b"x"), "a.exe")}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_bad_token(client):
    assert client.get("/download/../../etc/passwd").status_code == 404
    assert client.get("/download/" + "a" * 24).status_code == 404

"""Flask UI: upload a file, edit the YAML rules, download the redacted copy."""
import base64
import collections
import json
import os
import re
import secrets
import tempfile
import time

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for

from .engine import BUILTIN_PATTERNS, RulesError, load_rules
from .formats import SUPPORTED_EXTS, UnsupportedFile, output_name, redact_file

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES_PATH = os.environ.get("REDACTOR_RULES", os.path.join(HERE, "config", "rules.yaml"))
SAMPLES_DIR = os.path.join(HERE, "samples")
OUT_DIR = os.path.join(tempfile.gettempdir(), "doc_redactor_out")
MAX_MB = int(os.environ.get("REDACTOR_MAX_MB", "20"))
KEEP_SECONDS = 30 * 60
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
os.makedirs(OUT_DIR, exist_ok=True)


def _default_rules() -> str:
    with open(DEFAULT_RULES_PATH, encoding="utf-8") as fh:
        return fh.read()


def _samples() -> list[str]:
    if not os.path.isdir(SAMPLES_DIR):
        return []
    return sorted(f for f in os.listdir(SAMPLES_DIR) if not f.startswith("."))


def _cleanup() -> None:
    cutoff = time.time() - KEEP_SECONDS
    for name in os.listdir(OUT_DIR):
        path = os.path.join(OUT_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _index(rules_text: str, status: int = 200):
    return render_template(
        "index.html",
        rules_text=rules_text,
        exts=", ".join(SUPPORTED_EXTS),
        builtins=", ".join(BUILTIN_PATTERNS),
        samples=_samples(),
        max_mb=MAX_MB,
    ), status


@app.get("/")
def index():
    return _index(_default_rules())


@app.post("/redact")
def redact():
    _cleanup()
    rules_text = request.form.get("rules_text") or ""
    rules_file = request.files.get("rules_file")
    if rules_file and rules_file.filename:
        rules_text = rules_file.read().decode("utf-8", errors="replace")
    if not rules_text.strip():
        rules_text = _default_rules()
    try:
        ruleset = load_rules(rules_text)
    except RulesError as exc:
        flash(f"Rules error: {exc}")
        return _index(rules_text, 400)

    upload = request.files.get("document")
    sample = request.form.get("sample") or ""
    if upload and upload.filename:
        filename, data = os.path.basename(upload.filename), upload.read()
    elif sample in _samples():
        filename = sample
        with open(os.path.join(SAMPLES_DIR, sample), "rb") as fh:
            data = fh.read()
    else:
        flash("Choose a file to upload (or pick a sample).")
        return _index(rules_text, 400)

    try:
        res = redact_file(filename, data, ruleset)
    except UnsupportedFile as exc:
        flash(str(exc))
        return _index(rules_text, 400)

    token = secrets.token_urlsafe(18)
    out_name = output_name(filename)
    with open(os.path.join(OUT_DIR, token), "wb") as fh:
        fh.write(res.data)
    report = {
        "file": filename,
        "redacted_file": out_name,
        "counts": dict(collections.Counter(h.rule for h in res.hits)),
        "hits": [h.__dict__ for h in res.hits],
        "warnings": res.warnings,
    }
    with open(os.path.join(OUT_DIR, token + ".report"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    with open(os.path.join(OUT_DIR, token + ".meta"), "w", encoding="utf-8") as fh:
        json.dump({"name": out_name, "mimetype": res.mimetype}, fh)

    return render_template(
        "result.html",
        filename=filename,
        out_name=out_name,
        token=token,
        counts=collections.Counter(h.rule for h in res.hits).most_common(),
        hits=res.hits,
        warnings=res.warnings,
        preview_text=res.preview_text,
        preview_images=[base64.b64encode(img).decode() for img in res.preview_images],
    )


def _token_path(token: str, suffix: str = "") -> str:
    path = os.path.join(OUT_DIR, token + suffix)
    if not TOKEN_RE.match(token) or not os.path.exists(path):
        abort(404, "This download has expired. Upload the file again.")
    return path


@app.get("/download/<token>")
def download(token):
    path = _token_path(token)
    with open(_token_path(token, ".meta"), encoding="utf-8") as fh:
        meta = json.load(fh)
    return send_file(path, mimetype=meta["mimetype"], as_attachment=True, download_name=meta["name"])


@app.get("/report/<token>")
def report(token):
    path = _token_path(token, ".report")
    with open(_token_path(token, ".meta"), encoding="utf-8") as fh:
        meta = json.load(fh)
    return send_file(path, mimetype="application/json", as_attachment=True,
                     download_name=meta["name"] + ".report.json")


@app.get("/rules.yaml")
def default_rules():
    return send_file(DEFAULT_RULES_PATH, mimetype="text/yaml", as_attachment=True, download_name="rules.yaml")


@app.get("/samples/<name>")
def sample_file(name):
    if name not in _samples():
        abort(404)
    return send_file(os.path.join(SAMPLES_DIR, name), as_attachment=True, download_name=name)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.errorhandler(413)
def too_large(_exc):
    flash(f"File is too large (limit {MAX_MB} MB).")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")), debug=False)

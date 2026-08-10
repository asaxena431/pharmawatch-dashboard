"""Flask blueprint for the MedWatch 3500A -> XML demo GUI.

Shares the exact same backend as the CLI (:mod:`medwatch_ocr.pipeline`).
Mount it on any Flask app::

    from medwatch_ocr.web import medwatch_bp
    app.register_blueprint(medwatch_bp)

and browse to ``/medwatch``.
"""

import os
import shutil
import tempfile
import traceback
import xml.etree.ElementTree as ElementTree
from typing import List, Optional

from flask import Blueprint, Flask, Response, jsonify, redirect, render_template, request

from .form_1932 import VETERINARY_SAMPLES, fill_1932_form
from .models import CENTER_CDER, CENTER_CDRH, CENTER_CVM, STAGE_POSTMARKET, STAGE_PREMARKET
from .ocr import OcrError
from .official_form import OFFICIAL_SAMPLES, fill_official_form
from .pipeline import ENGINE_TEXT_LAYER, FORMAT_E2B, FORMAT_GL42, FORMAT_MDR, LAYOUT_AUTO, LAYOUTS, convert_pdf
from .samples import SAMPLES, render_sample
from .xml_diff import diff_xml, message_format

medwatch_bp = Blueprint("medwatch", __name__)

SAMPLE_LABELS = {
    "cder_premarket": {
        "title": "CDER premarket (drug / IND safety report)",
        "center": CENTER_CDER,
        "stage": STAGE_PREMARKET,
        "format": FORMAT_E2B,
    },
    "cdrh_postmarket": {
        "title": "CDRH postmarket (device / MDR)",
        "center": CENTER_CDRH,
        "stage": STAGE_POSTMARKET,
        "format": FORMAT_MDR,
    },
    "cvm_veterinary": {
        "title": "CVM veterinary (Form FDA 1932 / VICH GL42)",
        "center": CENTER_CVM,
        "stage": STAGE_POSTMARKET,
        "format": FORMAT_GL42,
    },
}

_SAMPLE_DIR = os.path.join(tempfile.gettempdir(), "medwatch_samples")


def _sample_pdf(name: str, facsimile: bool = False) -> str:
    """Render the requested sample PDF on demand and return its path.

    By default this fills the genuine FDA fillable 3500A form; ``facsimile``
    renders the flat label/value layout instead.
    """
    os.makedirs(_SAMPLE_DIR, exist_ok=True)
    if facsimile and name in SAMPLES:
        path = os.path.join(_SAMPLE_DIR, f"3500A_{name}.pdf")
        if not os.path.exists(path):
            render_sample(SAMPLES[name], path)
        return path
    if name in VETERINARY_SAMPLES:
        path = os.path.join(_SAMPLE_DIR, f"FDA-1932_{name}.pdf")
        if not os.path.exists(path):
            fill_1932_form(VETERINARY_SAMPLES[name], path)
        return path
    if name not in OFFICIAL_SAMPLES:
        raise KeyError(name)
    path = os.path.join(_SAMPLE_DIR, f"FDA-3500A_{name}.pdf")
    if not os.path.exists(path):
        fill_official_form(OFFICIAL_SAMPLES[name], path)
    return path


def _is_facsimile(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on", "facsimile")


@medwatch_bp.route("/medwatch", methods=["GET"])
def medwatch_home():
    return render_template("medwatch.html", samples=SAMPLE_LABELS)


@medwatch_bp.route("/medwatch/sample/<name>", methods=["GET"])
def medwatch_sample(name: str):
    facsimile = _is_facsimile(request.args.get("layout", ""))
    try:
        path = _sample_pdf(name, facsimile=facsimile)
    except KeyError:
        return jsonify({"error": f"unknown sample: {name}"}), 404
    with open(path, "rb") as handle:
        data = handle.read()
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{os.path.basename(path)}"'},
    )


def _save(upload, directory: str) -> str:
    """Save an upload under its own name: a 1932a message names every file it carries."""
    path = os.path.join(directory, os.path.basename(upload.filename))
    upload.save(path)
    return path


def _expected_xml(payload: dict) -> Optional[str]:
    """The XML to compare against: an uploaded file or inline text."""
    upload = request.files.get("expected_xml")
    if upload and upload.filename:
        return upload.read().decode("utf-8", errors="replace")
    text = request.form.get("expected_xml") or payload.get("expected_xml")
    return text or None


@medwatch_bp.route("/api/medwatch/convert", methods=["POST"])
def api_medwatch_convert():
    """OCR a 3500A PDF (uploaded or one of the samples) and return XML + fields."""
    payload = request.get_json(silent=True) or {}
    sample = request.form.get("sample") or payload.get("sample")
    center = request.form.get("center") or payload.get("center") or None
    stage = request.form.get("stage") or payload.get("stage") or None
    output_format = request.form.get("format") or payload.get("format") or None
    engine = request.form.get("engine") or payload.get("engine") or ENGINE_TEXT_LAYER
    dpi = int(request.form.get("dpi") or payload.get("dpi") or 200)
    layout = request.form.get("layout") or payload.get("layout") or LAYOUT_AUTO
    if layout not in LAYOUTS:
        return jsonify({"error": f"unknown layout: {layout}"}), 400
    facsimile = _is_facsimile(request.form.get("facsimile") or payload.get("facsimile") or "")

    expected = _expected_xml(payload)
    if expected and not output_format:
        # Compare like with like: an expected message states which profile to write,
        # unless a format was chosen explicitly.
        output_format = message_format(expected)

    uploaded = None
    attachments: List[str] = []
    try:
        upload = request.files.get("pdf")
        if upload and upload.filename:
            uploaded = tempfile.mkdtemp(prefix="medwatch_upload_")
            pdf_path = _save(upload, uploaded)
            attachments = [_save(each, uploaded) for each in request.files.getlist("attachments") if each.filename]
        else:
            sample = sample or "cder_premarket"
            try:
                pdf_path = _sample_pdf(sample, facsimile=facsimile)
            except KeyError:
                return jsonify({"error": f"unknown sample: {sample}"}), 400
            defaults = SAMPLE_LABELS[sample]
            center = center or defaults["center"]
            stage = stage or defaults["stage"]
            output_format = output_format or defaults["format"]

        result = convert_pdf(
            pdf_path,
            center=center or None,
            stage=stage or None,
            output_format=output_format or None,
            engine=engine,
            dpi=dpi,
            layout=layout,
            attachments=attachments,
        )
        response = {
            "summary": result.summary,
            "xml": result.xml,
            "fields": result.report.to_dict(),
            "ocr_text": result.ocr.text,
        }
        if expected:
            try:
                response["diff"] = diff_xml(result.xml, expected).as_dict()
            except ElementTree.ParseError as exc:
                response["diff_error"] = f"expected XML could not be parsed: {exc}"
        return jsonify(response)
    except OcrError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:  # pragma: no cover - surfaced in the UI
        traceback.print_exc()
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        if uploaded:
            shutil.rmtree(uploaded, ignore_errors=True)


def create_app():
    """Standalone demo app: ``flask --app medwatch_ocr.web:create_app run``."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
    )
    app.register_blueprint(medwatch_bp)
    app.add_url_rule("/", "root", lambda: redirect("/medwatch"))
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 5060)), debug=False)

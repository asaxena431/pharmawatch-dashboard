"""Flask blueprint for the MedWatch 3500A -> XML demo GUI.

Shares the exact same backend as the CLI (:mod:`medwatch_ocr.pipeline`).
Mount it on any Flask app::

    from medwatch_ocr.web import medwatch_bp
    app.register_blueprint(medwatch_bp)

and browse to ``/medwatch``.
"""

import os
import tempfile
import traceback

from flask import Blueprint, Flask, Response, jsonify, redirect, render_template, request

from .models import CENTER_CDER, CENTER_CDRH, STAGE_POSTMARKET, STAGE_PREMARKET
from .ocr import OcrError
from .pipeline import FORMAT_E2B, FORMAT_MDR, convert_pdf
from .samples import SAMPLES, render_sample

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
}

_SAMPLE_DIR = os.path.join(tempfile.gettempdir(), "medwatch_samples")


def _sample_pdf(name: str) -> str:
    """Render the requested sample PDF on demand and return its path."""
    if name not in SAMPLES:
        raise KeyError(name)
    os.makedirs(_SAMPLE_DIR, exist_ok=True)
    path = os.path.join(_SAMPLE_DIR, f"3500A_{name}.pdf")
    if not os.path.exists(path):
        render_sample(SAMPLES[name], path)
    return path


@medwatch_bp.route("/medwatch", methods=["GET"])
def medwatch_home():
    return render_template("medwatch.html", samples=SAMPLE_LABELS)


@medwatch_bp.route("/medwatch/sample/<name>", methods=["GET"])
def medwatch_sample(name: str):
    try:
        path = _sample_pdf(name)
    except KeyError:
        return jsonify({"error": f"unknown sample: {name}"}), 404
    with open(path, "rb") as handle:
        data = handle.read()
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="3500A_{name}.pdf"'},
    )


@medwatch_bp.route("/api/medwatch/convert", methods=["POST"])
def api_medwatch_convert():
    """OCR a 3500A PDF (uploaded or one of the samples) and return XML + fields."""
    payload = request.get_json(silent=True) or {}
    sample = request.form.get("sample") or payload.get("sample")
    center = request.form.get("center") or payload.get("center") or None
    stage = request.form.get("stage") or payload.get("stage") or None
    output_format = request.form.get("format") or payload.get("format") or None
    engine = request.form.get("engine") or payload.get("engine") or "auto"
    dpi = int(request.form.get("dpi") or payload.get("dpi") or 200)

    temp_path = None
    try:
        upload = request.files.get("pdf")
        if upload and upload.filename:
            handle, temp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(handle)
            upload.save(temp_path)
            pdf_path = temp_path
        else:
            sample = sample or "cder_premarket"
            try:
                pdf_path = _sample_pdf(sample)
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
        )
        return jsonify(
            {
                "summary": result.summary,
                "xml": result.xml,
                "fields": result.report.to_dict(),
                "ocr_text": result.ocr.text,
            }
        )
    except OcrError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:  # pragma: no cover - surfaced in the UI
        traceback.print_exc()
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


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

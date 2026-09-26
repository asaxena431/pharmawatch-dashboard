"""
Human-in-the-loop review UI (replaces the SOP's "trainee -> Inbox -> STL QC" loop).

  python -m mdr_redaction.review_app mdr_redaction/sample_reports.json  [--port 5051]

Loads a batch, runs triage/redaction/reportables/linking, then serves:
  /                 inbox in SOP priority order with review status
  /report/<num>     side-by-side original vs. redacted text, every finding with
                    accept/reject toggles, reportables + link candidates, editable
                    redacted text, Complete / Return-to-inbox buttons
  /export           decisions.json (approved text + per-finding accept/reject)

State is kept in memory and mirrored to <input>.decisions.json on every save.
Standalone: does not touch the main PharmaWatch app.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timezone

from flask import Flask, Response, redirect, request, url_for

from .linking import find_links
from .redactor import redact_report
from .reportables import detect_dicts
from .triage import prioritize

app = Flask(__name__)
STATE: dict = {"reports": [], "by_num": {}, "links": [], "decisions": {}, "decisions_path": ""}


# ------------------------------------------------------------ data -----------
def load(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        reports = json.load(fh)
    ordered = prioritize(reports)
    STATE["reports"] = []
    for r in ordered:
        red = redact_report(r)
        red["original_sections"] = r.get("sections") or {}
        red["reportables"] = detect_dicts(r)
        STATE["reports"].append(red)
    STATE["by_num"] = {r["report_number"]: r for r in STATE["reports"]}
    STATE["links"] = [c.to_dict() for c in find_links(reports)]
    STATE["decisions_path"] = path + ".decisions.json"
    if os.path.exists(STATE["decisions_path"]):
        with open(STATE["decisions_path"], encoding="utf-8") as fh:
            STATE["decisions"] = json.load(fh)
    else:
        STATE["decisions"] = {}


def _save() -> None:
    with open(STATE["decisions_path"], "w", encoding="utf-8") as fh:
        json.dump(STATE["decisions"], fh, indent=2)


def _status(num: str) -> str:
    return STATE["decisions"].get(num, {}).get("status", "PENDING")


def _links_for(num: str) -> list[dict]:
    return [c for c in STATE["links"] if num in (c["report_a"], c["report_b"])]


# ------------------------------------------------------------ html -----------
_CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#111}
header{background:#1f3a5f;color:#fff;padding:10px 20px;font-weight:600}
main{padding:16px 20px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border-bottom:1px solid #e3e6ea;padding:6px 8px;text-align:left;font-size:14px}
tr:hover td{background:#f0f4fa}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px;font-weight:600}
.CODE_BLUE{background:#d32f2f;color:#fff}.DEATH{background:#000;color:#fff}.INJURY{background:#e67e22;color:#fff}
.VOLUNTARY,.UF,.IMP{background:#3498db;color:#fff}.MALFUNCTION{background:#7f8c8d;color:#fff}.SUPPLEMENT{background:#bdc3c7}
.PENDING{background:#fff3cd}.COMPLETE{background:#d4edda}.RETURNED{background:#f8d7da}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.box{background:#fff;border:1px solid #e3e6ea;border-radius:6px;padding:12px}
pre,textarea{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:13px;width:100%;box-sizing:border-box}
textarea{min-height:160px}
mark{background:#ffe08a}
.b6{color:#b00020;font-weight:600}.b4{color:#0b5394;font-weight:600}
button{padding:6px 12px;border:0;border-radius:4px;cursor:pointer;font-weight:600}
.ok{background:#2e7d32;color:#fff}.warn{background:#c62828;color:#fff}.plain{background:#e0e0e0}
small{color:#555}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{_CSS}</style></head><body><header>eMDR Redaction Review &mdash; {html.escape(title)}"
            f"</header><main>{body}</main></body></html>")


def _e(s) -> str:
    return html.escape(str(s or ""))


def _highlight(text: str) -> str:
    t = _e(text)
    t = t.replace("(B)(6)", "<span class='b6'>(B)(6)</span>").replace("(B)(4)", "<span class='b4'>(B)(4)</span>")
    return t.replace("PROFANITY", "<mark>PROFANITY</mark>")


# ------------------------------------------------------------ routes ---------
@app.get("/")
def index():
    rows = []
    for r in STATE["reports"]:
        num = r["report_number"]
        kinds = ", ".join(sorted({x["kind"] for x in r["reportables"]}))
        nl = len(_links_for(num))
        rows.append(
            f"<tr><td><span class='tag {r['triage_category']}'>{r['triage_category']}</span></td>"
            f"<td><a href='{url_for('report', num=num)}'>{_e(num)}</a></td><td>{_e(r.get('manufacturer'))}</td>"
            f"<td>{_e(r.get('report_type'))}</td><td>{_e(r.get('outcome'))}</td><td>{_e(r.get('days_in_inbox'))}</td>"
            f"<td>{len(r['findings'])}</td><td>{'yes' if r['needs_human_review'] else ''}</td>"
            f"<td>{_e(kinds)}</td><td>{nl or ''}</td><td><span class='tag {_status(num)}'>{_status(num)}</span></td></tr>"
        )
    done = sum(1 for r in STATE["reports"] if _status(r["report_number"]) == "COMPLETE")
    body = (f"<p>{len(STATE['reports'])} reports in SOP priority order &middot; {done} complete &middot; "
            f"<a href='{url_for('export')}'>export decisions.json</a></p>"
            "<table><tr><th>Priority</th><th>Report #</th><th>Manufacturer</th><th>Type</th><th>Outcome</th>"
            "<th>Days</th><th>Redactions</th><th>Needs review</th><th>Reportables</th><th>Links</th><th>Status</th></tr>"
            + "".join(rows) + "</table>")
    return _page("Inbox", body)


@app.get("/report/<path:num>")
def report(num: str):
    r = STATE["by_num"].get(num)
    if not r:
        return _page("Not found", "<p>Unknown report</p>"), 404
    dec = STATE["decisions"].get(num, {})
    rejected = set(dec.get("rejected_findings", []))
    edited = dec.get("sections") or r["sections"]

    sections_html = []
    for sec, orig in r["original_sections"].items():
        sections_html.append(
            f"<h3>{_e(sec)}</h3><div class='grid'><div class='box'><small>original</small><pre>{_e(orig)}</pre></div>"
            f"<div class='box'><small>redacted (editable)</small>"
            f"<textarea name='sec_{_e(sec)}'>{_e(edited.get(sec, ''))}</textarea></div></div>"
        )

    frows = []
    for i, f in enumerate(r["findings"]):
        checked = "" if i in rejected else "checked"
        frows.append(
            f"<tr><td><input type='checkbox' name='accept_{i}' {checked}></td><td>{_e(f['section'])}</td>"
            f"<td>{_e(f['rule'])}</td><td>{_highlight(f['exemption'])}</td><td>{_e(f['original'])}</td>"
            f"<td>{_highlight(f['replacement'])}</td></tr>"
        )
    findings_html = ("<table><tr><th>Accept</th><th>Section</th><th>Rule</th><th>Exemption</th><th>Original</th>"
                     "<th>Replacement</th></tr>" + "".join(frows) + "</table>") if frows else "<p>No automatic redactions.</p>"

    rep_html = "".join(
        f"<li><b>{_e(x['kind'])}</b> &mdash; <code>{_e(x['email_subject'])}</code><br><small>{_e(x['evidence'])}</small></li>"
        for x in r["reportables"]
    ) or "<li>none</li>"
    link_html = "".join(
        f"<li>{'CONFIRMED' if c['confirmed'] else 'score ' + str(c['score'])} &mdash; "
        f"<code>{_e(c['email_subject'])}</code><br><small>{_e('; '.join(c['reasons']))}</small></li>"
        for c in _links_for(num)
    ) or "<li>none</li>"

    note = _e(dec.get("note", ""))
    body = (
        f"<p><a href='{url_for('index')}'>&larr; inbox</a> &middot; <span class='tag {r['triage_category']}'>"
        f"{r['triage_category']}</span> &middot; {_e(r.get('manufacturer'))} &middot; {_e(r.get('report_type'))} &middot; "
        f"status <span class='tag {_status(num)}'>{_status(num)}</span>"
        + (" &middot; <b style='color:#c62828'>trade-secret paragraph auto-redacted &mdash; verify</b>" if r["needs_human_review"] else "")
        + "</p>"
        f"<form method='post' action='{url_for('save', num=num)}'>"
        + "".join(sections_html)
        + f"<h3>Automatic redactions ({len(r['findings'])})</h3>{findings_html}"
        f"<div class='grid'><div class='box'><h3>Reportables</h3><ul>{rep_html}</ul></div>"
        f"<div class='box'><h3>Link candidates</h3><ul>{link_html}</ul></div></div>"
        f"<p><label>Reviewer note <input name='note' value='{note}' style='width:60%'></label></p>"
        "<p><button class='ok' name='action' value='COMPLETE'>Complete</button> "
        "<button class='warn' name='action' value='RETURNED'>Return to inbox (needs STL)</button> "
        "<button class='plain' name='action' value='PENDING'>Save draft</button></p></form>"
    )
    return _page(num, body)


@app.post("/report/<path:num>/save")
def save(num: str):
    r = STATE["by_num"].get(num)
    if not r:
        return redirect(url_for("index"))
    sections = {k[4:]: v for k, v in request.form.items() if k.startswith("sec_")}
    rejected = [i for i in range(len(r["findings"])) if f"accept_{i}" not in request.form]
    STATE["decisions"][num] = {
        "status": request.form.get("action", "PENDING"),
        "sections": sections,
        "rejected_findings": rejected,
        "note": request.form.get("note", ""),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save()
    nxt = next((x["report_number"] for x in STATE["reports"] if _status(x["report_number"]) == "PENDING"), None)
    if request.form.get("action") == "COMPLETE" and nxt:
        return redirect(url_for("report", num=nxt))
    return redirect(url_for("index"))


@app.get("/export")
def export():
    return Response(json.dumps(STATE["decisions"], indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=decisions.json"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="eMDR redaction review UI")
    ap.add_argument("input", help="JSON file: list of reports")
    ap.add_argument("--port", type=int, default=5051)
    args = ap.parse_args(argv)
    load(args.input)
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

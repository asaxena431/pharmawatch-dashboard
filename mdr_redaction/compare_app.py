"""
Web UI to review the automation-vs-editor comparison (same data as `compare` writes to Excel).

  python -m mdr_redaction.compare_app data/redacted_narratives.xlsx [--port 5052]

  /                 all narratives, filter by status, counts per status
  /row/<record_id>  original / editor / automation side by side with the differing words
                    highlighted, the reason for every difference and the SOP reference;
                    reviewer verdict (editor right / automation right / both wrong) + note
  /export           verdicts.json

Standalone: does not touch the main PharmaWatch app.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, Response, redirect, request, url_for

from .compare import SOP_REFS, RowResult, compare, load_rows, summary

app = Flask(__name__)
STATE: dict = {"rows": [], "by_id": {}, "verdicts": {}, "verdicts_path": ""}
STATUSES = ["MATCH", "FORMAT_ONLY", "AUTO_MISSED", "AUTO_EXTRA", "BOTH", "HUMAN_INCONSISTENT"]
VERDICTS = ["EDITOR_RIGHT", "AUTOMATION_RIGHT", "BOTH_ACCEPTABLE", "BOTH_WRONG"]

_CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#111}
header{background:#1f3a5f;color:#fff;padding:10px 20px;font-weight:600}
header a{color:#fff}
main{padding:16px 20px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border-bottom:1px solid #e3e6ea;padding:6px 8px;text-align:left;font-size:14px;vertical-align:top}
tr:hover td{background:#f0f4fa}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px;font-weight:600}
.MATCH{background:#c6efce}.FORMAT_ONLY{background:#e2efda}.AUTO_MISSED{background:#f8cbad}
.AUTO_EXTRA{background:#ffe699}.BOTH{background:#f4b084}.HUMAN_INCONSISTENT{background:#d9d2e9}
.EDITOR_RIGHT{background:#cfe2f3}.AUTOMATION_RIGHT{background:#d9ead3}.BOTH_ACCEPTABLE{background:#eee}.BOTH_WRONG{background:#f4cccc}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.box{background:#fff;border:1px solid #e3e6ea;border-radius:6px;padding:12px}
.box p.t{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:13px;line-height:1.5}
.tok{color:#b00020;font-weight:600}
.hum{background:#ffd6d6}.aut{background:#fff2b2}.ed{background:#dbe9ff}
.ref{background:#fffbe6;border-left:4px solid #e0b400;padding:8px 12px;white-space:pre-wrap;font-size:13px}
button{padding:6px 12px;border:0;border-radius:4px;cursor:pointer;font-weight:600;background:#1f3a5f;color:#fff}
small{color:#555}
.filters a{margin-right:10px}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(title)}</title><style>{_CSS}</style></head>"
            f"<body><header><a href='{url_for('index')}'>eMDR Redaction &mdash; automation vs editor</a> &middot; {_e(title)}"
            f"</header><main>{body}</main></body></html>")


def _mark_tokens(t: str) -> str:
    for tok in ("(B)(6)", "(B)(4)", "(b)(6)", "(b)(4)"):
        t = t.replace(tok, f"<span class='tok'>{tok}</span>")
    return t


def _diff_html(original: str, redacted: str, cls: str) -> str:
    """Redacted text with the words that differ from the original highlighted."""
    def norm(ws: list[str]) -> list[str]:
        return [w.lower().strip(".,;:") for w in ws]
    words = redacted.split()
    sm = difflib.SequenceMatcher(None, norm(original.split()), norm(words), autojunk=False)
    out = []
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        chunk = _e(" ".join(words[j1:j2]))
        out.append(chunk if op == "equal" else f"<span class='{cls}'>{chunk}</span>")
    return _mark_tokens(" ".join(x for x in out if x))


def _orig_html(r: RowResult) -> str:
    t = _e(r.original)
    for span in filter(None, (r.human_only + " | " + r.auto_only).split(" | ")):
        low = t.lower()
        i = low.find(span.strip())
        if i >= 0:
            cls = "hum" if span in r.human_only else "aut"
            t = t[:i] + f"<span class='{cls}'>" + t[i:i + len(span)] + "</span>" + t[i + len(span):]
    return t


def _verdict(rid: str) -> str:
    return STATE["verdicts"].get(rid, {}).get("verdict", "")


# ------------------------------------------------------------ routes ---------
@app.get("/")
def index():
    flt = request.args.get("status", "DIFF")
    rows = STATE["rows"]
    shown = [r for r in rows if flt == "ALL" or r.status == flt or (flt == "DIFF" and r.status not in ("MATCH", "FORMAT_ONLY"))]
    counts = {s: sum(1 for r in rows if r.status == s) for s in STATUSES}
    filters = " ".join(
        f"<a href='?status={s}'><span class='tag {s}'>{s} {counts[s]}</span></a>" for s in STATUSES
    )
    trs = []
    for r in shown:
        trs.append(
            f"<tr><td><span class='tag {r.status}'>{r.status}</span></td>"
            f"<td><a href='{url_for('row', rid=r.record_id)}'>{_e(r.record_id)}</a></td>"
            f"<td>{_e(r.reason)[:300]}</td><td>{_e(r.human_only)}</td><td>{_e(r.auto_only)}</td>"
            f"<td><span class='tag {_verdict(r.record_id)}'>{_verdict(r.record_id)}</span></td></tr>"
        )
    body = (f"<pre>{_e(summary(rows))}</pre>"
            f"<p class='filters'><a href='?status=DIFF'><b>differences only</b></a> <a href='?status=ALL'>all</a> {filters}"
            f" &middot; <a href='{url_for('export')}'>export verdicts.json</a></p>"
            "<table><tr><th>Status</th><th>Record</th><th>Reason</th><th>Editor redacted, automation kept</th>"
            "<th>Automation redacted, editor kept</th><th>Your verdict</th></tr>" + "".join(trs) + "</table>")
    return _page(f"{len(shown)} of {len(rows)} narratives", body)


@app.get("/row/<rid>")
def row(rid: str):
    r = STATE["by_id"].get(rid)
    if not r:
        return _page("Not found", "<p>Unknown record</p>"), 404
    ids = [x.record_id for x in STATE["rows"]]
    i = ids.index(rid)
    prev_id = ids[i - 1] if i > 0 else None
    next_id = ids[i + 1] if i + 1 < len(ids) else None
    v = STATE["verdicts"].get(rid, {})

    findings = "".join(f"<li><code>{_e(f)}</code></li>" for f in r.findings.split("; ") if f) or "<li>none</li>"
    reasons = "".join(f"<li>{_e(x)}</li>" for x in r.reason.split("; ") if x)
    refs = "".join(f"<div class='ref'>{_e(x)}</div>" for x in r.sop_reference.split("\n") if x) or "<small>n/a</small>"
    opts = "".join(f"<option value='{o}' {'selected' if v.get('verdict') == o else ''}>{o}</option>" for o in [""] + VERDICTS)

    prev_a = f"<a href='{url_for('row', rid=prev_id)}'>&larr; prev</a>" if prev_id else ""
    next_a = f"<a href='{url_for('row', rid=next_id)}'>next &rarr;</a>" if next_id else ""
    body = (
        f"<p>{prev_a} &middot; <a href='{url_for('index')}'>list</a> &middot; {next_a} &middot; "
        f"<span class='tag {r.status}'>{r.status}</span></p>"
        "<div class='grid'>"
        f"<div class='box'><small>ORIGINAL (COMPANY_NARRATIVE) &mdash; <span class='hum'>editor-only redaction</span> "
        f"<span class='aut'>automation-only redaction</span></small><p class='t'>{_orig_html(r)}</p></div>"
        f"<div class='box'><small>EDITOR (REDACTED_NARRATIVE) &mdash; <span class='hum'>changed vs original</span></small>"
        f"<p class='t'>{_diff_html(r.original, r.human_redacted, 'hum')}</p></div>"
        f"<div class='box'><small>AUTOMATION &mdash; <span class='aut'>changed vs original</span></small>"
        f"<p class='t'>{_diff_html(r.original, r.auto_redacted, 'aut')}</p></div></div>"
        f"<h3>Why they differ</h3><ul>{reasons}</ul>"
        f"<h3>SOP reference</h3>{refs}"
        f"<h3>Automation rules fired ({len([f for f in r.findings.split('; ') if f])})</h3><ul>{findings}</ul>"
        f"<form method='post' action='{url_for('save', rid=rid)}'><h3>Reviewer verdict</h3>"
        f"<p><select name='verdict'>{opts}</select> "
        f"<input name='note' placeholder='note' value='{_e(v.get('note', ''))}' style='width:50%'> "
        "<button>Save &amp; next</button></p></form>"
    )
    return _page(rid, body)


@app.post("/row/<rid>/save")
def save(rid: str):
    STATE["verdicts"][rid] = {
        "verdict": request.form.get("verdict", ""),
        "note": request.form.get("note", ""),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(STATE["verdicts_path"], "w", encoding="utf-8") as fh:
        json.dump(STATE["verdicts"], fh, indent=2)
    ids = [x.record_id for x in STATE["rows"] if x.status not in ("MATCH", "FORMAT_ONLY")]
    later = [x for x in ids if x > rid] if rid in ids else ids
    nxt = next((x for x in later if not _verdict(x)), None)
    return redirect(url_for("row", rid=nxt) if nxt else url_for("index"))


@app.get("/export")
def export():
    return Response(json.dumps(STATE["verdicts"], indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=verdicts.json"})


@app.get("/sop")
def sop():
    body = "<table><tr><th>key</th><th>SOP reference</th></tr>" + "".join(
        f"<tr><td>{k}</td><td>{_e(v)}</td></tr>" for k, v in SOP_REFS.items()) + "</table>"
    return _page("SOP references", body)


def load(path: str, orig_col: str, red_col: str, id_col: str, section: str) -> None:
    rows = compare(load_rows(Path(path), orig_col, red_col, id_col), orig_col, red_col, id_col, section)
    STATE["rows"] = rows
    STATE["by_id"] = {r.record_id: r for r in rows}
    STATE["verdicts_path"] = path + ".verdicts.json"
    if os.path.exists(STATE["verdicts_path"]):
        with open(STATE["verdicts_path"], encoding="utf-8") as fh:
            STATE["verdicts"] = json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review automation-vs-editor redaction comparison in a browser")
    ap.add_argument("input", help=".xlsx/.csv with original + editor-redacted columns")
    ap.add_argument("--orig-col", default="COMPANY_NARRATIVE")
    ap.add_argument("--redacted-col", default="REDACTED_NARRATIVE")
    ap.add_argument("--id-col", default="RECORD_ID")
    ap.add_argument("--section", default="B5")
    ap.add_argument("--port", type=int, default=5052)
    a = ap.parse_args(argv)
    load(a.input, a.orig_col, a.redacted_col, a.id_col, a.section)
    app.run(host="127.0.0.1", port=a.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

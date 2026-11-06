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
.hum{background:#ffd6d6}.aut{background:#fff2b2}.both{background:#d4f5d4}.ed{background:#dbe9ff}
mark{padding:0 2px;border-radius:3px;font-weight:600}
.legend mark{margin-right:8px}
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


def _norm(ws: list[str]) -> list[str]:
    return [w.lower().strip(".,;:") for w in ws]


def _changed_orig_idx(original: str, redacted: str) -> set[int]:
    """Indices of ORIGINAL words that the redacted text replaced or deleted."""
    sm = difflib.SequenceMatcher(None, _norm(original.split()), _norm(redacted.split()), autojunk=False)
    idx: set[int] = set()
    for op, i1, i2, _j1, _j2 in sm.get_opcodes():
        if op != "equal":
            idx.update(range(i1, i2))
    return idx


def _mark(words: list[str], cls_of) -> str:
    """Join words, wrapping each run of marked words in <mark class=...>&#9654;...</mark>."""
    out, run, run_cls = [], [], None
    def flush():
        if run:
            out.append(f"<mark class='{run_cls}'>&#9654;{_e(' '.join(run))}</mark>" if run_cls else _e(" ".join(run)))
    for i, w in enumerate(words):
        c = cls_of(i)
        if c != run_cls:
            flush()
            run, run_cls = [], c
        run.append(w)
    flush()
    return _mark_tokens(" ".join(out))


def _diff_html(original: str, redacted: str, cls: str) -> str:
    """Redacted text with the words that differ from the original marked."""
    words = redacted.split()
    sm = difflib.SequenceMatcher(None, _norm(original.split()), _norm(words), autojunk=False)
    changed: set[int] = set()
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            changed.update(range(j1, j2))
    return _mark(words, lambda i: cls if i in changed else None)


def _orig_html(r: RowResult) -> str:
    """Original text with the words changed by the editor (red), by automation (yellow) or by both (green) marked."""
    words = r.original.split()
    hum = _changed_orig_idx(r.original, r.human_redacted)
    aut = _changed_orig_idx(r.original, r.auto_redacted)
    return _mark(words, lambda i: "both" if i in hum and i in aut else "hum" if i in hum else "aut" if i in aut else None)


def _pinned() -> list[str]:
    return STATE["verdicts"].setdefault("_pinned", [])


def _save_state() -> None:
    with open(STATE["verdicts_path"], "w", encoding="utf-8") as fh:
        json.dump(STATE["verdicts"], fh, indent=2)


def _verdict(rid: str) -> str:
    v = STATE["verdicts"].get(rid, {})
    return v.get("verdict", "") if isinstance(v, dict) else ""


# ------------------------------------------------------------ routes ---------
@app.get("/")
def index():
    flt = request.args.get("status", "DIFF")
    rows = STATE["rows"]
    shown = [r for r in rows if flt == "ALL" or r.status == flt or (flt == "DIFF" and r.status not in ("MATCH", "FORMAT_ONLY"))]
    pins = _pinned()
    shown.sort(key=lambda r: (r.record_id not in pins, pins.index(r.record_id) if r.record_id in pins else 0))
    counts = {s: sum(1 for r in rows if r.status == s) for s in STATUSES}
    filters = " ".join(
        f"<a href='?status={s}'><span class='tag {s}'>{s} {counts[s]}</span></a>" for s in STATUSES
    )
    trs = []
    for r in shown:
        trs.append(
            f"<tr><td><span class='tag {r.status}'>{r.status}</span></td>"
            f"<td>{'&#128204; ' if r.record_id in pins else ''}<a href='{url_for('row', rid=r.record_id)}'>{_e(r.record_id)}</a></td>"
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
        f"<span class='tag {r.status}'>{r.status}</span> &middot; "
        f"<a href='{url_for('pin', rid=rid)}'>{'unpin' if rid in _pinned() else '&#128204; pin to top'}</a></p>"
        "<p class='legend'><small>&#9654; marks changed words: <mark class='hum'>editor changed</mark>"
        "<mark class='aut'>automation changed</mark><mark class='both'>both changed</mark></small></p>"
        "<div class='grid'>"
        f"<div class='box'><small>ORIGINAL (COMPANY_NARRATIVE)</small><p class='t'>{_orig_html(r)}</p></div>"
        f"<div class='box'><small>EDITOR (REDACTED_NARRATIVE)</small>"
        f"<p class='t'>{_diff_html(r.original, r.human_redacted, 'hum')}</p></div>"
        f"<div class='box'><small>AUTOMATION (REDACTED_NARRATIVE)</small>"
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


@app.get("/row/<rid>/pin")
def pin(rid: str):
    pins = _pinned()
    if rid in pins:
        pins.remove(rid)
    else:
        pins.insert(0, rid)
    _save_state()
    return redirect(url_for("index"))


@app.post("/row/<rid>/save")
def save(rid: str):
    STATE["verdicts"][rid] = {
        "verdict": request.form.get("verdict", ""),
        "note": request.form.get("note", ""),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_state()
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
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="0.0.0.0 lets other machines on your network open the UI (no login - use only on a trusted network)",
    )
    a = ap.parse_args(argv)
    load(a.input, a.orig_col, a.redacted_col, a.id_col, a.section)
    app.run(host=a.host, port=a.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
PharmaWatch API + Web UI
========================
FastAPI app that wraps extract_narrative_openai.parse_narrative.

Endpoints:
  GET  /          - Web UI
  POST /analyze   - JSON API
  GET  /health    - Health check

Environment variables:
  OPENAI_API_KEY  - Your OpenAI API key (required)
"""

import os
import json
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from extract_narrative_openai import parse_narrative, DEFAULT_MODEL

app = FastAPI(title="PharmaWatch", description="Clinical narrative drug-reaction extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request model ─────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    narrative: str
    analyze: bool = True
    model: Optional[str] = DEFAULT_MODEL


# ── HTML UI ───────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>PharmaWatch</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 20px 40px; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.6rem; color: #38bdf8; font-weight: 700; }
  header span { font-size: 0.85rem; color: #94a3b8; }
  .container { max-width: 1000px; margin: 40px auto; padding: 0 20px; }
  .card { background: #1e293b; border-radius: 12px; padding: 28px; margin-bottom: 24px; border: 1px solid #334155; }
  label { display: block; font-size: 0.85rem; color: #94a3b8; margin-bottom: 8px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; }
  textarea { width: 100%; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; padding: 14px; font-size: 0.95rem; resize: vertical; min-height: 140px; outline: none; transition: border .2s; }
  textarea:focus { border-color: #38bdf8; }
  .row { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; margin-top: 16px; }
  select { background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; padding: 10px 14px; font-size: 0.9rem; outline: none; }
  .toggle { display: flex; align-items: center; gap: 8px; font-size: 0.9rem; color: #cbd5e1; }
  .toggle input { width: 18px; height: 18px; accent-color: #38bdf8; }
  button { background: #38bdf8; color: #0f172a; border: none; border-radius: 8px; padding: 11px 28px; font-size: 0.95rem; font-weight: 700; cursor: pointer; transition: background .2s; }
  button:hover { background: #7dd3fc; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .pills { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
  .pill { padding: 5px 14px; border-radius: 20px; font-size: 0.82rem; font-weight: 600; }
  .pill-drug { background: #1d4ed8; color: #bfdbfe; }
  .pill-reaction { background: #7c3aed; color: #ddd6fe; }
  .section-title { font-size: 0.8rem; color: #64748b; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; font-weight: 700; }
  .drug-block { background: #0f172a; border-radius: 10px; padding: 16px; margin-bottom: 14px; border-left: 3px solid #38bdf8; }
  .drug-name { font-size: 1rem; font-weight: 700; color: #38bdf8; margin-bottom: 10px; }
  .rxn-row { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-bottom: 1px solid #1e293b; }
  .rxn-row:last-child { border-bottom: none; }
  .rxn-name { flex: 1; color: #e2e8f0; font-size: 0.92rem; }
  .badge { padding: 3px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 700; min-width: 70px; text-align: center; }
  .HIGH    { background: #166534; color: #86efac; }
  .MEDIUM  { background: #854d0e; color: #fde68a; }
  .LOW     { background: #7c2d12; color: #fdba74; }
  .VERY-LOW{ background: #4c0519; color: #fda4af; }
  .score   { color: #94a3b8; font-size: 0.82rem; min-width: 38px; text-align: right; }
  .reasoning { color: #64748b; font-size: 0.8rem; flex: 2; }
  .error { background: #450a0a; border: 1px solid #b91c1c; border-radius: 8px; padding: 14px; color: #fca5a5; margin-top: 16px; }
  .spinner { display: inline-block; width: 18px; height: 18px; border: 3px solid #334155; border-top-color: #38bdf8; border-radius: 50%; animation: spin .7s linear infinite; margin-right: 8px; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #results { display: none; }
</style>
</head>
<body>
<header>
  <h1>&#128138; PharmaWatch</h1>
  <span>Clinical Narrative Drug-Reaction Extractor</span>
</header>
<div class="container">
  <div class="card">
    <label>Clinical Narrative</label>
    <textarea id="narrative" placeholder="Paste clinical narrative here...
E.g. Patient was prescribed amoxicillin 500mg TID and developed rash and nausea. Ibuprofen 400mg was given for chest pain."></textarea>
    <div class="row">
      <div>
        <label>Model</label>
        <select id="model">
          <option value="gpt-4o-mini">gpt-4o-mini (fast, cheap)</option>
          <option value="gpt-4o">gpt-4o (best accuracy)</option>
        </select>
      </div>
      <label class="toggle"><input type="checkbox" id="analyze" checked/> Analyze drug-reaction links</label>
      <button id="btn" onclick="analyze()">Analyze</button>
    </div>
  </div>

  <div id="results">
    <div class="card" id="drugs-card">
      <div class="section-title">Drugs Detected</div>
      <div class="pills" id="drugs-list"></div>
    </div>
    <div class="card" id="reactions-card">
      <div class="section-title">Reactions Detected</div>
      <div class="pills" id="reactions-list"></div>
    </div>
    <div class="card" id="bydrugcard" style="display:none">
      <div class="section-title">Drug &#8594; Reaction Analysis</div>
      <div id="bydrug-list"></div>
    </div>
  </div>
  <div id="error-box"></div>
</div>

<script>
async function analyze() {
  const narrative = document.getElementById('narrative').value.trim();
  if (!narrative) return;
  const btn = document.getElementById('btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Analyzing...';
  document.getElementById('results').style.display = 'none';
  document.getElementById('error-box').innerHTML = '';

  try {
    const res = await fetch('/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        narrative,
        analyze: document.getElementById('analyze').checked,
        model: document.getElementById('model').value
      })
    });
    const data = await res.json();
    if (data.error) {
      document.getElementById('error-box').innerHTML = `<div class="error"><b>Error:</b> ${data.error}</div>`;
    } else {
      renderResults(data);
    }
  } catch(e) {
    document.getElementById('error-box').innerHTML = `<div class="error"><b>Error:</b> ${e.message}</div>`;
  }
  btn.disabled = false;
  btn.innerHTML = 'Analyze';
}

function renderResults(data) {
  const drugsList = document.getElementById('drugs-list');
  const rxnList   = document.getElementById('reactions-list');
  drugsList.innerHTML = (data.drugs||[]).map(d => `<span class="pill pill-drug">${d}</span>`).join('') || '<span style="color:#64748b">None found</span>';
  rxnList.innerHTML   = (data.reactions||[]).map(r => `<span class="pill pill-reaction">${r}</span>`).join('') || '<span style="color:#64748b">None found</span>';

  const byDrug = data.by_drug;
  const bdCard = document.getElementById('bydrugcard');
  if (byDrug && Object.keys(byDrug).length > 0) {
    let html = '';
    for (const [drug, entries] of Object.entries(byDrug)) {
      html += `<div class="drug-block"><div class="drug-name">&#128138; ${drug}</div>`;
      if (entries.length === 0) { html += '<span style="color:#64748b;font-size:.85rem">No reactions attributed</span>'; }
      entries.forEach(e => {
        const lbl = (e.confidence_label||'').replace(' ','-');
        html += `<div class="rxn-row">
          <span class="rxn-name">${e.reaction}</span>
          <span class="badge ${lbl}">${e.confidence_label||''}</span>
          <span class="score">${(e.confidence*100).toFixed(0)}%</span>
          <span class="reasoning">${e.reasoning||e.llt||''}</span>
        </div>`;
      });
      html += '</div>';
    }
    if (data.unattributed && data.unattributed.length > 0) {
      html += `<div class="drug-block" style="border-left-color:#64748b"><div class="drug-name" style="color:#94a3b8">Unattributed Reactions</div>`;
      data.unattributed.forEach(e => {
        const lbl = (e.confidence_label||'').replace(' ','-');
        html += `<div class="rxn-row"><span class="rxn-name">${e.reaction}</span><span class="badge ${lbl}">${e.confidence_label||''}</span><span class="score">${(e.confidence*100).toFixed(0)}%</span><span class="reasoning">${e.reasoning||''}</span></div>`;
      });
      html += '</div>';
    }
    document.getElementById('bydrug-list').innerHTML = html;
    bdCard.style.display = 'block';
  } else {
    bdCard.style.display = 'none';
  }
  document.getElementById('results').style.display = 'block';
}
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def ui():
    return _HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    result  = parse_narrative(
        req.narrative,
        analyze=req.analyze,
        model=req.model or DEFAULT_MODEL,
        api_key=api_key,
    )
    return JSONResponse(content=result)


# ── Local dev ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

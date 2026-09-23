"""Local blind labeling web tool for Layer 7 chart-review drafts.

Purpose
-------
Lets a human methodology reviewer label each Layer 7 draft case from the chart
ALONE, without seeing the deterministic engine verdict (no anchoring). The
human judgment is written into `human_confirmed_label` on the draft
`.template.json`, which Layer 9 audits as a NON-BLOCKING draft divergence
(`case_files()` excludes `.template.json`, so the green suite is never broken).
This produces genuine "engine vs expert" ground truth for calibration.

Safety
------
- Read-only with respect to market execution: writes only label/review metadata
  into case JSON; never any order/PnL/outcome field.
- Charts are rendered in BLIND mode (no entry/stop/target or BSU/BPU markers).
- Only writes to `.template.json` drafts, so labels stay non-blocking until a
  human explicitly promotes a case elsewhere.

Run
---
    python knowledge_bot/label_review_server.py
then open the printed http://127.0.0.1:PORT/ URL.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import render_layer7_case_chart as renderer

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "_knowledge_base" / "scenario_review_casebook" / "layer7_real_chart_cases"
CHART_CACHE_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer7_blind_charts"
VERSION = "layer7_blind_label_server_v1"

# Field vocabularies surfaced to the reviewer. Only the expert-meaningful axes
# are exposed; engine-internal counters (working_level_count, structure audit,
# checklist statuses) are NOT asked of a human and are simply omitted from the
# label `expected` dict. Layer 9 compares only fields present in `expected`.
FIELD_OPTIONS: dict[str, list[str]] = {
    "entry_direction": ["long", "short", "none"],
    "scenario_family": ["breakout", "false_breakout", "pullback", "manual_review", "none"],
    "main_level_status": ["working_level", "no_working_level"],
    "entry_status": ["trigger", "setup", "candidate", "no_working_level"],
    "hard_gate_status": ["pass", "reject", "manual_review"],
}
SCALAR_FIELDS = list(FIELD_OPTIONS.keys())
CONFIDENCE_OPTIONS = ["high", "medium", "low"]
SKIP = "(skip)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def case_path(case_id: str) -> Path | None:
    """Resolve a case_id to its draft file, restricted to the cases dir."""
    safe = case_id.strip()
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        return None
    for name in (f"{safe}.template.json", f"{safe}.json"):
        candidate = CASES_DIR / name
        if candidate.exists():
            return candidate
    # Drafts whose filename carries symbol/date (e.g. ...-001-btcusdt-2024-01).
    matches = sorted(CASES_DIR.glob(f"{safe}-*.template.json"))
    return matches[0] if matches else None


def discover_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(CASES_DIR.glob("L7-USER-REAL-*.template.json")):
        try:
            case = load_case(path)
        except (json.JSONDecodeError, OSError):
            continue
        human = case.get("human_confirmed_label") or {}
        rows.append({
            "case_id": case.get("case_id") or path.stem,
            "symbol": case.get("symbol"),
            "title": case.get("title"),
            "has_human_label": bool(human),
            "labeled_at": (human.get("reviewed_at") if isinstance(human, dict) else None),
            "confidence": (human.get("confidence") if isinstance(human, dict) else None),
        })
    # Stable order by case number when possible.
    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        cid = str(row.get("case_id") or "")
        digits = "".join(ch for ch in cid if ch.isdigit())
        return (int(digits) if digits else 10**9, cid)
    return sorted(rows, key=sort_key)


def chart_png(case_id: str, refresh: bool = False) -> Path | None:
    path = case_path(case_id)
    if path is None:
        return None
    case = load_case(path)
    out_path = CHART_CACHE_DIR / f"{case.get('case_id') or case_id}.blind.png"
    if refresh or not out_path.exists():
        renderer.render(case, out_path, execution_tail_bars=384, blind=True)
    return out_path


def write_human_label(case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = case_path(case_id)
    if path is None:
        return {"ok": False, "error": f"case not found: {case_id}"}
    if not path.name.endswith(".template.json"):
        return {"ok": False, "error": "labeling is only allowed on .template.json drafts"}

    reviewed_by = str(payload.get("reviewed_by") or "").strip()
    confidence = str(payload.get("confidence") or "").strip()
    notes = str(payload.get("blind_review_notes") or "").strip()
    if not reviewed_by:
        return {"ok": False, "error": "reviewed_by is required"}
    if confidence not in CONFIDENCE_OPTIONS:
        return {"ok": False, "error": f"confidence must be one of {CONFIDENCE_OPTIONS}"}
    if not notes:
        return {"ok": False, "error": "blind_review_notes is required"}

    incoming = payload.get("expected") or {}
    expected: dict[str, Any] = {}
    for field in SCALAR_FIELDS:
        value = incoming.get(field)
        if value is None:
            continue
        value = str(value).strip()
        if value and value != SKIP:
            expected[field] = value

    price_raw = incoming.get("main_level_price")
    if price_raw not in (None, "", SKIP):
        try:
            expected["main_level_price"] = float(price_raw)
            tol = incoming.get("main_level_tolerance")
            expected["main_level_tolerance"] = float(tol) if tol not in (None, "") else 0.01
        except (TypeError, ValueError):
            return {"ok": False, "error": "main_level_price must be a number"}

    if not expected:
        return {"ok": False, "error": "provide at least one judgment field"}

    case = load_case(path)
    now = utc_now()
    case["human_confirmed_label"] = {
        "label_origin": "human_confirmed_review",
        "confidence": confidence,
        "reviewed_by": reviewed_by,
        "reviewed_at": now,
        "blind_review_notes": notes,
        "expected": expected,
    }
    # Record provenance in human_review without promoting the draft (stays
    # .template.json, so still non-blocking in Layer 9).
    human_review = case.get("human_review") or {}
    human_review.update({
        "reviewed_by": reviewed_by,
        "reviewed_at": now,
        "ohlc_reviewed": True,
        "levels_reviewed": True,
        "expectations_reviewed": True,
    })
    case["human_review"] = human_review

    path.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "case_id": case.get("case_id"), "expected": expected, "reviewed_at": now}


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind labeling — Layer 7</title>
<style>
 :root{--bg:#f6f5f0;--card:#fffffc;--line:#ded9cf;--ink:#22221f;--muted:#66655e;--accent:#1b5ba1;--ok:#1f845a;--warn:#bf483b}
 *{box-sizing:border-box}
 body{margin:0;font:15px/1.45 Segoe UI,system-ui,sans-serif;color:var(--ink);background:var(--bg)}
 .wrap{display:grid;grid-template-columns:300px 1fr;min-height:100vh}
 .side{border-right:1px solid var(--line);background:var(--card);overflow:auto;max-height:100vh}
 .side h1{font-size:15px;margin:14px 14px 6px}
 .side .meta{font-size:12px;color:var(--muted);margin:0 14px 10px}
 .case{padding:8px 14px;border-top:1px solid var(--line);cursor:pointer;font-size:13px}
 .case:hover{background:#f0eee6}
 .case.active{background:#e7eef7}
 .case .cid{font-weight:600}
 .case .sym{color:var(--muted)}
 .case .done{color:var(--ok);font-weight:600}
 .main{padding:18px;overflow:auto;max-height:100vh}
 .chart{width:100%;border:1px solid var(--line);border-radius:6px;background:#fff}
 .title{font-size:16px;font-weight:600;margin:0 0 4px}
 .sub{color:var(--muted);font-size:13px;margin:0 0 14px}
 .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}
 label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
 select,input,textarea{width:100%;padding:7px 8px;border:1px solid var(--line);border-radius:5px;background:#fff;font:inherit}
 textarea{min-height:70px;resize:vertical}
 .full{grid-column:1/3}
 .row{display:flex;gap:10px;align-items:center;margin-top:16px}
 button{padding:9px 18px;border:0;border-radius:6px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
 button.sec{background:#e7e4da;color:var(--ink)}
 .msg{margin-top:10px;font-size:13px}
 .msg.ok{color:var(--ok)} .msg.err{color:var(--warn)}
 .hint{font-size:12px;color:var(--muted);margin-top:2px}
 .who{margin:0 14px 12px}
</style></head>
<body>
<div class="wrap">
 <div class="side">
   <h1>Blind labeling</h1>
   <div class="meta" id="prog">…</div>
   <div class="who"><label>Кто размечает (reviewed_by)</label><input id="reviewer" placeholder="напр. Jafar"></div>
   <div id="list"></div>
 </div>
 <div class="main" id="main"><p class="sub">Выберите кейс слева.</p></div>
</div>
<script>
const FIELD_OPTIONS = __FIELD_OPTIONS__;
const CONF = __CONF__;
const SKIP = "(skip)";
let cases = [], current = null;

function reviewerName(){ return document.getElementById('reviewer').value.trim(); }
try{ const s=localStorage.getItem('reviewer'); if(s) setTimeout(()=>{document.getElementById('reviewer').value=s;},0);}catch(e){}

async function loadCases(){
  const r = await fetch('/api/cases'); cases = await r.json();
  const done = cases.filter(c=>c.has_human_label).length;
  document.getElementById('prog').textContent = `${done}/${cases.length} размечено`;
  const list = document.getElementById('list'); list.innerHTML='';
  for(const c of cases){
    const d = document.createElement('div'); d.className='case'+(current===c.case_id?' active':'');
    d.innerHTML = `<div class="cid">${c.case_id} ${c.has_human_label?'<span class="done">✓</span>':''}</div>`+
                  `<div class="sym">${c.symbol||''} · ${(c.title||'').replace('Draft real chart case: ','')}</div>`;
    d.onclick=()=>openCase(c.case_id);
    list.appendChild(d);
  }
}

async function openCase(id){
  current=id; await loadCases();
  const r = await fetch('/api/case?id='+encodeURIComponent(id)); const c = await r.json();
  const opt = (f)=> [SKIP,...FIELD_OPTIONS[f]].map(o=>`<option ${ (c.expected&&c.expected[f]===o)?'selected':'' }>${o}</option>`).join('');
  const ex = c.expected||{};
  const m = document.getElementById('main');
  m.innerHTML = `
   <p class="title">${c.case_id} — ${c.symbol||''}</p>
   <p class="sub">${c.title||''} · ctx ${c.ctx_bars} баров / exec ${c.exec_bars} баров${c.has_human_label?' · уже размечен (можно переписать)':''}</p>
   <img class="chart" src="/chart?id=${encodeURIComponent(id)}&t=${Date.now()}">
   <div class="grid">
     <div><label>Направление (entry_direction)</label><select id="f_entry_direction">${opt('entry_direction')}</select></div>
     <div><label>Сценарий (scenario_family)</label><select id="f_scenario_family">${opt('scenario_family')}</select></div>
     <div><label>Статус уровня (main_level_status)</label><select id="f_main_level_status">${opt('main_level_status')}</select></div>
     <div><label>Цена рабочего уровня (main_level_price, опц.)</label><input id="f_main_level_price" value="${ex.main_level_price!=null?ex.main_level_price:''}" placeholder="напр. 96000"><div class="hint">tol по умолч. 0.01</div></div>
     <div><label>Готовность входа (entry_status)</label><select id="f_entry_status">${opt('entry_status')}</select></div>
     <div><label>Разрешение (hard_gate_status)</label><select id="f_hard_gate_status">${opt('hard_gate_status')}</select></div>
     <div><label>Уверенность (confidence)</label><select id="f_confidence">${CONF.map(o=>`<option ${ex&&c.confidence===o?'selected':''}>${o}</option>`).join('')}</select></div>
     <div></div>
     <div class="full"><label>Заметки (blind_review_notes, обязательно)</label><textarea id="f_notes" placeholder="почему такое суждение по чарту">${c.blind_review_notes||''}</textarea></div>
   </div>
   <div class="row">
     <button onclick="save()">Сохранить вердикт</button>
     <button class="sec" onclick="nextUnlabeled()">Следующий неразмеченный →</button>
     <span class="msg" id="msg"></span>
   </div>`;
}

async function save(){
  const msg = document.getElementById('msg');
  const rv = reviewerName();
  if(!rv){ msg.className='msg err'; msg.textContent='Впишите имя в поле «Кто размечает» слева'; return; }
  try{ localStorage.setItem('reviewer', rv);}catch(e){}
  const val=(id)=>document.getElementById(id).value;
  const body={ case_id: current, reviewed_by: rv, confidence: val('f_confidence'),
    blind_review_notes: val('f_notes'),
    expected:{ entry_direction:val('f_entry_direction'), scenario_family:val('f_scenario_family'),
      main_level_status:val('f_main_level_status'), entry_status:val('f_entry_status'),
      hard_gate_status:val('f_hard_gate_status'), main_level_price:val('f_main_level_price') } };
  const r= await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const res= await r.json();
  if(res.ok){ msg.className='msg ok'; msg.textContent='Сохранено ✓ ('+Object.keys(res.expected).length+' полей)'; await loadCases(); }
  else{ msg.className='msg err'; msg.textContent='Ошибка: '+res.error; }
}

function nextUnlabeled(){
  const rest = cases.filter(c=>!c.has_human_label);
  if(rest.length){ openCase(rest[0].case_id); } else { document.getElementById('msg').textContent='Все размечены 🎉'; }
}

loadCases();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence default logging
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/":
            html = (PAGE
                    .replace("__FIELD_OPTIONS__", json.dumps(FIELD_OPTIONS, ensure_ascii=False))
                    .replace("__CONF__", json.dumps(CONFIDENCE_OPTIONS)))
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/cases":
            self._json(discover_cases())
            return
        if path == "/api/case":
            case_id = (query.get("id") or [""])[0]
            cpath = case_path(case_id)
            if cpath is None:
                self._json({"error": "not found"}, 404)
                return
            case = load_case(cpath)
            human = case.get("human_confirmed_label") or {}
            self._json({
                "case_id": case.get("case_id"),
                "symbol": case.get("symbol"),
                "title": case.get("title"),
                "ctx_bars": len((case.get("bars") or {}).get("context") or []),
                "exec_bars": len((case.get("bars") or {}).get("execution") or []),
                "has_human_label": bool(human),
                "expected": (human.get("expected") if isinstance(human, dict) else None),
                "confidence": (human.get("confidence") if isinstance(human, dict) else None),
                "blind_review_notes": (human.get("blind_review_notes") if isinstance(human, dict) else None),
            })
            return
        if path == "/chart":
            case_id = (query.get("id") or [""])[0]
            refresh = (query.get("refresh") or ["0"])[0] == "1"
            try:
                png = chart_png(case_id, refresh=refresh)
            except Exception as exc:  # noqa: BLE001 - surface render errors to client
                self._json({"error": f"render failed: {type(exc).__name__}: {exc}"}, 500)
                return
            if png is None or not png.exists():
                self._json({"error": "chart not available"}, 404)
                return
            self._send(200, png.read_bytes(), "image/png")
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/label":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except json.JSONDecodeError:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        result = write_human_label(str(payload.get("case_id") or ""), payload)
        self._json(result, 200 if result.get("ok") else 400)


def find_free_port(preferred: int = 8770) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def serve(open_browser: bool = True) -> None:
    CHART_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    port = find_free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[{VERSION}] blind labeling at {url}")
    print(f"  cases dir : {CASES_DIR}")
    print(f"  cache dir : {CHART_CACHE_DIR}")
    print("  Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(open_browser="--no-browser" not in sys.argv)

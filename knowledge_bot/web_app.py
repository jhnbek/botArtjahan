from __future__ import annotations

import json
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from lecture_terms import expand_query_terms
from make_rag_prompt import build_prompt
from search_lectures import load_chunks, make_excerpt, score_chunks


APP_NAME = "KnowledgeBot"
APP_TITLE = "KnowledgeBot — справочник, ассистент и обучалка по базе лекций"
SAFETY_NOTE = "read-only: no signals, no orders, no PnL, no paper/live trading"

# Read-only safety contract. The web app never trades or generates signals.
SAFETY_FLAGS = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_root(raw_root: str | Path | None = None) -> Path:
    if raw_root:
        return Path(raw_root).expanduser().resolve()
    env_root = os.getenv("KNOWLEDGE_BOT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    base = app_dir()
    for candidate in (base, *base.parents):
        if (candidate / "_knowledge_base").exists():
            return candidate
    return base


def kb_dir(root: Path) -> Path:
    return root / "_knowledge_base"


def long_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class KnowledgeData:
    """Loads and serves the read-only knowledge base content for the web UI."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.kb = kb_dir(root)
        self.index_path = self.kb / "lecture_chunks.jsonl"
        self.chunks: list[dict[str, Any]] = []
        self.course_order: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.index_path.exists():
            self.chunks = load_chunks(self.index_path)
        course_order = load_json(self.kb / "course_order.json")
        if isinstance(course_order, dict):
            self.course_order = course_order.get("lectures", []) or []

    # --- status ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        file_count = 0
        total_bytes = 0
        if self.kb.exists():
            for dirpath, _, filenames in os.walk(long_path(self.kb)):
                for filename in filenames:
                    file_count += 1
                    total_bytes += os.path.getsize(os.path.join(dirpath, filename))

        coverage_status = load_json(
            self.kb / "structured" / "consolidation" / "kb_coverage_audit" / "kb_coverage_audit_status.json"
        )
        layer6_status = load_json(
            self.kb
            / "structured"
            / "consolidation"
            / "layer6_golden_packet_validation"
            / "layer6_golden_packet_validation_status.json"
        )
        counts = (coverage_status.get("counts") or {}) if isinstance(coverage_status, dict) else {}
        coverage = (coverage_status.get("coverage_status_counts") or {}) if isinstance(coverage_status, dict) else {}
        return {
            "app_name": APP_NAME,
            "root": str(self.root),
            "knowledge_base": str(self.kb),
            "kb_exists": self.kb.exists(),
            "kb_files": file_count,
            "kb_mb": round(total_bytes / 1024 / 1024, 2),
            "chunk_count": len(self.chunks),
            "lecture_count": len(self.course_order),
            "canonical": counts,
            "coverage_all_rows": coverage.get("all_rows") or {},
            "layer6": {
                "cases": layer6_status.get("case_count") if isinstance(layer6_status, dict) else None,
                "assertions": layer6_status.get("assertion_count") if isinstance(layer6_status, dict) else None,
                "passed": layer6_status.get("passed_assertion_count") if isinstance(layer6_status, dict) else None,
                "ready": layer6_status.get("layer6_golden_packet_validation_ready") if isinstance(layer6_status, dict) else None,
            },
            "safety_note": SAFETY_NOTE,
            "safety_flags": SAFETY_FLAGS,
        }

    # --- search ---------------------------------------------------------
    def search(self, query: str, top: int) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"query": query, "results": []}
        scored = score_chunks(self.chunks, query)[: max(1, min(top, 50))]
        query_terms = expand_query_terms(query)
        results = []
        for score, chunk in scored:
            frames = chunk.get("frames", []) or []
            results.append(
                {
                    "score": round(score, 2),
                    "lecture_title": chunk.get("lecture_title"),
                    "course_position": chunk.get("course_position"),
                    "chunk_id": chunk.get("chunk_id"),
                    "start_time": chunk.get("start_time"),
                    "end_time": chunk.get("end_time"),
                    "keywords": chunk.get("keywords", [])[:10],
                    "frame_times": [f.get("time") for f in frames[:4]],
                    "excerpt": make_excerpt(chunk.get("text", ""), query_terms, max_chars=700),
                }
            )
        return {"query": query, "results": results}

    # --- learning -------------------------------------------------------
    def lectures(self) -> list[dict[str, Any]]:
        items = []
        for lecture in self.course_order:
            items.append(
                {
                    "course_position": lecture.get("course_position"),
                    "title": lecture.get("title"),
                    "course_kind": lecture.get("course_kind"),
                    "duration": lecture.get("duration"),
                    "chunk_count": lecture.get("chunk_count"),
                    "lecture_id": lecture.get("lecture_id"),
                }
            )
        items.sort(key=lambda item: item.get("course_position") or 0)
        return items

    def lecture_sections(self, lecture_id: str) -> dict[str, Any]:
        sections = [c for c in self.chunks if c.get("lecture_id") == lecture_id]
        sections.sort(key=lambda c: c.get("start_sec") or 0)
        title = sections[0].get("lecture_title") if sections else lecture_id
        return {
            "lecture_id": lecture_id,
            "title": title,
            "sections": [
                {
                    "start_time": c.get("start_time"),
                    "end_time": c.get("end_time"),
                    "keywords": c.get("keywords", [])[:10],
                    "text": c.get("text", ""),
                }
                for c in sections
            ],
        }

    # --- prompt ---------------------------------------------------------
    def prompt(self, question: str, top: int) -> dict[str, Any]:
        question = (question or "").strip()
        if not question:
            return {"question": question, "prompt": ""}
        text = build_prompt(question, self.root, max(1, min(top, 20)), 2200)
        return {"question": question, "prompt": text}

    # --- reports --------------------------------------------------------
    def report_list(self) -> list[dict[str, Any]]:
        candidates = [
            ("Отчёт о сборке базы", self.kb / "build_report.md"),
            ("Инвентаризация знаний", self.kb / "knowledge_inventory.md"),
            ("Покрытие методологии", self.kb / "structured" / "consolidation" / "methodology_coverage_map" / "methodology_coverage_map.md"),
            ("Аудит покрытия KB", self.kb / "structured" / "consolidation" / "kb_coverage_audit" / "kb_coverage_audit.md"),
            ("Валидация Layer 6", self.kb / "structured" / "consolidation" / "layer6_golden_packet_validation" / "layer6_golden_packet_validation.md"),
        ]
        items = []
        for label, path in candidates:
            if path.exists():
                items.append({"label": label, "key": path.name, "rel": str(path.relative_to(self.kb))})
        return items

    def report_content(self, key: str) -> str:
        for item in self.report_list():
            if item["key"] == key:
                path = self.kb / item["rel"]
                if path.exists():
                    return path.read_text(encoding="utf-8")
        return ""


INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>KnowledgeBot</title>
<style>
  :root {
    --bg: #0f1419; --panel: #171d26; --panel2: #1f2733; --line: #2a3340;
    --text: #e6edf3; --muted: #8b97a6; --accent: #4c9aff;
    --good: #3fb950; --warn: #d29922;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: var(--bg); color: var(--text); }
  header { padding: 14px 22px; background: var(--panel); border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 17px; margin: 0; font-weight: 600; }
  .badge { font-size: 11px; color: var(--good); border: 1px solid var(--line); padding: 3px 9px; border-radius: 999px; }
  nav { display: flex; gap: 6px; padding: 10px 22px; background: var(--panel); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  nav button { background: transparent; color: var(--muted); border: 1px solid transparent; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px; }
  nav button:hover { color: var(--text); background: var(--panel2); }
  nav button.active { color: var(--text); background: var(--accent); border-color: var(--accent); }
  main { padding: 22px; max-width: 1080px; margin: 0 auto; }
  .tab { display: none; }
  .tab.active { display: block; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; }
  input[type=text], textarea { width: 100%; background: var(--panel2); color: var(--text); border: 1px solid var(--line); border-radius: 8px; padding: 11px 13px; font-size: 14px; font-family: inherit; }
  textarea { min-height: 90px; resize: vertical; }
  .grow { flex: 1; min-width: 240px; }
  button.primary { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: 11px 20px; cursor: pointer; font-size: 14px; font-weight: 600; }
  button.primary:hover { filter: brightness(1.08); }
  button.ghost { background: var(--panel2); color: var(--text); border: 1px solid var(--line); border-radius: 8px; padding: 9px 14px; cursor: pointer; font-size: 13px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; margin-top: 14px; }
  .card h3 { margin: 0 0 6px; font-size: 15px; }
  .meta { color: var(--muted); font-size: 12.5px; margin-bottom: 8px; }
  .kw { display: inline-block; background: var(--panel2); color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; font-size: 11.5px; margin: 2px 4px 2px 0; }
  .excerpt { font-size: 14px; line-height: 1.55; white-space: pre-wrap; }
  .score { float: right; color: var(--accent); font-size: 12px; font-weight: 600; }
  .hint { color: var(--muted); font-size: 13px; }
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .stat { background: var(--panel2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
  .stat .n { font-size: 22px; font-weight: 700; }
  .stat .l { color: var(--muted); font-size: 12px; margin-top: 3px; }
  .lecture-item { cursor: pointer; }
  .lecture-item:hover { border-color: var(--accent); }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
  pre.report { white-space: pre-wrap; font-family: "Cascadia Code", Consolas, monospace; font-size: 12.5px; line-height: 1.5; background: var(--panel2); border: 1px solid var(--line); border-radius: 10px; padding: 14px; max-height: 70vh; overflow: auto; }
  .safety { color: var(--warn); font-size: 12px; }
  .loading { color: var(--muted); font-size: 13px; padding: 10px 0; }
  a.back { color: var(--accent); cursor: pointer; font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>KnowledgeBot</h1>
  <span class="badge">только чтение базы лекций</span>
  <span class="safety">без сигналов · без ордеров · без торговли</span>
</header>
<nav>
  <button data-tab="search" class="active">Поиск</button>
  <button data-tab="learn">Обучение</button>
  <button data-tab="assistant">Ассистент</button>
  <button data-tab="reports">Отчёты</button>
  <button data-tab="status">Статус</button>
</nav>
<main>
  <section id="tab-search" class="tab active">
    <div class="row">
      <input id="q" class="grow" type="text" placeholder="Например: куда ставить стоп при пробое уровня" />
      <button class="primary" onclick="doSearch()">Искать</button>
    </div>
    <div class="hint" style="margin-top:8px">Поиск идёт по всей базе лекций локально. Enter — искать.</div>
    <div id="search-results"></div>
  </section>

  <section id="tab-learn" class="tab">
    <div class="hint">Лекции по порядку курса. Нажми на лекцию, чтобы открыть её содержание по таймкодам.</div>
    <div id="learn-list"></div>
    <div id="learn-detail"></div>
  </section>

  <section id="tab-assistant" class="tab">
    <div class="hint">Ассистент собирает готовый контекст-промпт из релевантных кусков лекций — его можно скопировать в любой ИИ-чат.</div>
    <textarea id="assistant-q" placeholder="Задай вопрос по методике, например: чем отличается БСУ от БПУ?"></textarea>
    <div class="row" style="margin-top:10px">
      <button class="primary" onclick="doPrompt()">Собрать контекст</button>
      <button class="ghost" onclick="copyPrompt()">Скопировать</button>
    </div>
    <div id="assistant-out"></div>
  </section>

  <section id="tab-reports" class="tab">
    <div class="hint">Технические отчёты о состоянии базы знаний.</div>
    <div id="reports-list" class="chips"></div>
    <div id="reports-content"></div>
  </section>

  <section id="tab-status" class="tab">
    <div id="status-out" class="loading">Загрузка…</div>
  </section>
</main>

<script>
function api(path) { return fetch(path).then(r => r.json()); }
function esc(s) { return (s == null ? '' : String(s)).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

document.querySelectorAll('nav button').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'learn') loadLectures();
    if (btn.dataset.tab === 'reports') loadReports();
    if (btn.dataset.tab === 'status') loadStatus();
  };
});

// ---- search ----
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
function doSearch() {
  const q = document.getElementById('q').value.trim();
  const box = document.getElementById('search-results');
  if (!q) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="loading">Ищу…</div>';
  api('/api/search?q=' + encodeURIComponent(q) + '&top=12').then(data => {
    if (!data.results.length) { box.innerHTML = '<div class="card">Ничего не найдено.</div>'; return; }
    box.innerHTML = data.results.map(r => `
      <div class="card">
        <span class="score">★ ${r.score}</span>
        <h3>${esc(r.lecture_title)}</h3>
        <div class="meta">${esc(r.start_time)} – ${esc(r.end_time)} · ${esc(r.chunk_id)}</div>
        <div>${r.keywords.map(k => '<span class="kw">' + esc(k) + '</span>').join('')}</div>
        <div class="excerpt" style="margin-top:8px">${esc(r.excerpt)}</div>
        ${r.frame_times.length ? '<div class="meta" style="margin-top:8px">Кадры: ' + r.frame_times.map(esc).join(' · ') + '</div>' : ''}
      </div>`).join('');
  });
}

// ---- learn ----
function loadLectures() {
  const list = document.getElementById('learn-list');
  document.getElementById('learn-detail').innerHTML = '';
  if (list.dataset.loaded) return;
  list.innerHTML = '<div class="loading">Загрузка лекций…</div>';
  api('/api/lectures').then(items => {
    list.dataset.loaded = '1';
    list.innerHTML = items.map(it => `
      <div class="card lecture-item" onclick="openLecture('${it.lecture_id}')">
        <h3>${esc(it.title)} <span class="meta">· ${esc(it.duration || '')}</span></h3>
        <div class="meta">${esc(it.course_kind || '')} · разделов: ${it.chunk_count}</div>
      </div>`).join('');
  });
}
function openLecture(id) {
  const det = document.getElementById('learn-detail');
  det.innerHTML = '<div class="loading">Открываю…</div>';
  api('/api/lecture?id=' + encodeURIComponent(id)).then(d => {
    det.innerHTML = `<div class="card"><a class="back" onclick="document.getElementById('learn-detail').innerHTML=''">← закрыть</a>
      <h3 style="margin-top:8px">${esc(d.title)}</h3></div>` +
      d.sections.map(s => `
      <div class="card">
        <div class="meta">${esc(s.start_time)} – ${esc(s.end_time)}</div>
        <div>${s.keywords.map(k => '<span class="kw">' + esc(k) + '</span>').join('')}</div>
        <div class="excerpt" style="margin-top:8px">${esc(s.text)}</div>
      </div>`).join('');
    window.scrollTo(0, 0);
  });
}

// ---- assistant ----
let lastPrompt = '';
function doPrompt() {
  const q = document.getElementById('assistant-q').value.trim();
  const out = document.getElementById('assistant-out');
  if (!q) { out.innerHTML = ''; return; }
  out.innerHTML = '<div class="loading">Собираю контекст…</div>';
  fetch('/api/prompt', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q, top: 8}) })
    .then(r => r.json()).then(d => {
      lastPrompt = d.prompt || '';
      out.innerHTML = '<pre class="report">' + esc(lastPrompt) + '</pre>';
    });
}
function copyPrompt() { if (lastPrompt) navigator.clipboard.writeText(lastPrompt); }

// ---- reports ----
function loadReports() {
  const list = document.getElementById('reports-list');
  if (list.dataset.loaded) return;
  api('/api/reports').then(items => {
    list.dataset.loaded = '1';
    list.innerHTML = items.map(it => `<button class="ghost" onclick="openReport('${it.key}', this)">${esc(it.label)}</button>`).join('');
  });
}
function openReport(key, btn) {
  const out = document.getElementById('reports-content');
  out.innerHTML = '<div class="loading">Загрузка…</div>';
  api('/api/report?key=' + encodeURIComponent(key)).then(d => {
    out.innerHTML = '<pre class="report">' + esc(d.content) + '</pre>';
  });
}

// ---- status ----
function loadStatus() {
  const out = document.getElementById('status-out');
  api('/api/status').then(s => {
    const cov = s.coverage_all_rows || {};
    const covHtml = Object.keys(cov).map(k => `<div class="stat"><div class="n">${cov[k]}</div><div class="l">${esc(k)}</div></div>`).join('');
    out.classList.remove('loading');
    out.innerHTML = `
      <div class="stat-grid">
        <div class="stat"><div class="n">${s.kb_files}</div><div class="l">файлов в базе</div></div>
        <div class="stat"><div class="n">${s.kb_mb} МБ</div><div class="l">размер базы</div></div>
        <div class="stat"><div class="n">${s.chunk_count}</div><div class="l">разделов лекций</div></div>
        <div class="stat"><div class="n">${s.lecture_count}</div><div class="l">лекций</div></div>
      </div>
      <div class="card"><h3>Канонические знания</h3>
        <div class="stat-grid">
          <div class="stat"><div class="n">${s.canonical.crd ?? '—'}</div><div class="l">CRD правила</div></div>
          <div class="stat"><div class="n">${s.canonical.fcd ?? '—'}</div><div class="l">FCD контракты</div></div>
          <div class="stat"><div class="n">${s.canonical.rscd_checklists ?? '—'}</div><div class="l">чек-листы</div></div>
          <div class="stat"><div class="n">${s.canonical.rscd_items ?? '—'}</div><div class="l">пункты чек-листов</div></div>
        </div>
      </div>
      ${covHtml ? '<div class="card"><h3>Покрытие методологии</h3><div class="stat-grid">' + covHtml + '</div></div>' : ''}
      <div class="card"><h3>Безопасность</h3>
        <div class="safety">${esc(s.safety_note)}</div>
        <div class="meta" style="margin-top:6px">База: ${esc(s.knowledge_base)}</div>
      </div>`;
  });
}

loadStatus();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    data: KnowledgeData  # set as class attribute before serving

    def log_message(self, *args: Any) -> None:  # silence default logging
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(self.data.status())
            return
        if path == "/api/search":
            query = (params.get("q", [""])[0])
            top = int(params.get("top", ["12"])[0] or 12)
            self._json(self.data.search(query, top))
            return
        if path == "/api/lectures":
            self._json(self.data.lectures())
            return
        if path == "/api/lecture":
            lecture_id = params.get("id", [""])[0]
            self._json(self.data.lecture_sections(lecture_id))
            return
        if path == "/api/reports":
            self._json(self.data.report_list())
            return
        if path == "/api/report":
            key = params.get("key", [""])[0]
            self._json({"content": self.data.report_content(key)})
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/prompt":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        question = payload.get("question", "")
        top = int(payload.get("top", 8) or 8)
        self._json(self.data.prompt(question, top))


def find_free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
        except OSError:
            continue
    return preferred


def serve(root: Path | None = None, port: int | None = None, open_browser: bool = True) -> None:
    resolved_root = resolve_root(root)
    data = KnowledgeData(resolved_root)
    Handler.data = data
    bind_port = port or find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", bind_port), Handler)
    url = f"http://127.0.0.1:{bind_port}/"
    print(APP_TITLE)
    print(f"Knowledge base: {data.kb}")
    print(f"KB chunks: {len(data.chunks)} | lectures: {len(data.course_order)}")
    print(f"Safety: {SAFETY_NOTE}")
    print("")
    print(f"Открой в браузере: {url}")
    print("Чтобы остановить — закрой это окно или нажми Ctrl+C.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = None
    port = None
    open_browser = True
    for i, token in enumerate(args):
        if token == "--root" and i + 1 < len(args):
            root = Path(args[i + 1])
        elif token == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif token == "--no-browser":
            open_browser = False
    serve(root=root, port=port, open_browser=open_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

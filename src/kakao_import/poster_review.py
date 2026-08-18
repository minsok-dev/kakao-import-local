# [변경사유]: 포스터 분류 결과를 로컬 브라우저에서 확인·수정하는 전용 UI
"""stdlib HTTP 서버: poster-classify 결과 조회 + human 라벨 저장."""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from kakao_import.config import Settings
from kakao_import.db import connect
from kakao_import.logging_util import get_logger
from kakao_import.poster_label import cmd_poster_label
from kakao_import.poster_schema import ensure_poster_schema, list_classify_rows
from kakao_import.similar_detect import resolve_photo_path

log = get_logger(__name__)

STATUS_LABELS = {
    "poster": "포스터",
    "uncertain": "불확실",
    "non_poster": "비포스터",
}
SOURCE_LABELS = {
    "model": "모델",
    "human": "사람",
    "rule": "규칙",
}


def _page_html() -> str:
    """단일 페이지 UI (외부 의존 없음)."""
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Poster 분류 리뷰 (로컬)</title>
<style>
  :root {
    --bg: #f6f4f1; --ink: #1a1a1a; --muted: #5c5c5c; --line: #d8d2c8;
    --card: #fff; --accent: #0f5c4c; --warn: #8a4b12; --bad: #8b2f2f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", sans-serif;
    background: linear-gradient(180deg, #efeae3 0%, var(--bg) 40%); color: var(--ink);
  }
  header {
    padding: 1.2rem 1.5rem 0.8rem; border-bottom: 1px solid var(--line);
    background: rgba(255,255,255,0.75); backdrop-filter: blur(6px);
    position: sticky; top: 0; z-index: 10;
  }
  h1 { margin: 0; font-size: 1.2rem; letter-spacing: -0.02em; }
  .sub { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.9rem; }
  main { padding: 1rem 1.5rem 3rem; max-width: 1240px; margin: 0 auto; }
  .toolbar, .filters, .summary {
    display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-top: 0.8rem;
  }
  button, select, input {
    appearance: none; border: 1px solid var(--line); background: #fff; color: var(--ink);
    border-radius: 8px; padding: 0.45rem 0.75rem; font: inherit;
  }
  button { cursor: pointer; }
  button:hover { border-color: var(--accent); }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button.active { outline: 2px solid var(--accent); }
  .note {
    margin-top: 0.75rem; padding: 0.65rem 0.8rem; background: #fff8e8;
    border: 1px solid #edd9b0; border-radius: 8px; color: var(--warn); font-size: 0.85rem;
  }
  .summary .pill, .badge {
    display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.2rem 0.55rem;
    border-radius: 999px; background: #f0ece6; font-size: 0.8rem;
  }
  .status-poster { background: #e8f5f1; color: var(--accent); }
  .status-uncertain { background: #fff4db; color: var(--warn); }
  .status-non-poster { background: #fde9e9; color: var(--bad); }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 1rem;
    margin-top: 1rem;
  }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden; box-shadow: 0 1px 0 rgba(0,0,0,0.03);
  }
  .thumb {
    width: 100%; aspect-ratio: 1 / 1; object-fit: cover; display: block; background: #eee;
    cursor: zoom-in;
  }
  .body { padding: 0.85rem; }
  .file {
    font-size: 0.82rem; font-weight: 600; word-break: break-all; line-height: 1.35;
  }
  .meta { color: var(--muted); font-size: 0.78rem; margin-top: 0.35rem; line-height: 1.45; }
  .actions { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-top: 0.7rem; }
  .actions button { padding: 0.3rem 0.5rem; font-size: 0.78rem; }
  .empty { color: var(--muted); padding: 2rem 0; text-align: center; }
  .status-line { min-height: 1.2em; font-size: 0.84rem; color: var(--accent); margin-top: 0.65rem; }
  .lightbox {
    display: none; position: fixed; inset: 0; z-index: 100; background: rgba(12,14,16,0.9);
    align-items: center; justify-content: center; padding: 1rem;
  }
  .lightbox.open { display: flex; flex-direction: column; gap: 0.7rem; }
  .lightbox img {
    max-width: min(96vw, 1400px); max-height: calc(100vh - 7rem); object-fit: contain;
    background: #111; border-radius: 6px;
  }
  .lightbox-bar { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; color: #f2f2f2; }
  .lightbox-bar button { background: #1f1f1f; color: #fff; border-color: #444; }
</style>
</head>
<body>
<header>
  <h1>Poster 분류 리뷰</h1>
  <p class="sub">`poster-classify` 결과를 확인하고, 필요하면 사람 판정으로 바로 덮어쓸 수 있습니다.</p>
  <div class="toolbar">
    <button type="button" id="btn-reload" class="primary">새로고침</button>
    <label><input type="checkbox" id="excluded-only"/> 업로드 제외만 보기</label>
  </div>
  <div class="filters">
    <select id="status-filter">
      <option value="">모든 상태</option>
      <option value="poster">poster</option>
      <option value="uncertain">uncertain</option>
      <option value="non_poster">non_poster</option>
    </select>
    <input id="room-filter" type="text" placeholder="room_id 필터 (예: info_latin_korea)"/>
    <button type="button" id="btn-apply">필터 적용</button>
  </div>
  <div class="summary" id="summary"></div>
  <div class="note">`poster`·`uncertain` 은 업로드 후보를 유지하고, `non_poster` 만 업로드 제외됩니다. 사람 판정(`human`)은 모델보다 우선합니다.</div>
</header>
<main>
  <div id="list" class="empty">불러오는 중…</div>
</main>
<div id="lightbox" class="lightbox" aria-hidden="true">
  <img id="lb-img" alt=""/>
  <div class="lightbox-bar">
    <span id="lb-caption"></span>
    <button type="button" id="lb-close">닫기 (Esc)</button>
  </div>
</div>
<script>
const STATUS_LABELS = {poster:"포스터", uncertain:"불확실", non_poster:"비포스터"};
const SOURCE_LABELS = {model:"모델", human:"사람", rule:"규칙"};
let itemsCache = [];
let lightboxItem = null;

function esc(v) {
  return String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}

function fmtScore(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(4) : "-";
}

function clsStatus(s) {
  return "status-" + String(s || "").replaceAll("_", "-");
}

async function load() {
  const params = new URLSearchParams();
  const status = document.getElementById("status-filter").value.trim();
  const room = document.getElementById("room-filter").value.trim();
  const excludedOnly = document.getElementById("excluded-only").checked;
  if (status) params.set("status", status);
  if (room) params.set("room_id", room);
  if (excludedOnly) params.set("excluded_only", "1");
  const res = await fetch("/api/items?" + params.toString());
  const payload = await res.json();
  itemsCache = payload.items || [];
  renderSummary(payload.summary || {});
  renderList(itemsCache);
}

function renderSummary(summary) {
  const el = document.getElementById("summary");
  const total = Number(summary.total || 0);
  el.innerHTML = [
    `<span class="pill">전체 ${total}건</span>`,
    `<span class="pill ${clsStatus("poster")}">poster ${Number(summary.poster || 0)}건</span>`,
    `<span class="pill ${clsStatus("uncertain")}">uncertain ${Number(summary.uncertain || 0)}건</span>`,
    `<span class="pill ${clsStatus("non_poster")}">non_poster ${Number(summary.non_poster || 0)}건</span>`,
    `<span class="pill">제외 ${Number(summary.excluded || 0)}건</span>`,
    `<span class="pill">human ${Number(summary.human || 0)}건</span>`
  ].join("");
}

function renderList(items) {
  const root = document.getElementById("list");
  if (!items.length) {
    root.className = "empty";
    root.textContent = "표시할 poster 분류 결과가 없습니다. 먼저 kakao-import poster-classify 를 실행하세요.";
    return;
  }
  root.className = "grid";
  root.innerHTML = items.map(renderCard).join("");
  bindCardEvents();
}

function renderCard(item) {
  const img = item.photo_id ? `<img class="thumb" src="/photo/${item.photo_id}" alt="${esc(item.file_name)}" data-open-photo="${item.photo_id}"/>` : `<div class="empty">이미지 없음</div>`;
  const excluded = Number(item.excluded_from_upload || 0) === 1;
  return `<section class="card">
    ${img}
    <div class="body">
      <div><span class="badge ${clsStatus(item.status)}">${esc(STATUS_LABELS[item.status] || item.status)}</span></div>
      <div class="file">${esc(item.file_name)}</div>
      <div class="meta">
        room: ${esc(item.room_id)}<br/>
        source: ${esc(SOURCE_LABELS[item.source] || item.source)}<br/>
        score: ${fmtScore(item.poster_score)}<br/>
        upload: ${excluded ? "제외" : "유지"}<br/>
        sha: ${esc(String(item.sha256 || "").slice(0, 12))}...<br/>
        classified_at: ${esc(item.classified_at || "-")}
      </div>
      <div class="actions">
        <button type="button" data-label="poster" data-room="${esc(item.room_id)}" data-sha="${esc(item.sha256)}">poster</button>
        <button type="button" data-label="uncertain" data-room="${esc(item.room_id)}" data-sha="${esc(item.sha256)}">uncertain</button>
        <button type="button" data-label="non_poster" data-room="${esc(item.room_id)}" data-sha="${esc(item.sha256)}">non_poster</button>
      </div>
      <div class="status-line" id="status-${esc(item.room_id)}-${esc(item.sha256)}"></div>
    </div>
  </section>`;
}

function bindCardEvents() {
  document.querySelectorAll("[data-label]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      const b = e.currentTarget;
      const payload = {
        room_id: b.getAttribute("data-room"),
        sha256: b.getAttribute("data-sha"),
        status: b.getAttribute("data-label")
      };
      const statusId = "status-" + payload.room_id + "-" + payload.sha256;
      const line = document.getElementById(statusId);
      if (line) line.textContent = "저장 중…";
      const res = await fetch("/api/label", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(payload)
      });
      const out = await res.json();
      if (line) line.textContent = out.ok ? "저장됨: human/" + payload.status : ("오류: " + (out.error || "save failed"));
      if (out.ok) await load();
    });
  });
  document.querySelectorAll("[data-open-photo]").forEach(img => {
    img.addEventListener("click", (e) => {
      const pid = Number(e.currentTarget.getAttribute("data-open-photo"));
      const item = itemsCache.find(x => Number(x.photo_id || 0) === pid) || null;
      if (!item) return;
      lightboxItem = item;
      document.getElementById("lb-img").src = "/photo/" + pid;
      document.getElementById("lb-caption").textContent = (item.room_id || "") + " / " + (item.file_name || "");
      document.getElementById("lightbox").classList.add("open");
    });
  });
}

function closeLightbox() {
  document.getElementById("lightbox").classList.remove("open");
  document.getElementById("lb-img").src = "";
  lightboxItem = null;
}

document.getElementById("btn-reload").addEventListener("click", load);
document.getElementById("btn-apply").addEventListener("click", load);
document.getElementById("excluded-only").addEventListener("change", load);
document.getElementById("lb-close").addEventListener("click", closeLightbox);
document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") closeLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLightbox();
});
load().catch(err => {
  const root = document.getElementById("list");
  root.className = "empty";
  root.textContent = "불러오기 실패: " + err;
});
</script>
</body>
</html>"""


def create_handler(settings: Settings, root: Path):
    """RequestHandler 클래스 팩토리."""
    photo_paths: dict[int, Path] = {}
    db_lock = threading.Lock()
    schema_ready = {"ok": False}

    def ensure_schema_once() -> None:
        if schema_ready["ok"]:
            return
        with connect(settings.db_path) as conn:
            ensure_poster_schema(conn)
            conn.commit()
        schema_ready["ok"] = True

    def refresh_photo_paths() -> None:
        with connect(settings.db_path) as conn:
            rows = conn.execute(
                "SELECT id, rel_path FROM photo_file WHERE rel_path IS NOT NULL"
            ).fetchall()
        photo_paths.clear()
        for r in rows:
            p = resolve_photo_path(root, str(r["rel_path"]))
            if p is not None:
                photo_paths[int(r["id"])] = p

    def summary_of(items: list[dict[str, Any]]) -> dict[str, int]:
        out = {
            "total": len(items),
            "poster": 0,
            "uncertain": 0,
            "non_poster": 0,
            "excluded": 0,
            "human": 0,
        }
        for item in items:
            status = str(item.get("status") or "")
            if status in out:
                out[status] += 1
            if int(item.get("excluded_from_upload") or 0) == 1:
                out["excluded"] += 1
            if str(item.get("source") or "") == "human":
                out["human"] += 1
        return out

    ensure_schema_once()
    refresh_photo_paths()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            log.info("poster-review %s", fmt % args)

        def _send(
            self,
            code: int,
            body: bytes,
            content_type: str,
            *,
            cache: str = "no-store",
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, code: int, obj: Any) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(code, raw, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self._send(
                    200, _page_html().encode("utf-8"), "text/html; charset=utf-8"
                )
                return
            if path == "/api/items":
                qs = parse_qs(parsed.query)
                room_id = (qs.get("room_id") or [""])[0].strip() or None
                status = (qs.get("status") or [""])[0].strip() or None
                excluded_only = (qs.get("excluded_only") or [""])[0] in ("1", "true")
                with db_lock:
                    ensure_schema_once()
                    with connect(settings.db_path) as conn:
                        items = list_classify_rows(
                            conn,
                            room_id=room_id,
                            status=status,
                            excluded_only=excluded_only,
                        )
                    refresh_photo_paths()
                self._json(200, {"ok": True, "items": items, "summary": summary_of(items)})
                return
            if path.startswith("/photo/"):
                try:
                    pid = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    self._json(400, {"ok": False, "error": "bad photo id"})
                    return
                file_path = photo_paths.get(pid)
                if file_path is None:
                    with db_lock:
                        with connect(settings.db_path) as conn:
                            row = conn.execute(
                                "SELECT rel_path FROM photo_file WHERE id = ?",
                                (pid,),
                            ).fetchone()
                        if row:
                            resolved = resolve_photo_path(root, str(row["rel_path"]))
                            if resolved is not None:
                                photo_paths[pid] = resolved
                                file_path = resolved
                if file_path is None or not file_path.is_file():
                    self._json(404, {"ok": False, "error": "file missing"})
                    return
                data = file_path.read_bytes()
                ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                self._send(200, data, ctype, cache="public, max-age=3600")
                return
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/label":
                self._json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                room_id = str(payload["room_id"])
                sha256 = str(payload["sha256"])
                status = str(payload["status"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": f"bad payload: {exc}"})
                return
            with db_lock:
                ensure_schema_once()
                out = cmd_poster_label(
                    settings,
                    sha256=sha256,
                    status=status,
                    room_id=room_id,
                )
            self._json(200 if out.get("ok") else 400, out)

    return Handler


def run_review_server(
    settings: Settings,
    *,
    root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    open_browser: bool = True,
) -> None:
    """로컬 포스터 리뷰 서버 실행 (블로킹). Ctrl+C 종료."""
    export_root = root or settings.export_root
    if export_root is None:
        raise ValueError("export_root 필요")
    handler = create_handler(settings, Path(export_root))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    log.info("poster-review listening %s db=%s", url, settings.db_path.name)
    print(f"Poster review UI: {url}")
    print("human label only - upload files unchanged. Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()

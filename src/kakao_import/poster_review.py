# [변경사유]: 포스터 분류 결과를 로컬 브라우저에서 확인·수정하는 전용 UI
"""stdlib HTTP 서버: poster-classify 결과 조회 + human 라벨 저장."""

from __future__ import annotations

import json
import mimetypes
import sqlite3
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
from kakao_import.poster_schema import (
    ensure_poster_schema,
    list_classify_rows,
    summarize_classify_rows,
)
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

# [변경사유]: 목록은 긴 변 400px JPEG 캐시. 원본은 라이트박스만
THUMB_MAX_PX = 400
THUMB_DIR_NAME = ".poster_review_thumbs"
# [변경사유]: 한 번에 20장, 스크롤로 이어서 불러온다
PAGE_SIZE = 20
PAGE_SIZE_MAX = 100


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
  .sentinel { height: 1px; }
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
  <div id="sentinel" class="sentinel" aria-hidden="true"></div>
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
const PAGE_SIZE = 20;
let itemsCache = [];
let lastSummary = {};
let lightboxItem = null;
let pageOffset = 0;
let hasMore = false;
let loadingPage = false;
let listBound = false;

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

function currentFilters() {
  return {
    status: document.getElementById("status-filter").value.trim(),
    room: document.getElementById("room-filter").value.trim(),
    excludedOnly: document.getElementById("excluded-only").checked
  };
}

function itemMatchesFilters(item) {
  const f = currentFilters();
  if (f.status && String(item.status || "") !== f.status) return false;
  if (f.room && String(item.room_id || "") !== f.room) return false;
  if (f.excludedOnly && Number(item.excluded_from_upload || 0) !== 1) return false;
  return true;
}

function findCard(room, sha) {
  return document.querySelector(
    '.card[data-room="' + CSS.escape(String(room || "")) + '"][data-sha="' + CSS.escape(String(sha || "")) + '"]'
  );
}

async function load(opts) {
  const append = !!(opts && opts.append);
  if (loadingPage) return;
  loadingPage = true;
  try {
    const params = new URLSearchParams();
    const f = currentFilters();
    if (f.status) params.set("status", f.status);
    if (f.room) params.set("room_id", f.room);
    if (f.excludedOnly) params.set("excluded_only", "1");
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(append ? itemsCache.length : 0));
    const res = await fetch("/api/items?" + params.toString());
    const payload = await res.json();
    const items = payload.items || [];
    lastSummary = payload.summary || {};
    hasMore = !!payload.has_more;
    if (append) itemsCache = itemsCache.concat(items);
    else itemsCache = items;
    pageOffset = itemsCache.length;
    renderSummary(lastSummary);
    if (append) appendCards(items);
    else renderList(itemsCache);
  } finally {
    loadingPage = false;
  }
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
}

function appendCards(items) {
  if (!items.length) return;
  const root = document.getElementById("list");
  if (root.className !== "grid") {
    renderList(itemsCache);
    return;
  }
  root.insertAdjacentHTML("beforeend", items.map(renderCard).join(""));
}

function renderCard(item) {
  const key = esc(item.room_id) + "\\t" + esc(item.sha256);
  const img = item.photo_id ? `<img class="thumb" src="/thumb/${item.photo_id}" alt="${esc(item.file_name)}" loading="lazy" data-open-photo="${item.photo_id}"/>` : `<div class="empty">이미지 없음</div>`;
  const excluded = Number(item.excluded_from_upload || 0) === 1;
  return `<section class="card" data-card-key="${key}" data-room="${esc(item.room_id)}" data-sha="${esc(item.sha256)}">
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

function bumpSummary(prev, next, removed) {
  const s = lastSummary;
  const prevStatus = String(prev.status || "");
  const nextStatus = String(next.status || "");
  if (prevStatus && s[prevStatus] != null) s[prevStatus] = Math.max(0, Number(s[prevStatus] || 0) - 1);
  if (nextStatus && s[nextStatus] != null) s[nextStatus] = Number(s[nextStatus] || 0) + 1;
  const prevEx = Number(prev.excluded_from_upload || 0) === 1;
  const nextEx = Number(next.excluded_from_upload || 0) === 1;
  if (prevEx && !nextEx) s.excluded = Math.max(0, Number(s.excluded || 0) - 1);
  if (!prevEx && nextEx) s.excluded = Number(s.excluded || 0) + 1;
  if (String(prev.source || "") !== "human" && String(next.source || "") === "human") {
    s.human = Number(s.human || 0) + 1;
  }
  if (removed) s.total = Math.max(0, Number(s.total || 0) - 1);
  renderSummary(s);
}

async function onListClick(e) {
  const img = e.target.closest("[data-open-photo]");
  if (img && document.getElementById("list").contains(img)) {
    const pid = Number(img.getAttribute("data-open-photo"));
    const item = itemsCache.find(x => Number(x.photo_id || 0) === pid) || null;
    if (!item) return;
    lightboxItem = item;
    document.getElementById("lb-img").src = "/photo/" + pid;
    document.getElementById("lb-caption").textContent = (item.room_id || "") + " / " + (item.file_name || "");
    document.getElementById("lightbox").classList.add("open");
    return;
  }
  const b = e.target.closest("[data-label]");
  if (!b || !document.getElementById("list").contains(b)) return;
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
  if (!out.ok) {
    if (line) line.textContent = "오류: " + (out.error || "save failed");
    return;
  }
  const idx = itemsCache.findIndex(x => String(x.room_id) === payload.room_id && String(x.sha256) === payload.sha256);
  const prev = idx >= 0 ? itemsCache[idx] : {};
  const next = Object.assign({}, prev, out.row || {}, {
    status: payload.status,
    source: "human",
    excluded_from_upload: payload.status === "non_poster" ? 1 : 0
  });
  const keep = itemMatchesFilters(next);
  bumpSummary(prev, next, !keep);
  if (!keep) {
    if (idx >= 0) itemsCache.splice(idx, 1);
    const card = findCard(payload.room_id, payload.sha256);
    if (card) card.remove();
    if (!itemsCache.length) {
      if (hasMore) await load({append: true});
      else renderList(itemsCache);
    } else if (hasMore && itemsCache.length < PAGE_SIZE) {
      await load({append: true});
    }
    return;
  }
  if (idx >= 0) itemsCache[idx] = next;
  else itemsCache.push(next);
  const card = findCard(payload.room_id, payload.sha256);
  if (card) {
    card.outerHTML = renderCard(next);
    const fresh = findCard(payload.room_id, payload.sha256);
    const freshLine = fresh ? fresh.querySelector(".status-line") : null;
    if (freshLine) freshLine.textContent = "저장됨: human/" + payload.status;
  }
}

function closeLightbox() {
  document.getElementById("lightbox").classList.remove("open");
  document.getElementById("lb-img").src = "";
  lightboxItem = null;
}

function bindListOnce() {
  if (listBound) return;
  listBound = true;
  document.getElementById("list").addEventListener("click", (e) => {
    onListClick(e).catch(err => {
      const line = e.target.closest(".card") && e.target.closest(".card").querySelector(".status-line");
      if (line) line.textContent = "오류: " + err;
    });
  });
}

document.getElementById("btn-reload").addEventListener("click", () => load());
document.getElementById("btn-apply").addEventListener("click", () => load());
document.getElementById("excluded-only").addEventListener("change", () => load());
document.getElementById("lb-close").addEventListener("click", closeLightbox);
document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") closeLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLightbox();
});
bindListOnce();
if ("IntersectionObserver" in window) {
  const io = new IntersectionObserver((entries) => {
    if (!entries.some(en => en.isIntersecting)) return;
    if (hasMore) load({append: true});
  }, {rootMargin: "400px"});
  io.observe(document.getElementById("sentinel"));
}
load().catch(err => {
  const root = document.getElementById("list");
  root.className = "empty";
  root.textContent = "불러오기 실패: " + err;
});
</script>
</body>
</html>"""


def _parse_page_int(raw: str | None, default: int, *, lo: int, hi: int) -> int:
    """쿼리 숫자를 범위 안으로 맞춘다."""
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _thumb_cache_fresh(src: Path, dest: Path) -> bool:
    """원본보다 캐시가 같거나 더 최근이면 재생성하지 않는다."""
    if not dest.is_file():
        return False
    try:
        return dest.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


def _make_thumb_jpeg(src: Path, dest: Path, *, max_px: int = THUMB_MAX_PX) -> bool:
    """긴 변 max_px JPEG 를 dest 에 쓴다. Pillow 없거나 실패면 False."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        log.warning("poster-review thumb skip no Pillow path=%s", src.name)
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            tmp = dest.with_suffix(".tmp.jpg")
            im.save(tmp, "JPEG", quality=82, optimize=True)
            tmp.replace(dest)
        log.info("poster-review thumb wrote id_name=%s bytes=%s", dest.name, dest.stat().st_size)
        return True
    except OSError as exc:
        log.warning("poster-review thumb fail path=%s err=%s", src, exc)
        return False


def create_handler(settings: Settings, root: Path):
    """RequestHandler 클래스 팩토리."""
    photo_paths: dict[int, Path] = {}
    db_lock = threading.Lock()
    schema_ready = {"ok": False}
    thumb_root = Path(root) / THUMB_DIR_NAME
    # [변경사유]: 요청마다 sqlite connect 하지 않고 리뷰 서버 수명 동안 연결 1개
    review_conn = connect(settings.db_path, check_same_thread=False)

    def close_resources() -> None:
        try:
            review_conn.close()
        except sqlite3.Error:
            pass

    def ensure_schema_once() -> None:
        if schema_ready["ok"]:
            return
        ensure_poster_schema(review_conn)
        review_conn.commit()
        schema_ready["ok"] = True

    def resolve_photo_file(pid: int) -> Path | None:
        """photo_id 경로. 전체 photo_file 스캔 없이 한 건만 조회한다."""
        cached = photo_paths.get(pid)
        if cached is not None and cached.is_file():
            return cached
        with db_lock:
            cached = photo_paths.get(pid)
            if cached is not None and cached.is_file():
                return cached
            ensure_schema_once()
            sql = "SELECT rel_path FROM photo_file WHERE id = ?"
            log.info("poster-review query photo_file id=%s sql=%s", pid, sql)
            row = review_conn.execute(sql, (pid,)).fetchone()
            if not row:
                return None
            resolved = resolve_photo_path(root, str(row["rel_path"]))
            if resolved is None:
                return None
            photo_paths[pid] = resolved
            return resolved

    def cache_photo_path_from_row(row: Any) -> None:
        """라벨 후 옮긴 경로만 메모리에 반영한다. photo_file 전체는 다시 읽지 않는다."""
        if not isinstance(row, dict):
            return
        try:
            pid = int(row.get("photo_id") or 0)
        except (TypeError, ValueError):
            return
        rel = row.get("rel_path")
        if not pid or not rel:
            return
        resolved = resolve_photo_path(root, str(rel))
        if resolved is not None:
            photo_paths[pid] = resolved
        else:
            photo_paths.pop(pid, None)

    def thumb_bytes(pid: int, file_path: Path) -> tuple[bytes, str]:
        """캐시 JPEG 또는 원본. DB 잠금 밖에서 디스크만 다룬다."""
        dest = thumb_root / f"{pid}.jpg"
        if _thumb_cache_fresh(file_path, dest):
            return dest.read_bytes(), "image/jpeg"
        if _make_thumb_jpeg(file_path, dest):
            return dest.read_bytes(), "image/jpeg"
        data = file_path.read_bytes()
        ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        return data, ctype

    ensure_schema_once()

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
                limit = _parse_page_int(
                    (qs.get("limit") or [""])[0], PAGE_SIZE, lo=1, hi=PAGE_SIZE_MAX
                )
                offset = _parse_page_int(
                    (qs.get("offset") or [""])[0], 0, lo=0, hi=1_000_000
                )
                with db_lock:
                    ensure_schema_once()
                    log.info(
                        "poster-review query items room=%s status=%s excluded_only=%s limit=%s offset=%s",
                        room_id or "-",
                        status or "-",
                        excluded_only,
                        limit,
                        offset,
                    )
                    summary = summarize_classify_rows(
                        review_conn,
                        room_id=room_id,
                        status=status,
                        excluded_only=excluded_only,
                    )
                    items = list_classify_rows(
                        review_conn,
                        room_id=room_id,
                        status=status,
                        excluded_only=excluded_only,
                        limit=limit,
                        offset=offset,
                    )
                has_more = offset + len(items) < int(summary.get("total") or 0)
                self._json(
                    200,
                    {
                        "ok": True,
                        "items": items,
                        "summary": summary,
                        "limit": limit,
                        "offset": offset,
                        "has_more": has_more,
                    },
                )
                return
            if path.startswith("/photo/") or path.startswith("/thumb/"):
                try:
                    pid = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    self._json(400, {"ok": False, "error": "bad photo id"})
                    return
                file_path = resolve_photo_file(pid)
                if file_path is None or not file_path.is_file():
                    self._json(404, {"ok": False, "error": "file missing"})
                    return
                if path.startswith("/thumb/"):
                    data, ctype = thumb_bytes(pid, file_path)
                else:
                    data = file_path.read_bytes()
                    ctype = (
                        mimetypes.guess_type(file_path.name)[0]
                        or "application/octet-stream"
                    )
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
            # [변경사유]: 잠금이 나도 500 트레이스백 대신 JSON 을 돌려 "저장 중"에 남지 않게 한다
            try:
                with db_lock:
                    ensure_schema_once()
                    out = cmd_poster_label(
                        settings,
                        sha256=sha256,
                        status=status,
                        room_id=room_id,
                        conn=review_conn,
                    )
                    # [변경사유]: 라벨로 경로가 바뀐 한 건만 캐시한다. photo_file 전체 SELECT 없음
                    if out.get("ok"):
                        cache_photo_path_from_row(out.get("row"))
            except sqlite3.OperationalError as exc:
                log.error("poster-review label sqlite error err=%s", exc)
                self._json(503, {"ok": False, "error": str(exc)})
                return
            self._json(200 if out.get("ok") else 400, out)

    Handler.close_resources = staticmethod(close_resources)  # type: ignore[attr-defined]
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
        close_fn = getattr(handler, "close_resources", None)
        if callable(close_fn):
            close_fn()

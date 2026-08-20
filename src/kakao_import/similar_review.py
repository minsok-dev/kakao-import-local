# [변경사유]: Phase 4.1 — 로컬 similar 썸네일 리뷰 UI (decision만, upload 미적용)
"""stdlib HTTP 서버: 그룹 썸네일 확인 + content decision 저장.

upload policy 적용·파일 삭제·자동 병합 없음.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kakao_import.config import Settings
from kakao_import.db import connect
from kakao_import.logging_util import get_logger
from kakao_import.similar_detect import (
    ensure_similar_schema,
    list_similar_groups,
    resolve_photo_path,
    set_similar_group_decision,
)
from kakao_import.similar_policy import upload_policy_for_decision
from kakao_import.upload_state import mark_candidates_needs_rebuild_by_similar_group

log = get_logger(__name__)

DECISION_LABELS = {
    "same_content": "같은 콘텐츠",
    "different_content": "다른 콘텐츠",
    "partial": "부분",
    "deferred": "보류",
}


def _page_html() -> str:
    """단일 페이지 UI (인라인 CSS/JS — 외부 의존 없음)."""
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Similar 그룹 리뷰 (로컬)</title>
<style>
  :root {
    --bg: #f6f4f1;
    --ink: #1a1a1a;
    --muted: #5c5c5c;
    --line: #d8d2c8;
    --card: #fff;
    --accent: #0f5c4c;
    --warn: #8a4b12;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", sans-serif;
    background: linear-gradient(180deg, #efeae3 0%, var(--bg) 40%);
    color: var(--ink);
    min-height: 100vh;
  }
  header {
    padding: 1.25rem 1.5rem 0.75rem;
    border-bottom: 1px solid var(--line);
    background: rgba(255,255,255,0.7);
    backdrop-filter: blur(6px);
    position: sticky; top: 0; z-index: 10;
  }
  h1 { margin: 0; font-size: 1.25rem; letter-spacing: -0.02em; }
  .sub { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.9rem; }
  main { padding: 1rem 1.5rem 3rem; max-width: 1100px; margin: 0 auto; }
  .toolbar { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.75rem; }
  button, .btn {
    appearance: none; border: 1px solid var(--line); background: #fff;
    color: var(--ink); border-radius: 8px; padding: 0.45rem 0.75rem;
    font: inherit; cursor: pointer;
  }
  button:hover { border-color: var(--accent); }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button.active { outline: 2px solid var(--accent); }
  .note {
    margin-top: 0.75rem; padding: 0.65rem 0.8rem; background: #fff8e8;
    border: 1px solid #edd9b0; border-radius: 8px; color: var(--warn); font-size: 0.85rem;
  }
  .group {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 1rem; margin: 1rem 0; box-shadow: 0 1px 0 rgba(0,0,0,0.03);
  }
  .group-head { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
  .meta { color: var(--muted); font-size: 0.85rem; }
  .thumbs { display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 0.9rem 0; }
  .thumb {
    width: 160px; text-align: center;
    border: 2px solid transparent; border-radius: 10px; padding: 0.25rem;
  }
  .thumb.rep { border-color: var(--accent); background: #e8f5f1; }
  .thumb img {
    width: 152px; height: 152px; object-fit: cover; border-radius: 8px;
    background: #eee; display: block; cursor: zoom-in;
  }
  .thumb .name { font-size: 0.7rem; color: var(--muted); margin-top: 0.3rem;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .thumb .thumb-actions { margin-top: 0.35rem; }
  .thumb .thumb-actions button { padding: 0.25rem 0.45rem; font-size: 0.72rem; }
  .actions { display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }
  .badge {
    display: inline-block; padding: 0.15rem 0.45rem; border-radius: 999px;
    background: #eee; font-size: 0.75rem;
  }
  .empty { color: var(--muted); padding: 2rem 0; text-align: center; }
  .status { font-size: 0.85rem; color: var(--accent); min-height: 1.2em; }
  /* [변경사유]: 썸네일 클릭 시 원본 확대 라이트박스 */
  .lightbox {
    display: none; position: fixed; inset: 0; z-index: 100;
    background: rgba(12, 14, 16, 0.88); align-items: center; justify-content: center;
    padding: 1rem;
  }
  .lightbox.open { display: flex; flex-direction: column; gap: 0.75rem; }
  .lightbox-stage {
    flex: 1; min-height: 0; width: 100%; display: flex; align-items: center; justify-content: center;
  }
  .lightbox img {
    max-width: min(96vw, 1400px); max-height: calc(100vh - 7rem);
    object-fit: contain; border-radius: 6px; background: #111;
    box-shadow: 0 8px 40px rgba(0,0,0,0.45);
  }
  .lightbox-bar {
    display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; justify-content: center;
    color: #f2f2f2; font-size: 0.9rem;
  }
  .lightbox-bar button { background: #1f1f1f; color: #fff; border-color: #444; }
  .lightbox-bar button:hover { border-color: #9adbcb; }
  .lightbox-hint { opacity: 0.75; font-size: 0.8rem; }
  /* [변경사유]: partial 서브그룹 편집 */
  .thumb.selected { outline: 2px dashed #0f5c4c; outline-offset: 1px; }
  .thumb .sg-badge {
    display: inline-block; margin-top: 0.2rem; padding: 0.1rem 0.35rem;
    border-radius: 4px; font-size: 0.68rem; color: #fff;
  }
  .partial-bar {
    display: none; margin: 0.5rem 0 0.75rem; padding: 0.65rem 0.75rem;
    background: #eef6f3; border: 1px solid #b7d9ce; border-radius: 8px;
    gap: 0.4rem; flex-wrap: wrap; align-items: center;
  }
  .partial-bar.open { display: flex; }
  .partial-bar .hint { color: var(--muted); font-size: 0.82rem; width: 100%; }
</style>
</head>
<body>
<header>
  <h1>Similar 그룹 리뷰</h1>
  <p class="sub">콘텐츠 관계(decision)만 저장합니다. 업로드 큐·파일 삭제·자동 병합은 하지 않습니다.</p>
  <div class="toolbar">
    <button type="button" id="btn-reload">새로고침</button>
    <span class="meta" id="count"></span>
  </div>
  <div class="note">Phase 4.1 · 썸네일=확대 · 「부분」=같은/다른 혼합 서브그룹 · upload 적용은 4.2+</div>
</header>
<main>
  <div id="list" class="empty">불러오는 중…</div>
</main>
<!-- [변경사유]: 클릭 시 큰 이미지 비교용 라이트박스 -->
<div id="lightbox" class="lightbox" aria-hidden="true">
  <div class="lightbox-stage"><img id="lb-img" alt=""/></div>
  <div class="lightbox-bar">
    <button type="button" id="lb-prev">이전</button>
    <button type="button" id="lb-next">다음</button>
    <span id="lb-caption"></span>
    <button type="button" id="lb-rep" class="primary">이 사진을 대표로</button>
    <button type="button" id="lb-close">닫기 (Esc)</button>
    <span class="lightbox-hint">← → 키로 같은 그룹 이동</span>
  </div>
</div>
<script>
const LABELS = {
  same_content: "같은 콘텐츠",
  different_content: "다른 콘텐츠",
  partial: "부분",
  deferred: "보류"
};
const SG_COLORS = ["#0f5c4c","#8a4b12","#1d4f91","#6b2d5c","#3d6b1f","#7a2e2e"];
const reps = {};
const selected = {}; // gid -> Set(pid)
const draftBundles = {}; // gid -> [{photo_ids, representative_photo_id}]
let partialEditGid = null;
let groupsCache = [];
let lb = { gid: null, index: 0, members: [] };

async function load() {
  const res = await fetch("/api/groups");
  const groups = await res.json();
  groupsCache = groups;
  document.getElementById("count").textContent = "그룹 " + groups.length + "개";
  const root = document.getElementById("list");
  if (!groups.length) {
    root.className = "empty";
    root.textContent = "similar 그룹이 없습니다. 먼저 kakao-import similar-detect 를 실행하세요.";
    return;
  }
  root.className = "";
  root.innerHTML = groups.map(renderGroup).join("");
  groups.forEach(g => {
    reps[g.group_id] = g.representative_photo_id;
    selected[g.group_id] = new Set();
    draftBundles[g.group_id] = [];
    if (g.decision === "partial" && g.subgroups) {
      draftBundles[g.group_id] = g.subgroups
        .filter(s => !s.is_singleton)
        .map(s => ({
          photo_ids: s.photo_ids.slice(),
          representative_photo_id: s.representative_photo_id
        }));
    }
  });
  if (partialEditGid != null) {
    const bar = document.getElementById("partial-bar-"+partialEditGid);
    if (bar) bar.classList.add("open");
    paintPartialThumbs(partialEditGid);
  }
}

function sgColor(key) {
  let h = 0;
  const s = String(key||"");
  for (let i=0;i<s.length;i++) h = (h*31 + s.charCodeAt(i)) >>> 0;
  return SG_COLORS[h % SG_COLORS.length];
}

function renderGroup(g) {
  const thumbs = g.members.map((m, idx) => {
    const isRep = !!m.is_representative || !!m.is_subgroup_rep;
    const sg = m.subgroup_key
      ? `<span class="sg-badge" style="background:${sgColor(m.subgroup_key)}">${esc(m.subgroup_key)}${m.is_subgroup_rep?" ·대표":""}</span>`
      : "";
    return `<div class="thumb ${isRep ? "rep" : ""}" data-gid="${g.group_id}" data-pid="${m.photo_id}"
         style="${m.subgroup_key ? "border-color:"+sgColor(m.subgroup_key) : ""}">
      <img src="/photo/${m.photo_id}" alt="${esc(m.file_name)}" loading="lazy"
           data-open-lb="${g.group_id}" data-idx="${idx}" title="클릭=큰 이미지"/>
      <div class="name">${esc(m.file_name)}</div>
      <div class="meta">#${m.photo_id}${isRep ? " · 대표" : ""}</div>
      ${sg}
      <div class="thumb-actions">
        <button type="button" data-toggle-sel="${g.group_id}" data-pid="${m.photo_id}">선택</button>
        <button type="button" data-set-rep="${g.group_id}" data-pid="${m.photo_id}">대표</button>
      </div>
    </div>`;
  }).join("");
  const btns = ["same_content","different_content","partial","deferred"].map(d => {
    const active = g.decision === d ? "active primary" : "";
    return `<button type="button" class="${active}" data-decide="${g.group_id}" data-decision="${d}">${LABELS[d]}</button>`;
  }).join("");
  const sgSummary = (g.subgroups||[]).length
    ? `<div class="meta">서브그룹: ${(g.subgroups||[]).map(s =>
        s.is_singleton ? ("단독#"+s.photo_ids[0]) : ("묶음["+s.photo_ids.join(",")+"]")
      ).join(" · ")}</div>`
    : "";
  return `<section class="group" id="g-${g.group_id}">
    <div class="group-head">
      <div>
        <strong>그룹 #${g.group_id}</strong> <span class="badge">${esc(g.group_key)}</span>
        <div class="meta">멤버 ${g.member_count} · distance≤${g.max_distance}
          · decision=<b>${LABELS[g.decision]||g.decision}</b>
          · policy=<code>${esc(g.upload_policy)}</code></div>
        ${sgSummary}
      </div>
      <div class="status" id="st-${g.group_id}"></div>
    </div>
    <div class="partial-bar" id="partial-bar-${g.group_id}">
      <span class="hint">같은 콘텐츠끼리 「선택」후 묶기. 나머지는 저장 시 단독. (최소 묶음 1개)</span>
      <button type="button" data-bundle="${g.group_id}">선택 묶기</button>
      <button type="button" data-clear-sel="${g.group_id}">선택 해제</button>
      <button type="button" data-reset-bundles="${g.group_id}">묶음 초기화</button>
      <button type="button" class="primary" data-save-partial="${g.group_id}">부분 저장</button>
      <button type="button" data-cancel-partial="${g.group_id}">취소</button>
      <span class="meta" id="partial-info-${g.group_id}"></span>
    </div>
    <div class="thumbs">${thumbs}</div>
    <div class="actions">${btns}</div>
  </section>`;
}

function esc(s) {
  return String(s||"").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function setRep(gid, pid) {
  reps[gid] = Number(pid);
  const section = document.getElementById("g-"+gid);
  if (section) {
    section.querySelectorAll(".thumb").forEach(el => {
      el.classList.toggle("rep", Number(el.dataset.pid) === Number(pid));
    });
  }
  const st = document.getElementById("st-"+gid);
  if (st) st.textContent = "대표 #" + pid + " 선택됨 (decision 저장 시 반영)";
}

function paintPartialThumbs(gid) {
  const bundled = new Set();
  (draftBundles[gid]||[]).forEach(b => b.photo_ids.forEach(id => bundled.add(Number(id))));
  const sel = selected[gid] || new Set();
  const section = document.getElementById("g-"+gid);
  if (!section) return;
  section.querySelectorAll(".thumb").forEach(el => {
    const pid = Number(el.dataset.pid);
    el.classList.toggle("selected", sel.has(pid));
    el.style.opacity = bundled.has(pid) ? "0.55" : "1";
  });
  const info = document.getElementById("partial-info-"+gid);
  if (info) {
    const bundles = (draftBundles[gid]||[]).map(b => "["+b.photo_ids.join(",")+"]").join(" ");
    info.textContent = "선택 " + sel.size + " · 묶음 " + (draftBundles[gid]||[]).length + (bundles ? " "+bundles : "");
  }
}

function enterPartialEdit(gid) {
  partialEditGid = gid;
  document.querySelectorAll(".partial-bar").forEach(el => el.classList.remove("open"));
  const bar = document.getElementById("partial-bar-"+gid);
  if (bar) bar.classList.add("open");
  selected[gid] = selected[gid] || new Set();
  draftBundles[gid] = draftBundles[gid] || [];
  paintPartialThumbs(gid);
  const st = document.getElementById("st-"+gid);
  if (st) st.textContent = "부분 편집: 같은 콘텐츠를 선택해 묶은 뒤 「부분 저장」";
}

function openLightbox(gid, idx) {
  const g = groupsCache.find(x => x.group_id === Number(gid));
  if (!g || !g.members.length) return;
  lb.gid = Number(gid);
  lb.members = g.members;
  lb.index = Math.max(0, Math.min(idx, g.members.length - 1));
  renderLightbox();
  const box = document.getElementById("lightbox");
  box.classList.add("open");
  box.setAttribute("aria-hidden", "false");
}

function renderLightbox() {
  const m = lb.members[lb.index];
  if (!m) return;
  const img = document.getElementById("lb-img");
  img.src = "/photo/" + m.photo_id;
  img.alt = m.file_name || "";
  document.getElementById("lb-caption").textContent =
    "#" + m.photo_id + " · " + (m.file_name || "") +
    " (" + (lb.index + 1) + "/" + lb.members.length + ")" +
    (Number(reps[lb.gid]) === Number(m.photo_id) ? " · 대표" : "");
}

function closeLightbox() {
  const box = document.getElementById("lightbox");
  box.classList.remove("open");
  box.setAttribute("aria-hidden", "true");
  document.getElementById("lb-img").removeAttribute("src");
}

function lbStep(delta) {
  if (!lb.members.length) return;
  lb.index = (lb.index + delta + lb.members.length) % lb.members.length;
  renderLightbox();
}

async function postDecide(body, gid) {
  const st = document.getElementById("st-"+gid);
  st.textContent = "저장 중…";
  const res = await fetch("/api/decide", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const out = await res.json();
  if (!out.ok) { st.textContent = "실패: " + (out.error||res.status); return null; }
  st.textContent = "저장됨 · " + (LABELS[out.decision]||out.decision) + " → " + out.upload_policy;
  applyDecideToUi(gid, out);
  return out;
}

function applyDecideToUi(gid, out) {
  const g = groupsCache.find(x => x.group_id === gid);
  if (g) {
    g.decision = out.decision;
    g.upload_policy = out.upload_policy;
    g.subgroups = out.subgroups || [];
    if (out.representative_photo_id != null) {
      g.representative_photo_id = out.representative_photo_id;
      reps[gid] = Number(out.representative_photo_id);
    }
    if (out.decision === "partial" && out.subgroups) {
      const byId = {};
      out.subgroups.forEach(s => s.photo_ids.forEach(pid => {
        byId[pid] = { key: s.subgroup_key, rep: s.representative_photo_id, solo: s.is_singleton };
      }));
      g.members.forEach(m => {
        const info = byId[m.photo_id];
        m.subgroup_key = info ? info.key : null;
        m.is_subgroup_rep = info ? Number(info.rep) === Number(m.photo_id) : false;
        m.is_representative = m.is_subgroup_rep;
      });
      draftBundles[gid] = out.subgroups.filter(s => !s.is_singleton).map(s => ({
        photo_ids: s.photo_ids.slice(),
        representative_photo_id: s.representative_photo_id
      }));
    } else {
      g.members.forEach(m => { m.subgroup_key = null; m.is_subgroup_rep = false; });
      draftBundles[gid] = [];
    }
  }
  const section = document.getElementById("g-"+gid);
  if (!section) return;
  // 메타·버튼·배지 갱신을 위해 해당 그룹만 재렌더(이미지는 캐시)
  if (g) {
    const html = renderGroup(g);
    section.outerHTML = html;
    if (out.decision === "partial") {
      partialEditGid = null;
      const bar = document.getElementById("partial-bar-"+gid);
      if (bar) bar.classList.remove("open");
    }
  }
}

document.getElementById("btn-reload").onclick = () => { partialEditGid = null; load(); };
document.getElementById("lb-close").onclick = closeLightbox;
document.getElementById("lb-prev").onclick = () => lbStep(-1);
document.getElementById("lb-next").onclick = () => lbStep(1);
document.getElementById("lb-rep").onclick = () => {
  const m = lb.members[lb.index];
  if (!m || lb.gid == null) return;
  setRep(lb.gid, m.photo_id);
  renderLightbox();
};
document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox" || e.target.classList.contains("lightbox-stage")) closeLightbox();
});
document.addEventListener("keydown", (e) => {
  const open = document.getElementById("lightbox").classList.contains("open");
  if (!open) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") lbStep(-1);
  if (e.key === "ArrowRight") lbStep(1);
});

document.getElementById("list").addEventListener("click", async (e) => {
  const openEl = e.target.closest("[data-open-lb]");
  if (openEl) {
    openLightbox(openEl.dataset.openLb, Number(openEl.dataset.idx));
    return;
  }
  const tog = e.target.closest("[data-toggle-sel]");
  if (tog) {
    const gid = Number(tog.dataset.toggleSel);
    const pid = Number(tog.dataset.pid);
    if (!selected[gid]) selected[gid] = new Set();
    if (selected[gid].has(pid)) selected[gid].delete(pid);
    else selected[gid].add(pid);
    if (partialEditGid !== gid) enterPartialEdit(gid);
    paintPartialThumbs(gid);
    return;
  }
  const repBtn = e.target.closest("[data-set-rep]");
  if (repBtn) {
    setRep(repBtn.dataset.setRep, repBtn.dataset.pid);
    return;
  }
  const bundleBtn = e.target.closest("[data-bundle]");
  if (bundleBtn) {
    const gid = Number(bundleBtn.dataset.bundle);
    const sel = [...(selected[gid]||[])];
    if (sel.length < 2) {
      document.getElementById("st-"+gid).textContent = "묶으려면 2장 이상 선택";
      return;
    }
    const bundled = new Set();
    (draftBundles[gid]||[]).forEach(b => b.photo_ids.forEach(id => bundled.add(Number(id))));
    if (sel.some(id => bundled.has(id))) {
      document.getElementById("st-"+gid).textContent = "이미 묶인 사진이 포함됨 — 묶음 초기화 후 다시";
      return;
    }
    const rep = (reps[gid] && sel.includes(Number(reps[gid]))) ? Number(reps[gid]) : sel[0];
    draftBundles[gid] = draftBundles[gid] || [];
    draftBundles[gid].push({ photo_ids: sel, representative_photo_id: rep });
    selected[gid] = new Set();
    paintPartialThumbs(gid);
    document.getElementById("st-"+gid).textContent = "묶음 추가됨 [" + sel.join(",") + "]";
    return;
  }
  if (e.target.closest("[data-clear-sel]")) {
    const gid = Number(e.target.closest("[data-clear-sel]").dataset.clearSel);
    selected[gid] = new Set();
    paintPartialThumbs(gid);
    return;
  }
  if (e.target.closest("[data-reset-bundles]")) {
    const gid = Number(e.target.closest("[data-reset-bundles]").dataset.resetBundles);
    draftBundles[gid] = [];
    selected[gid] = new Set();
    paintPartialThumbs(gid);
    return;
  }
  if (e.target.closest("[data-cancel-partial]")) {
    const gid = Number(e.target.closest("[data-cancel-partial]").dataset.cancelPartial);
    partialEditGid = null;
    const bar = document.getElementById("partial-bar-"+gid);
    if (bar) bar.classList.remove("open");
    document.getElementById("st-"+gid).textContent = "";
    return;
  }
  const savePartial = e.target.closest("[data-save-partial]");
  if (savePartial) {
    const gid = Number(savePartial.dataset.savePartial);
    const g = groupsCache.find(x => x.group_id === gid);
    if (!g) return;
    const bundles = draftBundles[gid] || [];
    if (!bundles.length) {
      document.getElementById("st-"+gid).textContent = "최소 1개 묶음 필요 (아니면 「다른 콘텐츠」)";
      return;
    }
    const used = new Set();
    bundles.forEach(b => b.photo_ids.forEach(id => used.add(Number(id))));
    const subgroups = bundles.map(b => ({
      photo_ids: b.photo_ids,
      representative_photo_id: b.representative_photo_id
    }));
    g.members.forEach(m => {
      if (!used.has(Number(m.photo_id))) {
        subgroups.push({ photo_ids: [m.photo_id], representative_photo_id: m.photo_id });
      }
    });
    await postDecide({ group_id: gid, decision: "partial", subgroups }, gid);
    return;
  }
  const t = e.target.closest("[data-decide]");
  if (!t) return;
  const gid = Number(t.dataset.decide);
  const decision = t.dataset.decision;
  if (decision === "partial") {
    enterPartialEdit(gid);
    return;
  }
  await postDecide({
    group_id: gid,
    decision,
    representative_photo_id: reps[gid] || null
  }, gid);
});

load();
</script>
</body>
</html>
"""


def create_handler(settings: Settings, root: Path):
    """RequestHandler 클래스 팩토리."""
    # [변경사유]: 사진 경로 메모리 캐시 + DB 락 — 동시 /photo 가 SQLite 잠금을 유발하지 않게
    photo_paths: dict[int, Path] = {}
    db_lock = threading.Lock()
    schema_ready = {"ok": False}

    def ensure_schema_once() -> None:
        if schema_ready["ok"]:
            return
        with connect(settings.db_path) as conn:
            ensure_similar_schema(conn)
            conn.commit()
        schema_ready["ok"] = True

    def refresh_photo_paths() -> None:
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT id, rel_path FROM photo_file").fetchall()
        photo_paths.clear()
        for r in rows:
            p = resolve_photo_path(root, str(r["rel_path"]))
            if p is not None:
                photo_paths[int(r["id"])] = p

    ensure_schema_once()
    refresh_photo_paths()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            log.info("similar-review %s", fmt % args)

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
                # 브라우저가 로딩 중 취소(라이트박스 전환 등)
                pass

        def _json(self, code: int, obj: Any) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(code, raw, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                html = _page_html().encode("utf-8")
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/groups":
                with db_lock:
                    ensure_schema_once()
                    with connect(settings.db_path) as conn:
                        groups = list_similar_groups(conn)
                    refresh_photo_paths()
                self._json(200, groups)
                return
            if path.startswith("/photo/"):
                try:
                    pid = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    self._json(400, {"ok": False, "error": "bad photo id"})
                    return
                file_path = photo_paths.get(pid)
                if file_path is None:
                    # 캐시 미스 시 1회 DB 조회
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
                ctype = (
                    mimetypes.guess_type(file_path.name)[0]
                    or "application/octet-stream"
                )
                # 썸네일/라이트박스 재요청 시 디스크·DB 부담 감소
                self._send(200, data, ctype, cache="public, max-age=3600")
                return
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/decide":
                self._json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "invalid json"})
                return
            try:
                group_id = int(payload["group_id"])
                decision = str(payload["decision"])
                rep = payload.get("representative_photo_id")
                rep_id = int(rep) if rep is not None else None
                subgroups = payload.get("subgroups")
            except (KeyError, TypeError, ValueError) as exc:
                self._json(400, {"ok": False, "error": f"bad payload: {exc}"})
                return
            with db_lock:
                ensure_schema_once()
                with connect(settings.db_path) as conn:
                    try:
                        out = set_similar_group_decision(
                            conn,
                            group_id=group_id,
                            decision=decision,
                            representative_photo_id=rep_id,
                            subgroups=subgroups if isinstance(subgroups, list) else None,
                        )
                    except ValueError as exc:
                        self._json(400, {"ok": False, "error": str(exc)})
                        return
                    conn.commit()
            out["ok"] = True
            out["upload_policy"] = upload_policy_for_decision(out["decision"])
            out["rebuild_marked"] = mark_candidates_needs_rebuild_by_similar_group(
                settings.db_path,
                group_id=group_id,
                reason=f"similar_review:{out['decision']}",
            )
            log.info(
                "similar-review decide group=%s decision=%s policy=%s rebuild=%s",
                group_id,
                out["decision"],
                out["upload_policy"],
                out["rebuild_marked"],
            )
            self._json(200, out)

    return Handler


def run_review_server(
    settings: Settings,
    *,
    root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """로컬 리뷰 서버 실행 (블로킹). Ctrl+C 종료."""
    export_root = root or settings.export_root
    if export_root is None:
        raise ValueError("export_root 필요")
    handler = create_handler(settings, Path(export_root))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    log.info("similar-review listening %s db=%s", url, settings.db_path.name)
    print(f"Similar review UI: {url}")
    print("decision only - upload queue unchanged. Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()

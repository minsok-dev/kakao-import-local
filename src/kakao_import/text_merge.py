# [변경사유]: Phase2 — exact SHA 텍스트 collapse/merge/conflict (safe|balanced|auto)
"""텍스트 병합."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from kakao_import.normalize import normalize_for_compare

MergeMode = Literal["safe", "balanced", "auto"]

_URL_RE = re.compile(r"https?://[^\s\]）)]+", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?:\d{4}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2}일?|\d{1,2}\s*월\s*\d{1,2}\s*일)"
)


@dataclass
class TextBundle:
    """한 사진(또는 멤버)에 연결된 설명 텍스트들."""

    photo_id: int
    parts: list[dict[str, Any]] = field(default_factory=list)  # message_id, body_raw, body_norm, abs_time

    def joined_raw(self) -> str:
        return "\n".join(p["body_raw"] for p in self.parts if p.get("body_raw"))

    def joined_norm(self) -> str:
        return normalize_for_compare(self.joined_raw())


@dataclass
class MergeResult:
    """한 SHA 그룹 merge 결과."""

    sha256: str
    mode: str
    decision: str
    review_required: bool
    merged_text: str | None
    merged_norm: str | None
    before: list[dict[str, Any]]
    after: dict[str, Any] | None
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


def _unique_sentences(texts: list[str]) -> list[str]:
    """문장/줄 단위 고유 유지(등장 순)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        for line in t.replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if not s:
                continue
            key = normalize_for_compare(s)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


def _extract_urls(text: str) -> set[str]:
    return {m.group(0).rstrip(".,;") for m in _URL_RE.finditer(text)}


def _extract_dates(text: str) -> set[str]:
    return {normalize_for_compare(m.group(0)) for m in _DATE_RE.finditer(text)}


def detect_conflicts(bundles: list[TextBundle]) -> list[dict[str, Any]]:
    """중요 충돌: URL·날짜 표기가 서로 모순될 때."""
    conflicts: list[dict[str, Any]] = []
    url_sets = [_extract_urls(b.joined_raw()) for b in bundles if b.joined_raw()]
    date_sets = [_extract_dates(b.joined_raw()) for b in bundles if b.joined_raw()]
    if len(url_sets) >= 2:
        base = url_sets[0]
        for u in url_sets[1:]:
            if u and base and u != base and not u.issubset(base) and not base.issubset(u):
                conflicts.append(
                    {
                        "field_name": "url",
                        "values_json": json.dumps([sorted(x) for x in url_sets], ensure_ascii=False),
                    }
                )
                break
    if len(date_sets) >= 2:
        base = date_sets[0]
        for d in date_sets[1:]:
            if d and base and d != base and not d.issubset(base) and not base.issubset(d):
                conflicts.append(
                    {
                        "field_name": "date",
                        "values_json": json.dumps([sorted(x) for x in date_sets], ensure_ascii=False),
                    }
                )
                break
    return conflicts


def merge_exact_texts(sha256: str, bundles: list[TextBundle], mode: MergeMode) -> MergeResult:
    """
    exact SHA 멤버들의 그룹 설명 병합.
    similar는 Phase4 — 여기서 다루지 않음.
    """
    before = [
        {
            "photo_id": b.photo_id,
            "text": b.joined_raw(),
            "norm": b.joined_norm(),
            "parts": b.parts,
        }
        for b in bundles
    ]
    sources: list[dict[str, Any]] = []
    for b in bundles:
        for i, p in enumerate(b.parts):
            sources.append(
                {
                    "photo_id": b.photo_id,
                    "message_id": p.get("message_id"),
                    "body_raw": p.get("body_raw") or "",
                    "body_norm": p.get("body_norm"),
                    "abs_time": p.get("abs_time"),
                    "seq_in_source": i,
                }
            )

    norms = [b.joined_norm() for b in bundles if b.joined_norm()]
    if not norms:
        return MergeResult(
            sha256=sha256,
            mode=mode,
            decision="skipped",
            review_required=False,
            merged_text=None,
            merged_norm=None,
            before=before,
            after=None,
            sources=sources,
        )

    all_same = len(set(norms)) == 1
    if all_same:
        text = next(b.joined_raw() for b in bundles if b.joined_raw())
        return MergeResult(
            sha256=sha256,
            mode=mode,
            decision="collapse",
            review_required=False,
            merged_text=text,
            merged_norm=normalize_for_compare(text),
            before=before,
            after={"merged_text": text, "decision": "collapse"},
            sources=sources,
        )

    # 서로 다른 텍스트
    if mode == "safe":
        return MergeResult(
            sha256=sha256,
            mode=mode,
            decision="review",
            review_required=True,
            merged_text=None,
            merged_norm=None,
            before=before,
            after={"decision": "review", "reason": "safe_mode_diff"},
            conflicts=[{"field_name": "text", "values_json": json.dumps(norms, ensure_ascii=False)}],
            sources=sources,
        )

    # balanced / auto — 고유 문장 병합 (auto여도 similar 자동통합 없음; exact만)
    merged_lines = _unique_sentences([b.joined_raw() for b in bundles])
    merged_text = "\n".join(merged_lines)
    conflicts = detect_conflicts(bundles)
    review = len(conflicts) > 0
    decision = "review" if review else "merged"
    return MergeResult(
        sha256=sha256,
        mode=mode,
        decision=decision,
        review_required=review,
        merged_text=merged_text,
        merged_norm=normalize_for_compare(merged_text),
        before=before,
        after={
            "merged_text": merged_text,
            "decision": decision,
            "mode": mode,
        },
        conflicts=conflicts,
        sources=sources,
    )

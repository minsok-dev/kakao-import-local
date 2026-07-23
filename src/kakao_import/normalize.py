# [변경사유]: Phase1 — 비교용 정규화(원문 보존, 이모지 유지·공백만 정리)
"""텍스트 정규화."""

from __future__ import annotations

import re


def normalize_for_compare(text: str) -> str:
    """비교용: 줄바꿈→공백, 연속 공백 축소. 이모지·특수문자 유지."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\n", " ")
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t

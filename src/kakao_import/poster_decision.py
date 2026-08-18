# [변경사유]: 점수→status. 임계값은 학습 json, 코드 상수 0.9 금지
"""포스터 점수 판정."""

from __future__ import annotations

from kakao_import.poster_const import (
    STATUS_NON_POSTER,
    STATUS_POSTER,
    STATUS_UNCERTAIN,
)


def decide_status(
    poster_score: float,
    *,
    exclude_threshold: float,
    poster_threshold: float,
) -> str:
    """
    poster_score = P(포스터).
    score < exclude_threshold → non_poster (업로드 제외)
    score >= poster_threshold → poster
    사이 → uncertain (통과)
    """
    if poster_score < exclude_threshold:
        return STATUS_NON_POSTER
    if poster_score >= poster_threshold:
        return STATUS_POSTER
    return STATUS_UNCERTAIN


def excluded_flag(status: str) -> int:
    """non_poster 만 업로드 제외."""
    return 1 if status == STATUS_NON_POSTER else 0


def pick_thresholds(test_poster_scores: list[float]) -> tuple[float, float, dict]:
    """
    test 포스터를 하나도 제외하지 않는 exclude_threshold.
    poster_threshold 는 그 위 밴드 (uncertain 구간).
    """
    meta: dict = {
        "test_poster_n": len(test_poster_scores),
        "min_test_poster_score": None,
    }
    if not test_poster_scores:
        # 테스트 포스터가 없으면 제외를 거의 안 함 (fail-open 쪽)
        meta["note"] = "no_test_poster_scores"
        return 0.0, 0.5, meta
    lo = float(min(test_poster_scores))
    meta["min_test_poster_score"] = lo
    exclude_threshold = lo
    poster_threshold = min(0.99, max(0.5, exclude_threshold + 0.10))
    if poster_threshold <= exclude_threshold:
        poster_threshold = min(0.99, exclude_threshold + 1e-6)
    return exclude_threshold, poster_threshold, meta

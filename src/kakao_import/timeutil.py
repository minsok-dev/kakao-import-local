# [변경사유]: Phase1 — 오전/오후·일자 헤더 → 절대시각
"""시각 변환."""

from __future__ import annotations

from datetime import date, datetime, time


def korean_ampm_to_time(ampm: str, hour: int, minute: int) -> time:
    """
    카카오 표기 → time.
    오전 12시 = 00시, 오후 12시 = 12시.
    """
    if hour < 1 or hour > 12:
        raise ValueError(f"invalid hour: {hour}")
    if ampm == "오전":
        h24 = 0 if hour == 12 else hour
    elif ampm == "오후":
        h24 = 12 if hour == 12 else hour + 12
    else:
        raise ValueError(f"invalid ampm: {ampm}")
    return time(h24, minute, 0)


def combine_abs(d: date, ampm: str, hour: int, minute: int) -> datetime:
    """날짜 + 오전/오후 → datetime (초=0)."""
    return datetime.combine(d, korean_ampm_to_time(ampm, hour, minute))


def parse_iso(s: str | None) -> datetime | None:
    """ISO 문자열 → datetime."""
    if not s:
        return None
    return datetime.fromisoformat(s)


def minute_key(dt: datetime) -> tuple[int, int, int, int, int]:
    """(y,m,d,H,M) 버킷."""
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute)

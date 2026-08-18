# [변경사유]: SHA·앨범·aHash 그룹 단위 split — 파일 무작위 금지
"""학습 샘플 그룹 분할."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kakao_import.logging_util import get_logger
from kakao_import.photo_name import kakao_album_stem_and_seq, parse_kakaotalk_filename
from kakao_import.poster_const import (
    AHASH_MAX_DISTANCE,
    RANDOM_SEED,
    TEST_GROUP_FRACTION,
)
from kakao_import.similar_cluster import hamming_hex

log = get_logger(__name__)


@dataclass
class LabeledImage:
    """dataset 한 장."""

    path: Path
    sha256: str
    label: int  # 1=poster, 0=non_poster
    album_stem: str | None
    name_time: datetime | None
    ahash_hex: str | None


def album_and_time(file_name: str) -> tuple[str | None, datetime | None]:
    """KakaoTalk 파일명에서 앨범 스템·시각."""
    stem, _seq = kakao_album_stem_and_seq(file_name)
    parsed = parse_kakaotalk_filename(file_name)
    return stem, parsed.name_time if parsed.ok else None


def _union_find(n: int) -> tuple[list[int], callable, callable]:
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    return parent, find, union


def assign_groups(samples: list[LabeledImage]) -> list[int]:
    """
    같은 SHA / 같은 앨범 스템 / 가까운 aHash 는 한 그룹.
    반환: 샘플 인덱스 → 그룹 루트 id
    """
    n = len(samples)
    _parent, find, union = _union_find(n)
    by_sha: dict[str, int] = {}
    by_stem: dict[str, int] = {}
    for i, s in enumerate(samples):
        sha = (s.sha256 or "").lower()
        if sha:
            if sha in by_sha:
                union(i, by_sha[sha])
            else:
                by_sha[sha] = i
        if s.album_stem:
            if s.album_stem in by_stem:
                union(i, by_stem[s.album_stem])
            else:
                by_stem[s.album_stem] = i
    # aHash 근사 중복 (n이 수백이라 O(n^2) 허용)
    with_hash = [(i, s.ahash_hex) for i, s in enumerate(samples) if s.ahash_hex]
    for a in range(len(with_hash)):
        i, ha = with_hash[a]
        for b in range(a + 1, len(with_hash)):
            j, hb = with_hash[b]
            try:
                dist = hamming_hex(ha, hb)
            except ValueError:
                continue
            if dist <= AHASH_MAX_DISTANCE:
                union(i, j)
    return [find(i) for i in range(n)]


def split_train_test(
    samples: list[LabeledImage],
    *,
    test_fraction: float = TEST_GROUP_FRACTION,
    seed: int = RANDOM_SEED,
) -> tuple[list[int], list[int]]:
    """
    그룹 단위 train/test. 가능하면 날짜(과거 train, 최근 test).
    반환: train 인덱스, test 인덱스
    """
    if not samples:
        return [], []
    group_ids = assign_groups(samples)
    groups: dict[int, list[int]] = {}
    for idx, gid in enumerate(group_ids):
        groups.setdefault(gid, []).append(idx)

    def group_time(idxs: list[int]) -> datetime:
        times = [samples[i].name_time for i in idxs if samples[i].name_time]
        if times:
            return min(times)
        return datetime.min

    dated = all(
        any(samples[i].name_time for i in idxs) for idxs in groups.values()
    )
    ordered = sorted(groups.items(), key=lambda kv: group_time(kv[1]))
    n_g = len(ordered)
    n_test = max(1, int(round(n_g * test_fraction))) if n_g >= 5 else max(1, n_g // 5)
    n_test = min(n_test, n_g - 1) if n_g > 1 else 0
    if not dated:
        # 날짜 없으면 그룹 id 해시로 안정 분할
        ordered = sorted(ordered, key=lambda kv: (kv[0] ^ seed))
    test_g = {gid for gid, _ in ordered[-n_test:]} if n_test else set()
    train_idx: list[int] = []
    test_idx: list[int] = []
    for gid, idxs in groups.items():
        if gid in test_g:
            test_idx.extend(idxs)
        else:
            train_idx.extend(idxs)
    # train 에 양 클래스 없으면 test 에서 한 그룹 되돌림
    def labels_of(idxs: list[int]) -> set[int]:
        return {samples[i].label for i in idxs}

    if train_idx and labels_of(train_idx) != {0, 1}:
        log.warning("train missing a class — moving one test group back")
        for gid, idxs in ordered:
            if gid in test_g:
                test_g.remove(gid)
                break
        train_idx, test_idx = [], []
        for gid, idxs in groups.items():
            if gid in test_g:
                test_idx.extend(idxs)
            else:
                train_idx.extend(idxs)
    log.info(
        "poster split groups=%s train=%s test=%s dated=%s",
        n_g,
        len(train_idx),
        len(test_idx),
        dated,
    )
    return train_idx, test_idx

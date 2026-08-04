# [변경사유]: Phase 4.0 — Hamming 기반 similar 클러스터 (서버 min(dHash,pHash) 와 동일)
"""사진 signature 목록 → similar 그룹 (union-find)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhotoSig:
    """비교용 서명."""

    photo_id: int
    dhash_hex: str
    phash_hex: str
    sha256: str | None = None


@dataclass(frozen=True)
class SimilarCluster:
    """멤버 photo_id 목록 (size>=2)."""

    photo_ids: tuple[int, ...]
    representative_photo_id: int


def hamming_hex(a: str, b: str) -> int:
    """hex 해시 Hamming distance."""
    aa = (a or "").lower().strip()
    bb = (b or "").lower().strip()
    if len(aa) != len(bb) or not aa:
        raise ValueError("hash length mismatch")
    return (int(aa, 16) ^ int(bb, 16)).bit_count()


def min_perceptual_distance(a: PhotoSig, b: PhotoSig) -> int:
    """서버 assessSimilar 와 동일: min(dHash, pHash)."""
    return min(
        hamming_hex(a.dhash_hex, b.dhash_hex),
        hamming_hex(a.phash_hex, b.phash_hex),
    )


def cluster_similar_photos(
    photos: list[PhotoSig],
    *,
    max_distance: int = 10,
    skip_same_sha: bool = True,
) -> list[SimilarCluster]:
    """
    O(n^2) pairwise — 로컬 배치 규모(수백~수천) 가정.
    동일 SHA는 Exact 그룹 영역 → skip_same_sha=True 면 간선 생략.
    """
    n = len(photos)
    if n < 2:
        return []
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

    for i in range(n):
        for j in range(i + 1, n):
            a, b = photos[i], photos[j]
            if skip_same_sha and a.sha256 and b.sha256 and a.sha256 == b.sha256:
                continue
            try:
                dist = min_perceptual_distance(a, b)
            except ValueError:
                continue
            if dist <= max_distance:
                union(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        buckets.setdefault(r, []).append(photos[i].photo_id)

    out: list[SimilarCluster] = []
    for ids in buckets.values():
        if len(ids) < 2:
            continue
        ids_sorted = tuple(sorted(ids))
        # 임시 대표: 가장 작은 photo_id (리뷰에서 변경)
        out.append(
            SimilarCluster(
                photo_ids=ids_sorted,
                representative_photo_id=ids_sorted[0],
            )
        )
    out.sort(key=lambda c: c.photo_ids)
    return out

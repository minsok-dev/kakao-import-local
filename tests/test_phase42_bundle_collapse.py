# [변경사유]: Phase 4.2+ — 채팅 매칭 묶음 collapse 단위 테스트
# [변경사유]: 묶음은 KakaoTalk `_01` 동일 스템만 — 연속 단독 사진 오묶음 방지
"""collapse_grouped_photo_bundles."""

from __future__ import annotations

import hashlib

from kakao_import.payload import (
    MAX_BUNDLE_MEMBERS,
    _bundle_idempotency_key,
    collapse_grouped_photo_bundles,
    media_fingerprint,
)
from kakao_import.photo_name import kakao_album_stem_and_seq


STEM = "20260804_161921080"


def _kakao_name(seq: int = 0, *, stem: str = STEM) -> str:
    suffix = f"_{seq:02d}" if seq else ""
    return f"KakaoTalk_{stem}{suffix}.png"


def _item(
    photo_id: int,
    *,
    sha: str | None = None,
    slot: int = 0,
    slot_count: int = 3,
    group_id: int = 10,
    bundle: bool = True,
    seq: int = 0,
    stem: str = STEM,
    file_name: str | None = None,
) -> dict:
    hexsha = sha or (f"{photo_id:064x}"[-64:])
    name = file_name or _kakao_name(seq, stem=stem)
    return {
        "local_item_id": f"photo:{photo_id}",
        "decision": "upload_one",
        "sha256": hexsha,
        "rel_path": f"photos/{name}",
        "matched_messages": [{"sent_at": "", "text": "hi"}],
        "_meta": {
            "photo_id": photo_id,
            "file_name": name,
            "idempotency_key": "x" * 64,
            "group_candidate": {
                "group_id": group_id,
                "group_key": f"g{group_id}",
                "slot_index": slot,
                "slot_count": slot_count,
                "bundle_candidate": bundle,
            },
        },
    }


def test_kakao_album_stem_and_seq() -> None:
    stem, seq = kakao_album_stem_and_seq("KakaoTalk_20260814_143041810.png")
    assert stem == "20260814_143041810" and seq == 0
    stem2, seq2 = kakao_album_stem_and_seq("KakaoTalk_20260814_143041810_01.png")
    assert stem2 == stem and seq2 == 1
    assert kakao_album_stem_and_seq("random.jpg") == (None, 0)


def test_collapse_three_to_one_bundle() -> None:
    items = [
        _item(1, slot=0, seq=0),
        _item(2, slot=1, seq=1),
        _item(3, slot=2, seq=2),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    main = r["items"][0]
    assert main["_meta"]["photo_id"] == 1
    assert len(main["sub_images"]) == 2
    assert main["sub_images"][0]["rel_path"].endswith(_kakao_name(1))
    assert r["bundle_collapsed_count"] == 2
    assert len(r["bundled_groups"]) == 1
    assert r["bundled_groups"][0]["member_count"] == 3
    assert r["bundled_groups"][0]["album_stem"] == STEM


def test_collapse_partial_y_two_of_three_slots() -> None:
    """슬롯 3 · 파일 2(본파일+_01) → 있는 장만 묶음."""
    items = [
        _item(1, slot=0, slot_count=3, seq=0),
        _item(3, slot=2, slot_count=3, seq=1),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    assert r["items"][0]["_meta"]["photo_id"] == 1
    assert len(r["items"][0]["sub_images"]) == 1
    assert r["items"][0]["sub_images"][0]["rel_path"].endswith(_kakao_name(1))


def test_collapse_album_suffix_only_without_base() -> None:
    """본파일 없어도 `_01`+`_02` 동일 스템이면 묶음."""
    items = [
        _item(8, slot=0, seq=1),
        _item(9, slot=1, seq=2),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    assert r["items"][0]["_meta"]["photo_id"] == 8
    assert len(r["items"][0]["sub_images"]) == 1


def test_no_collapse_consecutive_singles_different_ms() -> None:
    """같은 분·연속 채팅이어도 접미사 없는 단독 2장은 묶지 않음 (실사고 케이스)."""
    items = [
        _item(1309, slot=0, stem="20260814_143041810", seq=0),
        _item(1310, slot=1, stem="20260814_143053363", seq=0),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 2
    assert r["bundle_collapsed_count"] == 0
    assert "sub_images" not in r["items"][0]
    assert "sub_images" not in r["items"][1]


def test_mixed_group_album_plus_independent() -> None:
    """한 채팅 그룹에 앨범+단독이 있으면 앨범만 묶고 단독은 단건."""
    items = [
        _item(1, slot=0, seq=0),
        _item(2, slot=1, seq=1),
        _item(3, slot=2, stem="20260814_143053363", seq=0),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert r["bundle_collapsed_count"] == 1
    bundled = [it for it in r["items"] if it.get("sub_images")]
    singles = [it for it in r["items"] if not it.get("sub_images")]
    assert len(bundled) == 1 and bundled[0]["_meta"]["photo_id"] == 1
    assert len(singles) == 1 and singles[0]["_meta"]["photo_id"] == 3


def test_no_collapse_single_member() -> None:
    items = [_item(1, slot=0, slot_count=3, seq=0)]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    assert "sub_images" not in r["items"][0]
    assert r["bundle_collapsed_count"] == 0


def test_no_collapse_without_bundle_candidate() -> None:
    """similar-only / 단건 매칭 — 묶지 않음."""
    items = [
        _item(1, slot=0, slot_count=1, bundle=False, seq=0),
        _item(2, slot=1, slot_count=1, bundle=False, seq=1),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 2
    assert r["bundle_collapsed_count"] == 0


def test_no_collapse_non_kakao_filenames() -> None:
    """KakaoTalk `_01` 패턴이 아니면 채팅 슬롯 ≥2여도 묶지 않음."""
    items = [
        _item(1, slot=0, file_name="p1.jpg"),
        _item(2, slot=1, file_name="p2.jpg"),
        _item(3, slot=2, file_name="p3.jpg"),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 3
    assert r["bundle_collapsed_count"] == 0


def test_bundle_idempotency_stable() -> None:
    shas = ["a" * 64, "b" * 64, "c" * 64]
    cap = "c" * 64
    k1 = _bundle_idempotency_key(shas, cap)
    k2 = _bundle_idempotency_key(list(reversed(shas)), cap)
    assert k1 == k2
    assert len(k1) == 64
    media = media_fingerprint(shas[0], shas)
    assert k1 == hashlib.sha256(f"kakao:upload:v1:{media}:{cap}".encode()).hexdigest()


def test_max_members_truncate_overflow_stays_single() -> None:
    """상한 초과분은 드롭하지 않고 단건으로 남김 — 업로드 유실 방지."""
    items = [_item(i, slot=i, group_id=7, seq=i) for i in range(0, 7)]
    r = collapse_grouped_photo_bundles(
        items, client_id="cid", max_members=MAX_BUNDLE_MEMBERS
    )
    bundled = [it for it in r["items"] if it.get("sub_images")]
    singles = [it for it in r["items"] if not it.get("sub_images")]
    assert len(bundled) == 1
    assert 1 + len(bundled[0]["sub_images"]) == MAX_BUNDLE_MEMBERS
    assert len(singles) == 7 - MAX_BUNDLE_MEMBERS

# [변경사유]: Phase 4.2+ — 채팅 매칭 묶음 collapse 단위 테스트
"""collapse_grouped_photo_bundles."""

from __future__ import annotations

import hashlib

from kakao_import.payload import (
    MAX_BUNDLE_MEMBERS,
    _bundle_idempotency_key,
    collapse_grouped_photo_bundles,
)


def _item(
    photo_id: int,
    *,
    sha: str | None = None,
    slot: int = 0,
    slot_count: int = 3,
    group_id: int = 10,
    bundle: bool = True,
) -> dict:
    hexsha = sha or (f"{photo_id:064x}"[-64:])
    return {
        "local_item_id": f"photo:{photo_id}",
        "decision": "upload_one",
        "sha256": hexsha,
        "rel_path": f"photos/p{photo_id}.jpg",
        "matched_messages": [{"sent_at": "", "text": "hi"}],
        "_meta": {
            "photo_id": photo_id,
            "file_name": f"p{photo_id}.jpg",
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


def test_collapse_three_to_one_bundle() -> None:
    items = [
        _item(1, slot=0),
        _item(2, slot=1),
        _item(3, slot=2),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    main = r["items"][0]
    assert main["_meta"]["photo_id"] == 1
    assert len(main["sub_images"]) == 2
    assert main["sub_images"][0]["rel_path"].endswith("p2.jpg")
    assert r["bundle_collapsed_count"] == 2
    assert len(r["bundled_groups"]) == 1
    assert r["bundled_groups"][0]["member_count"] == 3


def test_collapse_partial_y_two_of_three_slots() -> None:
    """슬롯 3 · 파일 2 → 있는 장만 묶음."""
    items = [
        _item(1, slot=0, slot_count=3),
        _item(3, slot=2, slot_count=3),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    assert r["items"][0]["_meta"]["photo_id"] == 1
    assert len(r["items"][0]["sub_images"]) == 1
    assert r["items"][0]["sub_images"][0]["rel_path"].endswith("p3.jpg")


def test_no_collapse_single_member() -> None:
    items = [_item(1, slot=0, slot_count=3)]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 1
    assert "sub_images" not in r["items"][0]
    assert r["bundle_collapsed_count"] == 0


def test_no_collapse_without_bundle_candidate() -> None:
    """similar-only / 단건 매칭 — 묶지 않음."""
    items = [
        _item(1, slot=0, slot_count=1, bundle=False),
        _item(2, slot=1, slot_count=1, bundle=False),
    ]
    r = collapse_grouped_photo_bundles(items, client_id="cid")
    assert len(r["items"]) == 2
    assert r["bundle_collapsed_count"] == 0


def test_bundle_idempotency_stable() -> None:
    shas = ["a" * 64, "b" * 64, "c" * 64]
    k1 = _bundle_idempotency_key("cid", shas)
    k2 = _bundle_idempotency_key("cid", list(reversed(shas)))
    assert k1 == k2
    assert len(k1) == 64
    joined = "|".join(sorted(shas))
    assert k1 == hashlib.sha256(f"cid|bundle|{joined}".encode()).hexdigest()


def test_max_members_truncate() -> None:
    items = [_item(i, slot=i, group_id=7) for i in range(1, 8)]
    r = collapse_grouped_photo_bundles(
        items, client_id="cid", max_members=MAX_BUNDLE_MEMBERS
    )
    assert len(r["items"]) == 1
    assert 1 + len(r["items"][0]["sub_images"]) == MAX_BUNDLE_MEMBERS

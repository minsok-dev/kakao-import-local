# [변경사유]: A — similar deferred/same_content 를 앨범 묶음 단위로 적용
"""_apply_similar_policy album unit."""

from __future__ import annotations

from kakao_import.payload import _apply_similar_policy, collapse_grouped_photo_bundles


STEM = "20260827_230006025"


def _kakao(seq: int = 0) -> str:
    suffix = f"_{seq:02d}" if seq else ""
    return f"KakaoTalk_{STEM}{suffix}.png"


def _item(pid: int, seq: int = 0) -> dict:
    name = _kakao(seq)
    return {
        "local_item_id": f"photo:{pid}",
        "decision": "upload_one",
        "sha256": f"{pid:064x}"[-64:],
        "rel_path": f"photos/{name}",
        "matched_messages": [{"sent_at": "", "text": f"cap-{pid}"}],
        "_meta": {"photo_id": pid, "file_name": name},
    }


def _sim(pid: int, *, decision: str, rep: int, gid: int = 1) -> dict:
    return {
        "group_id": gid,
        "group_key": f"sg-{gid}",
        "decision": decision,
        "upload_policy": "upload_none" if decision == "deferred" else "upload_representative",
        "representative_photo_id": rep,
        "member_count": 2,
        "subgroup_key": None,
        "subgroup_size": 0,
        "is_subgroup_rep": False,
    }


def test_deferred_holds_whole_album_bundle() -> None:
    items = [_item(1, 0), _item(2, 1), _item(3, 2)]
    bundled = collapse_grouped_photo_bundles(items, client_id="c")["items"]
    assert len(bundled) == 1
    # 형제 하나만 deferred 여도 묶음 전체 skip
    similar_map = {
        2: _sim(2, decision="deferred", rep=2),
    }
    r = _apply_similar_policy(bundled, similar_map)
    assert r["items"] == []
    assert len(r["skipped"]) == 1
    assert r["skipped"][0]["reason"] == "similar_deferred"


def test_same_content_keeps_album_if_rep_inside() -> None:
    items = [_item(1, 0), _item(2, 1)]
    bundled = collapse_grouped_photo_bundles(items, client_id="c")["items"]
    # 대표가 `_01`(photo 2) — main 은 본파일이어도 묶음 keep
    similar_map = {
        1: _sim(1, decision="same_content", rep=2),
        2: _sim(2, decision="same_content", rep=2),
    }
    r = _apply_similar_policy(bundled, similar_map)
    assert len(r["items"]) == 1
    assert r["skipped"] == []

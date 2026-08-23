# [변경사유]: --room 레이아웃 필터
"""방 필터 유틸."""

from __future__ import annotations

from pathlib import Path

from kakao_import.pipeline import (
    filter_room_layouts,
    iter_room_layouts,
    normalize_room_ids,
    room_id_from_rel,
)


def test_normalize_and_filter_layouts(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    for rid in ("alpha", "beta"):
        (raw / rid / "photos").mkdir(parents=True)
        (raw / rid / "chats").mkdir(parents=True)
    layouts = iter_room_layouts(raw)
    assert {x[0] for x in layouts} >= {"alpha", "beta"}
    only = filter_room_layouts(layouts, normalize_room_ids(["alpha"]))
    assert [x[0] for x in only] == ["alpha"]
    assert room_id_from_rel("alpha/photos/a.jpg") == "alpha"
    assert normalize_room_ids([]) is None
    assert normalize_room_ids(None) is None

# [변경사유]: PC 앨범 원자 — 슬롯 분 윈도우 후보 + stem 형제 group_text 공유
"""matcher album window / stem sibling."""

from __future__ import annotations

from datetime import datetime

from kakao_import.matcher import PhotoSlot, match_photos_to_messages


def _msg(
    mid: int,
    *,
    seq: int,
    kind: str,
    body: str,
    at: datetime,
    chat_id: int = 1,
    sender: str = "DJ",
    photo_count: int = 1,
) -> dict:
    return {
        "id": mid,
        "chat_id": chat_id,
        "seq": seq,
        "msg_kind": kind,
        "sender": sender,
        "abs_time": at.isoformat(timespec="seconds"),
        "body_raw": body,
        "photo_count": photo_count,
        "body_norm": body,
    }


def test_match_candidates_span_adjacent_minutes() -> None:
    """사진(10:59) + 사진 2장(11:00) — 파일도 두 분에 걸쳐 있으면 전원 배정."""
    t_text = datetime(2026, 8, 27, 10, 59, 0)
    t_photo1 = datetime(2026, 8, 27, 10, 59, 30)
    t_photo2 = datetime(2026, 8, 27, 11, 0, 10)
    msgs = [
        _msg(1, seq=1, kind="text", body="intro", at=t_text),
        _msg(2, seq=2, kind="photo", body="사진", at=t_photo1, photo_count=1),
        _msg(3, seq=3, kind="photo_multi", body="사진 2장", at=t_photo2, photo_count=2),
        _msg(4, seq=4, kind="text", body="after", at=datetime(2026, 8, 27, 11, 0, 40)),
    ]
    photos = [
        PhotoSlot(1, "photos/KakaoTalk_20260827_105930100.png", t_photo1, 0),
        PhotoSlot(2, "photos/KakaoTalk_20260827_110010200.png", t_photo2, 0),
        PhotoSlot(
            3,
            "photos/KakaoTalk_20260827_110010200_01.png",
            t_photo2,
            1,
        ),
    ]
    out = match_photos_to_messages(
        photos=photos,
        messages=msgs,
        tolerance_seconds=60,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    assigned = {
        a.photo_id: a
        for a in out.assignments
        if a.confidence != "unmatched"
    }
    assert set(assigned.keys()) == {1, 2, 3}
    assert len({a.group_key for a in assigned.values()}) == 1
    assert any(gt.message_id == 1 for gt in out.group_texts)
    assert any(gt.message_id == 4 for gt in out.group_texts)


def test_album_stem_sibling_shares_group_key() -> None:
    """본파일만 분 매칭돼도 `_01` 형제는 같은 group_key 로 붙는다."""
    t = datetime(2026, 8, 27, 23, 0, 6)
    msgs = [
        _msg(1, seq=1, kind="text", body="cap", at=datetime(2026, 8, 27, 22, 59, 50)),
        _msg(2, seq=2, kind="photo_multi", body="사진 3장", at=t, photo_count=3),
    ]
    # 파일 시각이 메시지와 맞지만 후보 수·순서로 본파일+_01 만 1차 배정되는 상황을
    # stem sibling 이 `_02` 까지 끌어올린다.
    photos = [
        PhotoSlot(10, "photos/KakaoTalk_20260827_230006025.png", t, 0),
        PhotoSlot(11, "photos/KakaoTalk_20260827_230006025_01.png", t, 1),
        PhotoSlot(12, "photos/KakaoTalk_20260827_230006025_02.png", t, 2),
    ]
    out = match_photos_to_messages(
        photos=photos,
        messages=msgs,
        tolerance_seconds=60,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    assigned = [a for a in out.assignments if a.confidence != "unmatched"]
    assert len(assigned) == 3
    keys = {a.group_key for a in assigned}
    assert len(keys) == 1
    assert any(a.match_reason == "album_stem_sibling" for a in assigned) or all(
        a.match_reason != "no_message_match" for a in assigned
    )

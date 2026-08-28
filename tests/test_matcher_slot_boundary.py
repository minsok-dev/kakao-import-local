# [변경사유]: 연속 사진 슬롯 병합 경계 — 발신자 변경·간격 초과 시 그룹 분리
"""matcher slot merge boundary."""

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


def _run(msgs: list[dict], photos: list[PhotoSlot], *, gap_minutes: int = 2):
    return match_photos_to_messages(
        photos=photos,
        messages=msgs,
        tolerance_seconds=60,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        slot_merge_max_gap_minutes=gap_minutes,
    )


def test_sender_change_splits_group_and_keeps_each_caption() -> None:
    """
    A(09:09 텍스트+사진) 뒤에 B(10:55 사진 2장 + 10:56 텍스트).
    두 그룹으로 분리되어 각자 자기 발신자 본문을 갖는다.
    """
    t_a_text = datetime(2026, 8, 28, 9, 9, 0)
    t_a_photo = datetime(2026, 8, 28, 9, 9, 30)
    t_b_photo = datetime(2026, 8, 28, 10, 55, 50)
    t_b_text = datetime(2026, 8, 28, 10, 56, 0)
    msgs = [
        _msg(1, seq=1, kind="text", body="A안내", at=t_a_text, sender="A"),
        _msg(2, seq=2, kind="photo", body="사진", at=t_a_photo, sender="A"),
        _msg(
            3,
            seq=3,
            kind="photo_multi",
            body="사진 2장",
            at=t_b_photo,
            sender="B",
            photo_count=2,
        ),
        _msg(4, seq=4, kind="text", body="B포스터본문", at=t_b_text, sender="B"),
    ]
    photos = [
        PhotoSlot(1, "photos/KakaoTalk_20260828_090930100.png", t_a_photo, 0),
        PhotoSlot(2, "photos/KakaoTalk_20260828_105550870.jpg", t_b_photo, 0),
        PhotoSlot(3, "photos/KakaoTalk_20260828_105550870_01.jpg", t_b_photo, 1),
    ]
    out = _run(msgs, photos)

    assigned = {a.photo_id: a for a in out.assignments if a.confidence != "unmatched"}
    assert set(assigned) == {1, 2, 3}
    # A 사진과 B 사진은 서로 다른 그룹
    assert assigned[1].group_key != assigned[2].group_key
    # B 앨범 두 장은 같은 그룹
    assert assigned[2].group_key == assigned[3].group_key

    texts_by_group: dict[str, set[int]] = {}
    for gt in out.group_texts:
        texts_by_group.setdefault(gt.group_key, set()).add(gt.message_id)
    # A 그룹은 A 텍스트만, B 그룹은 B 텍스트만
    assert texts_by_group.get(assigned[1].group_key) == {1}
    assert texts_by_group.get(assigned[2].group_key) == {4}


def test_same_sender_long_gap_splits_group() -> None:
    """같은 사람이라도 간격이 상한(2분)을 넘으면 그룹을 끊는다."""
    t1 = datetime(2026, 8, 28, 7, 26, 0)
    t2 = datetime(2026, 8, 28, 7, 30, 0)
    msgs = [
        _msg(1, seq=1, kind="photo", body="사진", at=t1),
        _msg(2, seq=2, kind="photo", body="사진", at=t2),
        _msg(3, seq=3, kind="text", body="설명", at=datetime(2026, 8, 28, 7, 30, 30)),
    ]
    photos = [
        PhotoSlot(1, "photos/KakaoTalk_20260828_072600100.png", t1, 0),
        PhotoSlot(2, "photos/KakaoTalk_20260828_073000200.png", t2, 0),
    ]
    out = _run(msgs, photos)
    assigned = {a.photo_id: a for a in out.assignments if a.confidence != "unmatched"}
    assert set(assigned) == {1, 2}
    assert assigned[1].group_key != assigned[2].group_key
    # 후속 텍스트는 뒤 그룹에만
    groups_with_text = {gt.group_key for gt in out.group_texts if gt.message_id == 3}
    assert groups_with_text == {assigned[2].group_key}


def test_photo_multi_album_not_split_by_gap_rule() -> None:
    """'사진 3장' 한 건은 간격 규칙과 무관하게 한 그룹으로 유지."""
    t = datetime(2026, 8, 28, 11, 2, 0)
    msgs = [
        _msg(1, seq=1, kind="photo_multi", body="사진 3장", at=t, photo_count=3),
        _msg(2, seq=2, kind="text", body="본문", at=datetime(2026, 8, 28, 11, 3, 0)),
    ]
    photos = [
        PhotoSlot(1, "photos/KakaoTalk_20260828_110200100.png", t, 0),
        PhotoSlot(2, "photos/KakaoTalk_20260828_110200100_01.png", t, 1),
        PhotoSlot(3, "photos/KakaoTalk_20260828_110200100_02.png", t, 2),
    ]
    out = _run(msgs, photos)
    assigned = [a for a in out.assignments if a.confidence != "unmatched"]
    assert len(assigned) == 3
    assert len({a.group_key for a in assigned}) == 1


def test_gap_zero_keeps_legacy_merge() -> None:
    """설정 0 이면 이전 동작(간격 무제한). 단, 발신자 조건은 항상 적용."""
    t1 = datetime(2026, 8, 28, 7, 26, 0)
    t2 = datetime(2026, 8, 28, 7, 40, 0)
    msgs = [
        _msg(1, seq=1, kind="photo", body="사진", at=t1),
        _msg(2, seq=2, kind="photo", body="사진", at=t2),
    ]
    photos = [
        PhotoSlot(1, "photos/KakaoTalk_20260828_072600100.png", t1, 0),
        PhotoSlot(2, "photos/KakaoTalk_20260828_074000200.png", t2, 0),
    ]
    out = _run(msgs, photos, gap_minutes=0)
    assigned = {a.photo_id: a for a in out.assignments if a.confidence != "unmatched"}
    assert set(assigned) == {1, 2}
    assert assigned[1].group_key == assigned[2].group_key

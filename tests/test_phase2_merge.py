# [변경사유]: Phase2 텍스트 merge 단위 테스트
"""text_merge collapse / safe review / balanced merge+conflict."""

from __future__ import annotations

from kakao_import.text_merge import TextBundle, merge_exact_texts


def _bundle(pid: int, *texts: str) -> TextBundle:
    b = TextBundle(photo_id=pid)
    for i, t in enumerate(texts):
        b.parts.append(
            {
                "message_id": pid * 10 + i,
                "body_raw": t,
                "body_norm": t,
                "abs_time": "2026-07-24T00:53:00",
            }
        )
    return b


def test_collapse_same_text() -> None:
    r = merge_exact_texts(
        "abc",
        [_bundle(1, "hello"), _bundle(2, "hello")],
        "balanced",
    )
    assert r.decision == "collapse"
    assert r.review_required is False
    assert r.merged_text == "hello"


def test_safe_diff_goes_review() -> None:
    r = merge_exact_texts(
        "abc",
        [_bundle(1, "a"), _bundle(2, "b")],
        "safe",
    )
    assert r.decision == "review"
    assert r.review_required is True
    assert r.merged_text is None


def test_balanced_unique_merge() -> None:
    """멤버별 본문은 구분선으로 연결 (줄 flatten 대신 블록 유지)."""
    from kakao_import.caption_sep import ROOM_ADD_SEPARATOR

    r = merge_exact_texts(
        "abc",
        [_bundle(1, "line1", "shared"), _bundle(2, "shared", "line2")],
        "balanced",
    )
    assert r.decision == "merged"
    assert r.review_required is False
    text = r.merged_text or ""
    assert "line1" in text
    assert "line2" in text
    assert ROOM_ADD_SEPARATOR in text


def test_joined_raw_inserts_room_separator() -> None:
    from kakao_import.caption_sep import ROOM_ADD_SEPARATOR

    b = TextBundle(photo_id=1)
    b.parts.append(
        {
            "message_id": 1,
            "chat_id": 77,
            "body_raw": "room77 text",
            "body_norm": "room77",
            "abs_time": "2026-08-04T15:00:00",
        }
    )
    b.parts.append(
        {
            "message_id": 2,
            "chat_id": 80,
            "body_raw": "room80 text",
            "body_norm": "room80",
            "abs_time": "2026-08-04T15:00:00",
        }
    )
    raw = b.joined_raw()
    assert "room77 text" in raw and "room80 text" in raw
    assert ROOM_ADD_SEPARATOR in raw
    assert f"\n\n{ROOM_ADD_SEPARATOR}\n\n" in raw


def test_balanced_url_conflict_review() -> None:
    r = merge_exact_texts(
        "abc",
        [
            _bundle(1, "see https://a.example/x"),
            _bundle(2, "see https://b.example/y"),
        ],
        "balanced",
    )
    assert r.decision == "review"
    assert r.review_required is True
    assert any(c["field_name"] == "url" for c in r.conflicts)
    assert "https://" in (r.merged_text or "")


def test_before_json_for_undo() -> None:
    r = merge_exact_texts("abc", [_bundle(1, "only")], "auto")
    assert r.decision == "collapse"
    assert len(r.before) == 1
    assert r.before[0]["text"] == "only"

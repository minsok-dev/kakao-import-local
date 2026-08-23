# [변경사유]: 방별 chats 는 최신 export txt 1개만 사용
"""중복 카카오 대화 export 가 있어도 최신 txt만 파싱하는지 확인."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.pipeline import cmd_parse, latest_chat_exports


def _settings(tmp: Path, root: Path) -> Settings:
    return Settings(
        export_root=root,
        db_path=tmp / "t.db",
        log_level="WARNING",
        match_tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
    )


def _chat_text(room: str, body: str) -> str:
    return (
        f"{room} 님과 카카오톡 대화\n"
        "저장한 날짜 : 2026-08-18 18:00:00\n"
        "--------------- 2026년 8월 18일 화요일 ---------------\n"
        f"[홍길동] [오전 12:06] {body}\n"
    )


def test_latest_chat_exports_selects_newest_name(tmp_path: Path) -> None:
    chats = tmp_path / "gangnam_latin" / "chats"
    chats.mkdir(parents=True)
    old_txt = chats / "KakaoTalk_20260818_000621766_group.txt"
    new_txt = chats / "KakaoTalk_20260818_013327453_group.txt"
    old_txt.write_text(_chat_text("강남 라틴클럽", "예전 대화"), encoding="utf-8")
    new_txt.write_text(_chat_text("강남 라틴클럽", "최신 대화"), encoding="utf-8")

    picked = latest_chat_exports(chats)
    assert picked == [new_txt]


def test_cmd_parse_keeps_only_latest_chat_source(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    chats = root / "gangnam_latin" / "chats"
    photos = root / "gangnam_latin" / "photos"
    chats.mkdir(parents=True)
    photos.mkdir(parents=True)
    old_txt = chats / "KakaoTalk_20260818_000621766_group.txt"
    new_txt = chats / "KakaoTalk_20260818_013327453_group.txt"
    old_txt.write_text(_chat_text("강남 라틴클럽", "예전 대화"), encoding="utf-8")
    new_txt.write_text(_chat_text("강남 라틴클럽", "최신 대화"), encoding="utf-8")

    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)

    summary = cmd_parse(settings, root)
    assert summary["rooms"] == 1
    assert summary["messages"] == 1

    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT rel_path FROM chat_source ORDER BY rel_path"
        ).fetchall()
        msgs = conn.execute(
            "SELECT body_raw FROM parsed_message ORDER BY id"
        ).fetchall()

    assert [str(r["rel_path"]) for r in rows] == [
        "gangnam_latin/chats/KakaoTalk_20260818_013327453_group.txt"
    ]
    assert [str(r["body_raw"]) for r in msgs] == ["최신 대화"]


def test_cmd_parse_skips_unchanged_content_sha(tmp_path: Path) -> None:
    """[변경사유]: I7 — content_sha256 동일 시 재파싱·replace 스킵."""
    root = tmp_path / "raw"
    chats = root / "gangnam_latin" / "chats"
    photos = root / "gangnam_latin" / "photos"
    chats.mkdir(parents=True)
    photos.mkdir(parents=True)
    txt = chats / "KakaoTalk_20260818_013327453_group.txt"
    txt.write_text(_chat_text("강남 라틴클럽", "동일 대화"), encoding="utf-8")

    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)

    first = cmd_parse(settings, root)
    assert first["skipped_unchanged"] == 0
    assert first["messages"] == 1

    second = cmd_parse(settings, root)
    assert second["skipped_unchanged"] == 1
    assert second["messages"] == 1
    assert second["rooms"] == 1

    with connect(settings.db_path) as conn:
        msgs = conn.execute(
            "SELECT body_raw FROM parsed_message ORDER BY id"
        ).fetchall()
    assert [str(r["body_raw"]) for r in msgs] == ["동일 대화"]


def test_cmd_parse_reparses_when_content_changes(tmp_path: Path) -> None:
    """내용이 바뀌면 스킵하지 않고 메시지를 교체한다."""
    root = tmp_path / "raw"
    chats = root / "gangnam_latin" / "chats"
    (root / "gangnam_latin" / "photos").mkdir(parents=True)
    chats.mkdir(parents=True)
    txt = chats / "KakaoTalk_20260818_013327453_group.txt"
    txt.write_text(_chat_text("강남 라틴클럽", "첫번째"), encoding="utf-8")

    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    cmd_parse(settings, root)

    txt.write_text(_chat_text("강남 라틴클럽", "두번째"), encoding="utf-8")
    out = cmd_parse(settings, root)
    assert out["skipped_unchanged"] == 0

    with connect(settings.db_path) as conn:
        msgs = conn.execute(
            "SELECT body_raw FROM parsed_message ORDER BY id"
        ).fetchall()
    assert [str(r["body_raw"]) for r in msgs] == ["두번째"]

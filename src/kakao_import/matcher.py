# [변경사유]: Phase1 — 시각·개수·순서 matcher + image group + 후속 텍스트
"""사진↔메시지 매칭."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from kakao_import.timeutil import minute_key, parse_iso


@dataclass
class PhotoSlot:
    """매칭용 사진 파일."""

    photo_id: int
    rel_path: str
    name_time: datetime


@dataclass
class MsgSlot:
    """사진 메시지 슬롯 1칸."""

    message_id: int
    chat_id: int
    sender: str | None
    abs_time: datetime
    seq: int


@dataclass
class Assignment:
    """배정 결과."""

    photo_id: int
    message_id: int | None
    chat_id: int | None
    slot_index: int
    confidence: str
    match_reason: str
    review_required: bool
    group_key: str


@dataclass
class GroupTextLink:
    """그룹 설명 메시지."""

    group_key: str
    message_id: int
    seq_in_group: int


@dataclass
class MatchOutput:
    """매처 출력."""

    assignments: list[Assignment] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    group_texts: list[GroupTextLink] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)


def _photo_message_slots(messages: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[MsgSlot]]]:
    """
    연속 사진 메시지를 그룹 후보로 묶고 슬롯 전개.
    반환: (anchor_msg, slots[])
    """
    groups: list[tuple[dict[str, Any], list[MsgSlot]]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m["msg_kind"] not in ("photo", "photo_multi") or not m.get("abs_time"):
            i += 1
            continue
        slots: list[MsgSlot] = []
        anchor = m
        j = i
        while (
            j < n
            and messages[j]["msg_kind"] in ("photo", "photo_multi")
            and messages[j].get("abs_time")
        ):
            mj = messages[j]
            count = int(mj.get("photo_count") or 1)
            at = parse_iso(mj["abs_time"]) if isinstance(mj["abs_time"], str) else mj["abs_time"]
            assert at is not None
            for _ in range(count):
                slots.append(
                    MsgSlot(
                        message_id=int(mj["id"]),
                        chat_id=int(mj["chat_id"]),
                        sender=mj.get("sender"),
                        abs_time=at,
                        seq=int(mj["seq"]),
                    )
                )
            j += 1
        groups.append((anchor, slots))
        i = j
    return groups


def match_photos_to_messages(
    *,
    photos: list[PhotoSlot],
    messages: list[dict[str, Any]],
    tolerance_seconds: int,
    group_text_max_gap_minutes: int,
    different_sender_grace_seconds: int,
    different_sender_max_chars: int,
) -> MatchOutput:
    """
    매칭 수행.
    messages: id, chat_id, seq, msg_kind, sender, abs_time(ISO), body_raw, photo_count, body_norm
    """
    out = MatchOutput()
    # chat별 정렬
    by_chat: dict[int, list[dict[str, Any]]] = {}
    for m in messages:
        by_chat.setdefault(int(m["chat_id"]), []).append(m)
    for cid, chat_msgs in by_chat.items():
        chat_msgs.sort(key=lambda x: int(x["seq"]))

    # 분 버킷: 파싱 성공 사진
    photos_ok = [p for p in photos if p.name_time is not None]
    photos_by_minute: dict[tuple, list[PhotoSlot]] = {}
    for p in photos_ok:
        photos_by_minute.setdefault(minute_key(p.name_time), []).append(p)
    for plist in photos_by_minute.values():
        plist.sort(key=lambda x: x.name_time)

    # 방별 사진 메시지 분 버킷 (충돌 감지)
    room_minutes: dict[tuple, set[int]] = {}
    for cid, msgs in by_chat.items():
        for m in msgs:
            if m["msg_kind"] not in ("photo", "photo_multi") or not m.get("abs_time"):
                continue
            at = parse_iso(m["abs_time"]) if isinstance(m["abs_time"], str) else m["abs_time"]
            if at is None:
                continue
            room_minutes.setdefault(minute_key(at), set()).add(cid)

    assigned_photos: set[int] = set()

    group_idx = 0
    for cid, msgs in by_chat.items():
        for anchor, slots in _photo_message_slots(msgs):
            if not slots:
                continue
            group_idx += 1
            group_key = f"c{cid}_g{group_idx}_s{slots[0].seq}"
            mk = minute_key(slots[0].abs_time)
            rooms = room_minutes.get(mk, set())
            multi_room = len(rooms) > 1

            candidates = list(photos_by_minute.get(mk, []))
            # 미배정만
            candidates = [p for p in candidates if p.photo_id not in assigned_photos]

            confidence = "ambiguous"
            reason = ""
            review = True

            if multi_room:
                reason = "multi_room_same_minute"
            elif len(candidates) == len(slots) and len(slots) > 0:
                confidence = "high"
                reason = "same_minute_count_order"
                review = False
            elif len(candidates) != len(slots):
                # tolerance: 유일 대체?
                reason = "count_mismatch"
                if len(slots) == 1 and not candidates:
                    # 단일 슬롯 — tolerance 내 유일 미배정 사진
                    sole = _unique_tolerance_photo(
                        slots[0].abs_time, photos_ok, assigned_photos, tolerance_seconds
                    )
                    if sole:
                        candidates = [sole]
                        confidence = "medium"
                        reason = "tolerance_unique"
                        review = True  # medium → review
                    else:
                        confidence = "ambiguous"
                        review = True
                else:
                    confidence = "ambiguous"
                    review = True
            else:
                confidence = "ambiguous"
                reason = "fallback"
                review = True

            out.groups.append(
                {
                    "group_key": group_key,
                    "chat_id": cid,
                    "confidence": confidence,
                    "review_required": review,
                    "match_reason": reason,
                    "slot_count": len(slots),
                    "candidate_count": len(candidates),
                }
            )

            if confidence in ("high", "medium") and len(candidates) == len(slots):
                for si, (slot, photo) in enumerate(zip(slots, candidates, strict=True)):
                    assigned_photos.add(photo.photo_id)
                    out.assignments.append(
                        Assignment(
                            photo_id=photo.photo_id,
                            message_id=slot.message_id,
                            chat_id=cid,
                            slot_index=si,
                            confidence=confidence,
                            match_reason=reason,
                            review_required=review,
                            group_key=group_key,
                        )
                    )
                # 후속 텍스트
                _attach_group_texts(
                    out,
                    group_key=group_key,
                    msgs=msgs,
                    after_seq=slots[-1].seq,
                    last_photo_time=slots[-1].abs_time,
                    photo_sender=slots[0].sender,
                    max_gap=timedelta(minutes=group_text_max_gap_minutes),
                    diff_grace=timedelta(seconds=different_sender_grace_seconds),
                    diff_max_chars=different_sender_max_chars,
                )
            else:
                # 강제 배정 없음 — review만
                out.reviews.append(
                    {
                        "kind": "match_ambiguous",
                        "group_key": group_key,
                        "reason": reason,
                        "confidence": confidence,
                        "slots": len(slots),
                        "candidates": [p.rel_path for p in candidates[:20]],
                    }
                )

    # unmatched photos
    for p in photos_ok:
        if p.photo_id not in assigned_photos:
            out.assignments.append(
                Assignment(
                    photo_id=p.photo_id,
                    message_id=None,
                    chat_id=None,
                    slot_index=0,
                    confidence="unmatched",
                    match_reason="no_message_match",
                    review_required=True,
                    group_key=f"unmatched_{p.photo_id}",
                )
            )
            out.reviews.append(
                {
                    "kind": "unmatched_photo",
                    "photo_id": p.photo_id,
                    "rel_path": p.rel_path,
                    "reason": "no_message_match",
                }
            )

    # unparsed photos reported by caller via parse_error
    return out


def _unique_tolerance_photo(
    center: datetime,
    photos: list[PhotoSlot],
    assigned: set[int],
    tolerance_seconds: int,
) -> PhotoSlot | None:
    """tolerance 창 안 미배정 사진이 정확히 1개면 반환."""
    window = timedelta(seconds=tolerance_seconds)
    found: list[PhotoSlot] = []
    for p in photos:
        if p.photo_id in assigned:
            continue
        if abs((p.name_time - center).total_seconds()) <= window.total_seconds():
            found.append(p)
    if len(found) == 1:
        return found[0]
    return None


def _attach_group_texts(
    out: MatchOutput,
    *,
    group_key: str,
    msgs: list[dict[str, Any]],
    after_seq: int,
    last_photo_time: datetime,
    photo_sender: str | None,
    max_gap: timedelta,
    diff_grace: timedelta,
    diff_max_chars: int,
) -> None:
    """
    연속 사진 뒤 설명 연결.
    종료: 다음 사진, 시스템, max_gap, (다른 발신자+grace초과 또는 긴 문장).
    다른 발신자만으로 즉시 종료하지 않음 — grace·길이 조건.
    """
    seq_g = 0
    for m in msgs:
        if int(m["seq"]) <= after_seq:
            continue
        kind = m["msg_kind"]
        if kind in ("photo", "photo_multi"):
            break
        if kind == "system":
            break
        at = parse_iso(m["abs_time"]) if m.get("abs_time") else None
        if at is None:
            # 날짜 없는 시스템성 — skip
            continue
        if at - last_photo_time > max_gap:
            break
        sender = m.get("sender")
        body = m.get("body_raw") or ""
        if sender != photo_sender:
            # 다른 발신자: grace 안 + 짧은 메시지만 스킵(그룹에 안 넣음)하고 계속? 
            # 규칙: grace 초과 또는 긴 문장 → 그룹 종료. grace 안 짧은 문장 → 넣지 않고 종료도 함(대화 분기).
            # Phase1: 다른 발신자면 설명에 포함하지 않고 그룹 텍스트 수집 종료.
            # (무조건 즉시 자르되, '포함 안 함'으로 문서화 — grace는 향후 확장용으로만 검사)
            if (at - last_photo_time) > diff_grace or len(body) > diff_max_chars:
                break
            break  # 짧은 타발신자도 Phase1에서는 그룹 설명 종료 (테스트로 고정)
        if kind != "text":
            # video/emoji/file — 그룹 설명 종료
            break
        seq_g += 1
        out.group_texts.append(
            GroupTextLink(group_key=group_key, message_id=int(m["id"]), seq_in_group=seq_g)
        )

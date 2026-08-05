# [변경사유]: Phase1 — 시각·개수·순서 matcher + image group + 후속 텍스트
# [변경사유]: 선행 텍스트(≤2분) 귀속 + 슬롯/파일 수 불일치 시 부분 매칭·동일 group_text
# [변경사유]: multi_room 같은 분 — 배정 유지 + 전 방 캡션 union (포기하지 않음)
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
    # [변경사유]: KakaoTalk `_01` 등 앨범 멤버 — name_time 동일 시 순서 보장
    name_seq: int = 0


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
    group_text_before_max_seconds: int = 120,
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
        # [변경사유]: 동일 ms + `_01`/`_02` 는 sequence → photo_id 순
        plist.sort(key=lambda x: (x.name_time, x.name_seq, x.photo_id))

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
            assign_n = 0

            # [변경사유]: multi_room이어도 후보 있으면 medium 배정 — 캡션은 전 방 union
            if multi_room and len(candidates) > 0 and len(slots) > 0:
                assign_n = min(len(candidates), len(slots))
                confidence = "medium"
                reason = "multi_room_caption_union"
                review = True
            elif multi_room:
                reason = "multi_room_same_minute"
            elif len(candidates) == len(slots) and len(slots) > 0:
                confidence = "high"
                reason = "same_minute_count_order"
                review = False
                assign_n = len(slots)
            elif len(slots) == 1 and not candidates:
                # 단일 슬롯 — tolerance 내 유일 미배정 사진
                reason = "count_mismatch"
                sole = _unique_tolerance_photo(
                    slots[0].abs_time, photos_ok, assigned_photos, tolerance_seconds
                )
                if sole:
                    candidates = [sole]
                    confidence = "medium"
                    reason = "tolerance_unique"
                    review = True
                    assign_n = 1
                else:
                    confidence = "ambiguous"
                    review = True
            elif len(candidates) > 0 and len(slots) > 0:
                # [변경사유]: 슬롯/파일 수 불일치여도 있는 파일은 순서 배정 + 동일 group_text
                #   부족한 슬롯·남는 파일은 review. 누락보다 과다 귀속이 낫다.
                assign_n = min(len(candidates), len(slots))
                confidence = "medium"
                reason = (
                    "partial_count_order"
                    if len(candidates) != len(slots)
                    else "same_minute_count_order"
                )
                review = True
            else:
                confidence = "ambiguous"
                reason = "count_mismatch" if len(candidates) != len(slots) else "fallback"
                review = True

            out.groups.append(
                {
                    "group_key": group_key,
                    "chat_id": cid,
                    "anchor_message_id": int(anchor["id"]),
                    "photo_sender": slots[0].sender,
                    "first_abs_time": slots[0].abs_time.isoformat(timespec="seconds"),
                    "last_abs_time": slots[-1].abs_time.isoformat(timespec="seconds"),
                    "confidence": confidence,
                    "review_required": review,
                    "match_reason": reason,
                    "slot_count": len(slots),
                    "candidate_count": len(candidates),
                    "assigned_count": assign_n,
                    "bundle_candidate": len(slots) >= 2,
                    "multi_room": multi_room,
                    "multi_room_count": len(rooms),
                }
            )

            if assign_n > 0 and confidence in ("high", "medium"):
                for si in range(assign_n):
                    slot = slots[si]
                    photo = candidates[si]
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
                # [변경사유]: multi_room → 같은 분 모든 방 캡션 union / 아니면 해당 방만
                if multi_room:
                    _commit_group_texts_unique(
                        out,
                        group_key=group_key,
                        links=_collect_minute_room_texts(
                            by_chat=by_chat,
                            minute=mk,
                            group_key=group_key,
                            before_max=timedelta(seconds=group_text_before_max_seconds),
                            max_gap=timedelta(minutes=group_text_max_gap_minutes),
                            diff_grace=timedelta(
                                seconds=different_sender_grace_seconds
                            ),
                            diff_max_chars=different_sender_max_chars,
                        ),
                    )
                else:
                    _attach_group_texts(
                        out,
                        group_key=group_key,
                        msgs=msgs,
                        first_photo_seq=slots[0].seq,
                        after_seq=slots[-1].seq,
                        first_photo_time=slots[0].abs_time,
                        last_photo_time=slots[-1].abs_time,
                        photo_sender=slots[0].sender,
                        before_max=timedelta(seconds=group_text_before_max_seconds),
                        max_gap=timedelta(minutes=group_text_max_gap_minutes),
                        diff_grace=timedelta(seconds=different_sender_grace_seconds),
                        diff_max_chars=different_sender_max_chars,
                    )
                if reason in ("partial_count_order", "multi_room_caption_union") or (
                    assign_n < len(slots) or assign_n < len(candidates)
                ):
                    out.reviews.append(
                        {
                            "kind": (
                                "match_multi_room_union"
                                if reason == "multi_room_caption_union"
                                else "match_partial"
                            ),
                            "group_key": group_key,
                            "reason": reason,
                            "confidence": confidence,
                            "slots": len(slots),
                            "assigned": assign_n,
                            "rooms": sorted(rooms),
                            "candidates": [p.rel_path for p in candidates[:20]],
                        }
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


def _commit_group_texts_unique(
    out: MatchOutput,
    *,
    group_key: str,
    links: list[GroupTextLink],
) -> None:
    """message_id 중복 제거 후 seq_in_group 재부여."""
    seen: set[int] = {
        gt.message_id for gt in out.group_texts if gt.group_key == group_key
    }
    seq = len(seen)
    for link in links:
        if link.message_id in seen:
            continue
        seen.add(link.message_id)
        seq += 1
        out.group_texts.append(
            GroupTextLink(
                group_key=group_key,
                message_id=link.message_id,
                seq_in_group=seq,
            )
        )


def _collect_minute_room_texts(
    *,
    by_chat: dict[int, list[dict[str, Any]]],
    minute: tuple,
    group_key: str,
    before_max: timedelta,
    max_gap: timedelta,
    diff_grace: timedelta,
    diff_max_chars: int,
) -> list[GroupTextLink]:
    """같은 분에 사진이 있는 모든 방의 앞/뒤 텍스트를 모은다."""
    collected: list[GroupTextLink] = []
    for _cid, msgs in sorted(by_chat.items(), key=lambda x: x[0]):
        for _anchor, slots in _photo_message_slots(msgs):
            if not slots:
                continue
            if minute_key(slots[0].abs_time) != minute:
                continue
            collected.extend(
                _collect_group_texts(
                    msgs=msgs,
                    group_key=group_key,
                    first_photo_seq=slots[0].seq,
                    after_seq=slots[-1].seq,
                    first_photo_time=slots[0].abs_time,
                    last_photo_time=slots[-1].abs_time,
                    photo_sender=slots[0].sender,
                    before_max=before_max,
                    max_gap=max_gap,
                    diff_grace=diff_grace,
                    diff_max_chars=diff_max_chars,
                )
            )
    return collected


def _collect_group_texts(
    *,
    msgs: list[dict[str, Any]],
    group_key: str,
    first_photo_seq: int,
    after_seq: int,
    first_photo_time: datetime,
    last_photo_time: datetime,
    photo_sender: str | None,
    before_max: timedelta,
    max_gap: timedelta,
    diff_grace: timedelta,
    diff_max_chars: int,
) -> list[GroupTextLink]:
    """
    사진 앞(≤before_max, 같은 sender) + 뒤(기존 max_gap) 설명 연결.
    순서는 앞→뒤. 매칭된 그룹 멤버가 동일 group_text를 공유한다.
    """
    links: list[GroupTextLink] = []
    seq_g = 0

    # --- 선행 텍스트: 첫 사진 직전을 역순 탐색 후 시간순으로 뒤집기 ---
    preceding: list[dict[str, Any]] = []
    for m in reversed(msgs):
        if int(m["seq"]) >= first_photo_seq:
            continue
        kind = m["msg_kind"]
        if kind in ("photo", "photo_multi"):
            break
        if kind == "system":
            break
        at = parse_iso(m["abs_time"]) if m.get("abs_time") else None
        if at is None:
            continue
        if first_photo_time - at > before_max:
            break
        sender = m.get("sender")
        if sender != photo_sender:
            break
        if kind != "text":
            break
        body = (m.get("body_raw") or "").strip()
        if not body:
            continue
        preceding.append(m)
    preceding.reverse()
    for m in preceding:
        seq_g += 1
        links.append(
            GroupTextLink(
                group_key=group_key, message_id=int(m["id"]), seq_in_group=seq_g
            )
        )

    # --- 후속 텍스트 (기존 규칙) ---
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
            continue
        if at - last_photo_time > max_gap:
            break
        sender = m.get("sender")
        body = m.get("body_raw") or ""
        if sender != photo_sender:
            # Phase1: 다른 발신자면 설명에 포함하지 않고 그룹 텍스트 수집 종료.
            if (at - last_photo_time) > diff_grace or len(body) > diff_max_chars:
                break
            break
        if kind != "text":
            break
        if not body.strip():
            continue
        seq_g += 1
        links.append(
            GroupTextLink(
                group_key=group_key, message_id=int(m["id"]), seq_in_group=seq_g
            )
        )

    return links


def _attach_group_texts(
    out: MatchOutput,
    *,
    group_key: str,
    msgs: list[dict[str, Any]],
    first_photo_seq: int,
    after_seq: int,
    first_photo_time: datetime,
    last_photo_time: datetime,
    photo_sender: str | None,
    before_max: timedelta,
    max_gap: timedelta,
    diff_grace: timedelta,
    diff_max_chars: int,
) -> None:
    """
    사진 앞(≤before_max, 같은 sender) + 뒤(기존 max_gap) 설명 연결.
    순서는 앞→뒤. 매칭된 그룹 멤버가 동일 group_text를 공유한다.
    """
    _commit_group_texts_unique(
        out,
        group_key=group_key,
        links=_collect_group_texts(
            msgs=msgs,
            group_key=group_key,
            first_photo_seq=first_photo_seq,
            after_seq=after_seq,
            first_photo_time=first_photo_time,
            last_photo_time=last_photo_time,
            photo_sender=photo_sender,
            before_max=before_max,
            max_gap=max_gap,
            diff_grace=diff_grace,
            diff_max_chars=diff_max_chars,
        ),
    )

# [변경사유]: Phase3 — dry-run 매니페스트 + (선택) HTTP 업로드 (쿠키 디스크 저장 금지)
# [변경사유]: Phase 3.5 — empty adjacent gate · file_missing · UTF-8 요약/결과 파일
"""Import upload 클라이언트."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kakao_import.config import Settings
from kakao_import.ledger import (
    forget_uploaded_sha,
    is_uploaded_sha,
    record_uploaded_sha,
    response_ledger_fields,
)
from kakao_import.logging_util import get_logger
from kakao_import.payload import (
    build_batch_manifest,
    exceeds_ingress_limit,
    is_attachment_marker_text,
    write_manifest,
)
from kakao_import.upload_state import sync_upload_candidates
from kakao_import.upload_state import (
    apply_candidate_classification,
    mark_candidate_retry,
    mark_candidate_uploaded,
    uploadable_candidate_keys,
)

log = get_logger(__name__)

# [변경사유]: caption-only 실패 시 파일 재전송
CAPTION_ONLY_FALLBACK_CODES = frozenset(
    {
        "CAPTION_ONLY_NO_EXACT",
        "CAPTION_ONLY_REQUIRES_FILE",
        "MISSING_FILE",
    }
)


class UploadHttpError(Exception):
    """HTTP 오류 + 서버 code (caption-only fallback 용)."""

    def __init__(self, status: int, code: str | None, preview: str):
        super().__init__(f"HTTP {status} {code or preview[:80]}")
        self.status = status
        self.code = (code or "").strip() or None
        self.preview = preview

# [변경사유]: 실전송 배치가 similar 판정·OCR job 을 연속 유발 → 서비스 부하 완화용 기본 유휴
#   요청 처리 시간(1~20s)과 별도로, 응답 후 대기. ocr_queued 는 워커 큐잉이 이어지므로 추가 대기.
DEFAULT_UPLOAD_SLEEP_SEC = 5.0
DEFAULT_UPLOAD_OCR_EXTRA_SEC = 5.0


def resolve_upload_pace_sec(
    *,
    sleep_sec: float | None = None,
    ocr_extra_sec: float | None = None,
) -> tuple[float, float]:
    """업로드 간격(초). env: KAKAO_IMPORT_UPLOAD_SLEEP_SEC / KAKAO_IMPORT_UPLOAD_OCR_EXTRA_SEC."""
    if sleep_sec is None:
        raw = (os.getenv("KAKAO_IMPORT_UPLOAD_SLEEP_SEC") or "").strip()
        sleep_sec = float(raw) if raw else DEFAULT_UPLOAD_SLEEP_SEC
    if ocr_extra_sec is None:
        raw_extra = (os.getenv("KAKAO_IMPORT_UPLOAD_OCR_EXTRA_SEC") or "").strip()
        ocr_extra_sec = (
            float(raw_extra) if raw_extra else DEFAULT_UPLOAD_OCR_EXTRA_SEC
        )
    return max(0.0, float(sleep_sec)), max(0.0, float(ocr_extra_sec))


def idle_after_upload_sec(
    *,
    base_sleep_sec: float,
    ocr_extra_sec: float,
    response: dict[str, Any] | None,
) -> float:
    """다음 업로드 전 대기 초. next=ocr_queued 이면 OCR 워커 여유를 위해 가산."""
    wait = max(0.0, float(base_sleep_sec))
    next_val = ""
    if isinstance(response, dict):
        next_val = str(response.get("next") or "")
        data = response.get("data")
        if not next_val and isinstance(data, dict):
            next_val = str(data.get("next") or "")
    if next_val == "ocr_queued":
        wait += max(0.0, float(ocr_extra_sec))
    return wait


def matched_messages_nonempty(payload: dict[str, Any]) -> bool:
    """item.matched_messages 에 실질 본문이 있는지."""
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    msgs = item.get("matched_messages") if isinstance(item, dict) else None
    if not isinstance(msgs, list) or not msgs:
        return False
    for m in msgs:
        if not isinstance(m, dict):
            continue
        text = str(m.get("text") or "").strip()
        # [변경사유]: '사진' 마커만 있으면 EMPTY 로 취급
        if text and not is_attachment_marker_text(text):
            return True
    return False


def classify_upload_requests(
    requests: list[dict[str, Any]],
    *,
    root: Path,
) -> dict[str, Any]:
    """
    upload 전 분류 — empty_adjacent / file_missing / ready.
    [변경사유]: Phase 3.5 P2·P3 — 게이트·요약용
    [변경사유]: Phase 4.2+ — 묶음 sub_file_rels 도 파일 존재 검사
    """
    photos_root = root / "photos"
    empty_adjacent: list[dict[str, Any]] = []
    file_missing: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []

    def _resolve_file(rel: str) -> Path | None:
        rel_n = str(rel or "").replace("\\", "/")
        candidates = [root / rel_n, photos_root / Path(rel_n).name]
        return next((p for p in candidates if p.is_file()), None)

    for req_payload in requests:
        rel = str(req_payload.get("file_rel") or "").replace("\\", "/")
        file_path = _resolve_file(rel)
        sub_rels = [
            str(r).replace("\\", "/")
            for r in (req_payload.get("sub_file_rels") or [])
            if r
        ]
        sub_paths: list[str] = []
        meta = {
            "rel": rel,
            "local_item_id": (req_payload.get("item") or {}).get("local_item_id"),
            "candidate_key": req_payload.get("candidate_key"),
            "sha_prefix": str((req_payload.get("item") or {}).get("sha256") or "")[:12],
            "sub_count": len(sub_rels),
        }
        if file_path is None:
            file_missing.append(meta)
            continue
        missing_sub = False
        for srel in sub_rels:
            sp = _resolve_file(srel)
            if sp is None:
                file_missing.append({**meta, "rel": srel, "bundle_main_rel": rel})
                missing_sub = True
                break
            sub_paths.append(str(sp))
        if missing_sub:
            continue
        if not matched_messages_nonempty(req_payload):
            empty_adjacent.append(
                {
                    **meta,
                    "file_path": str(file_path),
                    "sub_file_paths": sub_paths,
                }
            )
            continue
        ready.append(
            {
                **meta,
                "file_path": str(file_path),
                "sub_file_paths": sub_paths,
                "payload": req_payload,
            }
        )

    return {
        "empty_adjacent": empty_adjacent,
        "file_missing": file_missing,
        "ready": ready,
        "empty_adjacent_count": len(empty_adjacent),
        "file_missing_count": len(file_missing),
        "ready_count": len(ready),
    }


def build_upload_summary(
    *,
    dry_run: bool,
    classified: dict[str, Any],
    ok: int = 0,
    fail: int = 0,
    blocked: bool = False,
    allow_empty_caption: bool = False,
) -> dict[str, Any]:
    """콘솔용 요약 (본문·emoji JSON 최소화)."""
    return {
        "dry_run": dry_run,
        "blocked": blocked,
        "allow_empty_caption": allow_empty_caption,
        "OK": ok,
        "FAIL": fail,
        "EMPTY_CONTEXT": classified.get("empty_adjacent_count", 0),
        "FILE_MISSING": classified.get("file_missing_count", 0),
        "READY": classified.get("ready_count", 0),
        "SIMILAR_DEFERRED_BLOCKED": len(
            classified.get("similar_deferred_groups", []) or []
        ),
        "SIMILAR_SKIPPED_REPRESENTATIVE": classified.get(
            "similar_skipped_representative_count", 0
        ),
        "SIMILAR_SKIPPED_PARTIAL": classified.get(
            "similar_skipped_partial_count", 0
        ),
    }


def write_upload_result_json(path: Path, data: dict[str, Any]) -> None:
    """상세 결과 UTF-8 저장 (Windows cp949 콘솔 우회)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def echo_text_safe(text: str) -> None:
    """
    콘솔 출력. [변경사유]: cp949 환경에서 한글·이모지 UnicodeEncodeError 방지.
    인코딩 불가 시 stdout.buffer에 UTF-8로 기록.
    """
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        # 대상 인코딩으로 변환 가능하면 print
        text.encode(enc)
        print(text, flush=True)
    except (UnicodeEncodeError, LookupError):
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def echo_summary_safe(summary: dict[str, Any]) -> None:
    """
    콘솔에 ASCII-safe 요약만 출력.
    [변경사유]: Phase 3.5 P4 — emoji/cp949 UnicodeEncodeError 방지
    [변경사유]: 기존 한 줄 유지 + 한글 줄바꿈 요약 추가 (가독성)
    """
    line = (
        f"OK={summary.get('OK', 0)} "
        f"FAIL={summary.get('FAIL', 0)} "
        f"EMPTY_CONTEXT={summary.get('EMPTY_CONTEXT', 0)} "
        f"FILE_MISSING={summary.get('FILE_MISSING', 0)} "
        f"READY={summary.get('READY', 0)} "
        f"SIMILAR_DEFERRED_BLOCKED={summary.get('SIMILAR_DEFERRED_BLOCKED', 0)} "
        f"SIMILAR_SKIPPED_REPRESENTATIVE={summary.get('SIMILAR_SKIPPED_REPRESENTATIVE', 0)} "
        f"SIMILAR_SKIPPED_PARTIAL={summary.get('SIMILAR_SKIPPED_PARTIAL', 0)} "
        f"dry_run={summary.get('dry_run')} "
        f"blocked={summary.get('blocked')}"
    )
    echo_text_safe(line)

    ready = int(summary.get("READY") or 0)
    empty = int(summary.get("EMPTY_CONTEXT") or 0)
    item_count = summary.get("item_count")
    if item_count is None:
        item_count = ready + empty
    bundle_n = int(summary.get("bundle_collapsed_count") or 0)
    # [변경사유]: 기존 OK=… 한 줄은 유지하고, 아래에 한글 해석만 추가
    ko_lines = [
        "----- 업로드 요약 -----",
        f"총 업로드 컨텐츠 수: {item_count}",
        f"  (캡션 있음 READY={ready} + 이미지만 EMPTY={empty})",
        f"앨범 묶음(main+sub) 수: {bundle_n}",
        f"유사(대표만) 스킵: {summary.get('SIMILAR_SKIPPED_REPRESENTATIVE', 0)}",
        f"파일 없음: {summary.get('FILE_MISSING', 0)}",
        f"dry_run={summary.get('dry_run')} blocked={summary.get('blocked')}",
        "----------------------",
    ]
    echo_text_safe("\n".join(ko_lines))


def cmd_export_payload(
    settings: Settings,
    *,
    out: Path,
    limit: int | None = None,
    room_key: str | None = None,
) -> dict[str, Any]:
    """매니페스트만 생성 (전송 없음)."""
    manifest = build_batch_manifest(settings, limit=limit, room_key=room_key)
    write_manifest(manifest, out)
    return {
        "out": str(out),
        "item_count": manifest["item_count"],
        "batch_id": manifest["batch_id"],
    }


def _cookie_from_env() -> str | None:
    """
    세션 쿠키는 환경변수로만 주입.
    [변경사유]: 로컬 프로그램에 쿠키 파일 저장 금지 (Phase3 정책).
    """
    raw = (os.getenv("KAKAO_IMPORT_SESSION_COOKIE") or "").strip()
    return raw or None


def upload_one(
    *,
    endpoint: str,
    payload: dict[str, Any],
    file_path: Path,
    cookie: str,
    timeout_sec: int = 60,
    sub_file_paths: list[Path] | None = None,
    caption_only: bool = False,
) -> dict[str, Any]:
    """단건 multipart 업로드 (stdlib only). 묶음이면 sub_0… 추가.
    [변경사유]: caption_only=True 이면 file/sub 생략 (ledger SHA 재사용)
    """
    import uuid

    boundary = f"----kakaoImport{uuid.uuid4().hex}"
    body_payload = {
        k: v
        for k, v in payload.items()
        if k not in ("file_rel", "file_name", "sub_file_rels", "sub_file_names", "bundle")
    }
    # [변경사유]: 서버가 파일 없이 exact SNS append 하도록 표시
    if caption_only:
        body_payload = {**body_payload, "caption_only": True}
        item = body_payload.get("item")
        if isinstance(item, dict) and item.get("sub_images"):
            item = {**item, "sub_images": []}
            body_payload = {**body_payload, "item": item}
    payload_bytes = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
    filename = file_path.name

    parts: list[bytes] = []
    # [변경사유]: payload에 Content-Type을 넣으면 formidable이 필드를 파일로 취급
    # → maxFiles=1 초과(http 413) → 서버가 FILE_TOO_LARGE로 오인. 텍스트 필드는 mimetype 없이 전송.
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="payload"\r\n\r\n'
        ).encode()
        + payload_bytes
        + b"\r\n"
    )
    if not caption_only:
        file_bytes = file_path.read_bytes()
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + file_bytes
            + b"\r\n"
        )
        # [변경사유]: Phase 4.2+ — main 외 sub_i 필드
        for i, sub_path in enumerate(sub_file_paths or []):
            if not sub_path.is_file():
                continue
            sub_bytes = sub_path.read_bytes()
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="sub_{i}"; '
                    f'filename="{sub_path.name}"\r\n'
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                + sub_bytes
                + b"\r\n"
            )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": cookie,
            "Origin": endpoint.rsplit("/api/", 1)[0] or endpoint,
            "Referer": endpoint.rsplit("/api/", 1)[0] + "/",
        },
    )
    log.info(
        "upload_one sha_prefix=%s file=%s subs=%s caption_only=%s",
        str(body_payload.get("item", {}).get("sha256", ""))[:12],
        file_path.name,
        0 if caption_only else len(sub_file_paths or []),
        caption_only,
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        code = None
        try:
            parsed = json.loads(err_body)
            if isinstance(parsed, dict):
                code = str(parsed.get("code") or "") or None
        except json.JSONDecodeError:
            code = None
        log.warning(
            "upload HTTPError status=%s code=%s body=%s",
            e.code,
            code,
            err_body[:300],
        )
        raise UploadHttpError(int(e.code or 0), code, err_body[:300]) from e
    except URLError as e:
        log.warning("upload URLError %s", e)
        raise


def cmd_upload(
    settings: Settings,
    *,
    root: Path,
    dry_run: bool = True,
    limit: int | None = None,
    endpoint: str | None = None,
    out_manifest: Path | None = None,
    allow_empty_caption: bool = True,
    require_adjacent: bool = False,
    result_json: Path | None = None,
    sleep_sec: float | None = None,
    ocr_extra_sec: float | None = None,
    room_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    """
    dry_run=True(기본): 매니페스트만 기록 + empty/file_missing 통계.
    dry_run=False: READY + EMPTY(이미지만) 업로드. file_missing만 스킵.
    [변경사유]: 운영 — 포스터-only도 올려야 함. empty로 배치 전체 차단 금지.
    require_adjacent=True 일 때만 empty 있으면 전체 차단 (엄격 모드).
    [변경사유]: 실전송 시 장당 유휴(sleep) — 서비스 similar/OCR 부하 완화.
    [변경사유]: room_ids 있으면 해당 방 후보만.
    """
    photos_root = root / "photos"
    pace_sleep, pace_ocr_extra = resolve_upload_pace_sec(
        sleep_sec=sleep_sec, ocr_extra_sec=ocr_extra_sec
    )
    manifest = build_batch_manifest(settings, limit=limit, room_ids=room_ids)
    out = out_manifest or (settings.db_path.parent / "last_upload_manifest.json")
    write_manifest(manifest, out)
    candidate_sync = sync_upload_candidates(
        settings.db_path,
        requests=list(manifest["requests"]),
    )
    actionable_requests = list(candidate_sync.get("actionable_requests") or [])

    classified = classify_upload_requests(actionable_requests, root=root)
    classification_state = apply_candidate_classification(
        settings.db_path,
        classified=classified,
    )
    allowed_keys = uploadable_candidate_keys(
        settings.db_path,
        candidate_keys=[
            str(r.get("candidate_key") or "")
            for r in actionable_requests
            if str(r.get("candidate_key") or "").strip()
        ],
    )
    similar_policy = manifest.get("similar_policy") or {}
    similar_skipped = list(similar_policy.get("skipped") or [])
    similar_deferred_groups = list(similar_policy.get("deferred_groups") or [])
    classified["similar_skipped"] = similar_skipped
    classified["similar_deferred_groups"] = similar_deferred_groups
    classified["similar_skipped_representative_count"] = sum(
        1 for item in similar_skipped if item.get("reason") == "similar_non_representative"
    )
    classified["similar_skipped_partial_count"] = sum(
        1
        for item in similar_skipped
        if item.get("reason") == "similar_partial_non_representative"
    )
    result_path = result_json or (settings.db_path.parent / "upload-result.json")

    # [변경사유]: allow_empty_caption 기본 True — False만 오면 require_adjacent와 동일 취급(하위호환)
    strict_adjacent = require_adjacent or (allow_empty_caption is False)

    if dry_run:
        summary = build_upload_summary(
            dry_run=True,
            classified=classified,
            blocked=False,
            allow_empty_caption=not strict_adjacent,
        )
        detail = {
            "summary": summary,
            "manifest": str(out),
            "batch_id": manifest["batch_id"],
            "item_count": manifest["item_count"],
            "empty_adjacent": classified["empty_adjacent"],
            "file_missing": classified["file_missing"],
            "similar_skipped": similar_skipped,
            "similar_deferred_groups": similar_deferred_groups,
            "grouped_photo_candidates": list(
                manifest.get("grouped_photo_candidates") or []
            ),
            "bundled_groups": list(manifest.get("bundled_groups") or []),
            "bundle_collapsed_count": int(
                manifest.get("bundle_collapsed_count") or 0
            ),
            "candidate_sync": candidate_sync,
            "classification_state": classification_state,
            "incremental": dict(manifest.get("incremental") or {}),
            # [변경사유]: dry-run은 payload 본문 제외 — 경로·id만
            "ready_rels": [r.get("rel") for r in classified["ready"]],
            "note": "EMPTY_CONTEXT items are uploaded by default (poster-only OK); similar_deferred items are held and skipped",
        }
        write_upload_result_json(result_path, detail)
        log.info(
            "upload dry-run items=%s empty=%s missing=%s ready=%s",
            manifest["item_count"],
            classified["empty_adjacent_count"],
            classified["file_missing_count"],
            classified["ready_count"],
        )
        return {
            **summary,
            "item_count": manifest["item_count"],
            # [변경사유]: 콘솔 한글 요약용 — 기존 필드 유지 + 묶음 수 전달
            "bundle_collapsed_count": int(
                manifest.get("bundle_collapsed_count") or 0
            ),
            "candidate_sync": candidate_sync,
            "classification_state": classification_state,
            "manifest": str(out),
            "batch_id": manifest["batch_id"],
            "result_json": str(result_path),
        }
    # [변경사유]: 엄격 모드(--require-adjacent)만 empty 시 전체 차단
    if classified["empty_adjacent_count"] > 0 and strict_adjacent:
        summary = build_upload_summary(
            dry_run=False,
            classified=classified,
            blocked=True,
            allow_empty_caption=False,
        )
        detail = {
            "summary": summary,
            "error": "EMPTY_ADJACENT_BLOCKED",
            "hint": "default uploads poster-only; omit --require-adjacent",
            "empty_adjacent": classified["empty_adjacent"],
            "file_missing": classified["file_missing"],
            "similar_skipped": similar_skipped,
            "similar_deferred_groups": similar_deferred_groups,
            "candidate_sync": candidate_sync,
            "classification_state": classification_state,
            "manifest": str(out),
        }
        write_upload_result_json(result_path, detail)
        log.warning(
            "upload blocked EMPTY_CONTEXT=%s (--require-adjacent)",
            classified["empty_adjacent_count"],
        )
        return {
            **summary,
            "item_count": manifest["item_count"],
            "manifest": str(out),
            "result_json": str(result_path),
            "error": "EMPTY_ADJACENT_BLOCKED",
        }

    cookie = _cookie_from_env()
    if not cookie:
        raise RuntimeError(
            "실전송에는 KAKAO_IMPORT_SESSION_COOKIE 환경변수 필요 (파일 저장 금지)"
        )
    ep = (endpoint or os.getenv("KAKAO_IMPORT_ENDPOINT") or "").strip()
    if not ep:
        raise RuntimeError(
            "실전송에는 --endpoint 또는 KAKAO_IMPORT_ENDPOINT 필요"
        )

    results: list[dict[str, Any]] = []

    # file_missing — 업로드 시도 없이 기록
    for miss in classified["file_missing"]:
        results.append(
            {
                "ok": False,
                "error": "file_missing",
                "rel": miss.get("rel"),
            }
        )

    # [변경사유]: READY + EMPTY(이미지만) 모두 전송 — file_missing만 제외
    upload_queue: list[dict[str, Any]] = [
        entry
        for entry in classified["ready"]
        if (
            not str(entry.get("candidate_key") or "").strip()
            or str(entry.get("candidate_key") or "") in allowed_keys
        )
    ]
    for ea in classified["empty_adjacent"]:
        rel = str(ea.get("rel") or "")
        req = next(
            (
                r
                for r in actionable_requests
                if str(r.get("file_rel") or "").replace("\\", "/") == rel
                and (
                    not str(r.get("candidate_key") or "").strip()
                    or str(r.get("candidate_key") or "") in allowed_keys
                )
            ),
            None,
        )
        if req is None:
            continue
        fp = Path(str(ea.get("file_path") or ""))
        if not fp.is_file():
            results.append({"ok": False, "error": "file_missing", "rel": rel})
            continue
        upload_queue.append(
            {
                **ea,
                "payload": req,
                "file_path": str(fp),
                "sub_file_paths": list(ea.get("sub_file_paths") or []),
            }
        )

    log.info(
        "upload pace sleep_sec=%s ocr_extra_sec=%s queue=%s",
        pace_sleep,
        pace_ocr_extra,
        len(upload_queue),
    )
    for idx, entry in enumerate(upload_queue):
        req_payload = entry["payload"]
        candidate_key = str(req_payload.get("candidate_key") or "").strip()
        file_path = Path(entry["file_path"])
        sub_paths = [Path(p) for p in (entry.get("sub_file_paths") or [])]
        rel = str(entry.get("rel") or "")
        try:
            sz = file_path.stat().st_size
        except OSError:
            sz = -1
        if exceeds_ingress_limit(sz):
            log.warning(
                "skip upload oversized rel=%s bytes=%s error=FILE_EXCEEDS_INGRESS_LIMIT",
                rel,
                sz,
            )
            results.append(
                {
                    "ok": False,
                    "error": "FILE_EXCEEDS_INGRESS_LIMIT",
                    "rel": rel,
                    "bytes": sz,
                }
            )
            if candidate_key:
                mark_candidate_retry(
                    settings.db_path,
                    candidate_key=candidate_key,
                    error_code="FILE_EXCEEDS_INGRESS_LIMIT",
                    error_message=f"main file too large: {sz}",
                    terminal=True,
                )
            continue
        # [변경사유]: Phase 4.2+ — sub 도 파일별 ingress 검사
        oversized_sub = False
        for sp in sub_paths:
            try:
                ssz = sp.stat().st_size
            except OSError:
                ssz = -1
            if exceeds_ingress_limit(ssz):
                log.warning(
                    "skip upload oversized sub rel=%s bytes=%s",
                    sp.name,
                    ssz,
                )
                results.append(
                    {
                        "ok": False,
                        "error": "FILE_EXCEEDS_INGRESS_LIMIT",
                        "rel": str(sp),
                        "bundle_main_rel": rel,
                        "bytes": ssz,
                    }
                )
                if candidate_key:
                    mark_candidate_retry(
                        settings.db_path,
                        candidate_key=candidate_key,
                        error_code="FILE_EXCEEDS_INGRESS_LIMIT",
                        error_message=f"sub file too large: {ssz}",
                        terminal=True,
                    )
                oversized_sub = True
                break
        if oversized_sub:
            continue
        resp: dict[str, Any] | None = None
        item = req_payload.get("item") if isinstance(req_payload.get("item"), dict) else {}
        source_sha = str((item or {}).get("sha256") or "").strip().lower()
        # [변경사유]: 장부에 있고 묶음이 아니면 파일 생략 (caption-only)
        caption_only = (
            not sub_paths
            and len(source_sha) == 64
            and is_uploaded_sha(settings.db_path, source_sha)
        )
        try:
            try:
                resp = upload_one(
                    endpoint=ep,
                    payload=req_payload,
                    file_path=file_path,
                    cookie=cookie,
                    sub_file_paths=sub_paths,
                    caption_only=caption_only,
                )
            except UploadHttpError as e:
                # [변경사유]: 서버가 exact를 못 찾으면 장부 지우고 파일 재전송 1회
                if (
                    caption_only
                    and e.code in CAPTION_ONLY_FALLBACK_CODES
                    and file_path.is_file()
                ):
                    log.info(
                        "caption-only fallback rel=%s code=%s",
                        rel,
                        e.code,
                    )
                    forget_uploaded_sha(settings.db_path, source_sha)
                    resp = upload_one(
                        endpoint=ep,
                        payload=req_payload,
                        file_path=file_path,
                        cookie=cookie,
                        sub_file_paths=sub_paths,
                        caption_only=False,
                    )
                    caption_only = False
                else:
                    raise
            results.append(
                {
                    "ok": True,
                    "rel": rel,
                    "sub_count": 0 if caption_only else len(sub_paths),
                    "caption_only": caption_only,
                    "response": resp,
                }
            )
            fields = response_ledger_fields(resp)
            rec_sha = fields.get("source_sha256") or source_sha
            next_val = fields.get("next_val")
            if rec_sha:
                # [변경사유]: ocr_idx·거부·지문을 장부에 남겨 caption-only/재실행에 사용
                record_uploaded_sha(
                    settings.db_path,
                    source_sha256=rec_sha,
                    request_idx=fields.get("request_idx"),
                    next_val=next_val,
                    final_sha_prefix=fields.get("final_sha_prefix"),
                    ocr_idx=fields.get("ocr_idx"),
                    media_fingerprint=str(req_payload.get("media_fingerprint") or "")
                    or None,
                    caption_fingerprint=str(req_payload.get("caption_fingerprint") or "")
                    or None,
                    rejected=next_val == "already_rejected",
                )
            if candidate_key:
                mark_candidate_uploaded(
                    settings.db_path,
                    candidate_key=candidate_key,
                    request_idx=fields.get("request_idx"),
                    ocr_idx=fields.get("ocr_idx"),
                )
        except Exception as e:  # noqa: BLE001 — 배치 계속
            results.append({"ok": False, "error": str(e), "rel": rel})
            if candidate_key:
                terminal = False
                err_code = e.__class__.__name__
                if isinstance(e, UploadHttpError):
                    err_code = str(e.code or f"HTTP_{e.status}")
                    terminal = int(e.status or 0) >= 400 and int(e.status or 0) < 500
                mark_candidate_retry(
                    settings.db_path,
                    candidate_key=candidate_key,
                    error_code=err_code,
                    error_message=str(e),
                    terminal=terminal,
                )

        # [변경사유]: 마지막 건 제외 — 장당 유휴로 frontend/OCR/similar 부하 완화
        if idx < len(upload_queue) - 1:
            wait = idle_after_upload_sec(
                base_sleep_sec=pace_sleep,
                ocr_extra_sec=pace_ocr_extra,
                response=resp,
            )
            if wait > 0:
                log.info(
                    "upload idle %.1fs after rel=%s next=%s (%s/%s)",
                    wait,
                    Path(rel).name,
                    (resp or {}).get("next"),
                    idx + 1,
                    len(upload_queue),
                )
                time.sleep(wait)

    ok_n = sum(1 for r in results if r.get("ok"))
    fail_n = len(results) - ok_n
    summary = build_upload_summary(
        dry_run=False,
        classified=classified,
        ok=ok_n,
        fail=fail_n,
        allow_empty_caption=not strict_adjacent,
    )
    summary["UPLOAD_SLEEP_SEC"] = pace_sleep
    summary["UPLOAD_OCR_EXTRA_SEC"] = pace_ocr_extra
    detail = {
        "summary": summary,
        "manifest": str(out),
        "results": results,
        "empty_adjacent": classified["empty_adjacent"],
        "file_missing": classified["file_missing"],
        "similar_skipped": similar_skipped,
        "similar_deferred_groups": similar_deferred_groups,
        "candidate_sync": candidate_sync,
        "classification_state": classification_state,
        "grouped_photo_candidates": list(manifest.get("grouped_photo_candidates") or []),
        "bundled_groups": list(manifest.get("bundled_groups") or []),
        "bundle_collapsed_count": int(manifest.get("bundle_collapsed_count") or 0),
    }
    write_upload_result_json(result_path, detail)
    log.info(
        "upload done OK=%s FAIL=%s EMPTY_CONTEXT=%s FILE_MISSING=%s sleep=%s ocr_extra=%s",
        ok_n,
        fail_n,
        classified["empty_adjacent_count"],
        classified["file_missing_count"],
        pace_sleep,
        pace_ocr_extra,
    )
    # photos_root unused warning avoid — kept for path parity with classify
    _ = photos_root
    return {
        **summary,
        "item_count": len(results),
        # [변경사유]: 콘솔 한글 요약용
        "bundle_collapsed_count": int(manifest.get("bundle_collapsed_count") or 0),
        "candidate_sync": candidate_sync,
        "classification_state": classification_state,
        "ok": ok_n,
        "fail": fail_n,
        "manifest": str(out),
        "result_json": str(result_path),
    }

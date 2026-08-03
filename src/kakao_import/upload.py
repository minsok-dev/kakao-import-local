# [변경사유]: Phase3 — dry-run 매니페스트 + (선택) HTTP 업로드 (쿠키 디스크 저장 금지)
# [변경사유]: Phase 3.5 — empty adjacent gate · file_missing · UTF-8 요약/결과 파일
"""Import upload 클라이언트."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kakao_import.config import Settings
from kakao_import.logging_util import get_logger
from kakao_import.payload import (
    build_batch_manifest,
    exceeds_ingress_limit,
    write_manifest,
)

log = get_logger(__name__)


def matched_messages_nonempty(payload: dict[str, Any]) -> bool:
    """item.matched_messages 에 실질 본문이 있는지."""
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    msgs = item.get("matched_messages") if isinstance(item, dict) else None
    if not isinstance(msgs, list) or not msgs:
        return False
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if str(m.get("text") or "").strip():
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
    """
    photos_root = root / "photos"
    empty_adjacent: list[dict[str, Any]] = []
    file_missing: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []

    for req_payload in requests:
        rel = str(req_payload.get("file_rel") or "").replace("\\", "/")
        candidates = [root / rel, photos_root / Path(rel).name]
        file_path = next((p for p in candidates if p.is_file()), None)
        meta = {
            "rel": rel,
            "local_item_id": (req_payload.get("item") or {}).get("local_item_id"),
            "sha_prefix": str((req_payload.get("item") or {}).get("sha256") or "")[:12],
        }
        if file_path is None:
            file_missing.append(meta)
            continue
        if not matched_messages_nonempty(req_payload):
            empty_adjacent.append({**meta, "file_path": str(file_path)})
            continue
        ready.append({**meta, "file_path": str(file_path), "payload": req_payload})

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
    }


def write_upload_result_json(path: Path, data: dict[str, Any]) -> None:
    """상세 결과 UTF-8 저장 (Windows cp949 콘솔 우회)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def echo_summary_safe(summary: dict[str, Any]) -> None:
    """
    콘솔에 ASCII-safe 요약만 출력.
    [변경사유]: Phase 3.5 P4 — emoji/cp949 UnicodeEncodeError 방지
    """
    line = (
        f"OK={summary.get('OK', 0)} "
        f"FAIL={summary.get('FAIL', 0)} "
        f"EMPTY_CONTEXT={summary.get('EMPTY_CONTEXT', 0)} "
        f"FILE_MISSING={summary.get('FILE_MISSING', 0)} "
        f"READY={summary.get('READY', 0)} "
        f"dry_run={summary.get('dry_run')} "
        f"blocked={summary.get('blocked')}"
    )
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


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
) -> dict[str, Any]:
    """단건 multipart 업로드 (stdlib only)."""
    import uuid

    boundary = f"----kakaoImport{uuid.uuid4().hex}"
    body_payload = {
        k: v
        for k, v in payload.items()
        if k not in ("file_rel", "file_name")
    }
    payload_bytes = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
    file_bytes = file_path.read_bytes()
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
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + file_bytes
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
        "upload_one sha_prefix=%s file=%s",
        str(body_payload.get("item", {}).get("sha256", ""))[:12],
        file_path.name,
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log.warning("upload HTTPError status=%s body=%s", e.code, err_body[:300])
        raise
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
) -> dict[str, Any]:
    """
    dry_run=True(기본): 매니페스트만 기록 + empty/file_missing 통계.
    dry_run=False: READY + EMPTY(이미지만) 업로드. file_missing만 스킵.
    [변경사유]: 운영 — 포스터-only도 올려야 함. empty로 배치 전체 차단 금지.
    require_adjacent=True 일 때만 empty 있으면 전체 차단 (엄격 모드).
    """
    photos_root = root / "photos"
    manifest = build_batch_manifest(settings, limit=limit)
    out = out_manifest or (settings.db_path.parent / "last_upload_manifest.json")
    write_manifest(manifest, out)

    classified = classify_upload_requests(manifest["requests"], root=root)
    result_path = result_json or (settings.db_path.parent / "upload-result.json")

    # [변경사유]: allow_empty_caption 기본 True — False만 오면 require_adjacent와 동일 취급(하위호환)
    strict_adjacent = require_adjacent or (allow_empty_caption is False)

    if dry_run:
        summary = build_upload_summary(
            dry_run=True,
            classified=classified,
            allow_empty_caption=not strict_adjacent,
        )
        detail = {
            "summary": summary,
            "manifest": str(out),
            "batch_id": manifest["batch_id"],
            "item_count": manifest["item_count"],
            "empty_adjacent": classified["empty_adjacent"],
            "file_missing": classified["file_missing"],
            # [변경사유]: dry-run은 payload 본문 제외 — 경로·id만
            "ready_rels": [r.get("rel") for r in classified["ready"]],
            "note": "EMPTY_CONTEXT items are uploaded by default (poster-only OK)",
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
    upload_queue: list[dict[str, Any]] = list(classified["ready"])
    for ea in classified["empty_adjacent"]:
        rel = str(ea.get("rel") or "")
        req = next(
            (
                r
                for r in manifest["requests"]
                if str(r.get("file_rel") or "").replace("\\", "/") == rel
            ),
            None,
        )
        if req is None:
            continue
        fp = Path(str(ea.get("file_path") or ""))
        if not fp.is_file():
            results.append({"ok": False, "error": "file_missing", "rel": rel})
            continue
        upload_queue.append({**ea, "payload": req, "file_path": str(fp)})

    for entry in upload_queue:
        req_payload = entry["payload"]
        file_path = Path(entry["file_path"])
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
            continue
        try:
            resp = upload_one(
                endpoint=ep,
                payload=req_payload,
                file_path=file_path,
                cookie=cookie,
            )
            results.append({"ok": True, "rel": rel, "response": resp})
        except Exception as e:  # noqa: BLE001 — 배치 계속
            results.append({"ok": False, "error": str(e), "rel": rel})

    ok_n = sum(1 for r in results if r.get("ok"))
    fail_n = len(results) - ok_n
    summary = build_upload_summary(
        dry_run=False,
        classified=classified,
        ok=ok_n,
        fail=fail_n,
        allow_empty_caption=not strict_adjacent,
    )
    detail = {
        "summary": summary,
        "manifest": str(out),
        "results": results,
        "empty_adjacent": classified["empty_adjacent"],
        "file_missing": classified["file_missing"],
    }
    write_upload_result_json(result_path, detail)
    log.info(
        "upload done OK=%s FAIL=%s EMPTY_CONTEXT=%s FILE_MISSING=%s",
        ok_n,
        fail_n,
        classified["empty_adjacent_count"],
        classified["file_missing_count"],
    )
    # photos_root unused warning avoid — kept for path parity with classify
    _ = photos_root
    return {
        **summary,
        "item_count": len(results),
        "ok": ok_n,
        "fail": fail_n,
        "manifest": str(out),
        "result_json": str(result_path),
    }

# [변경사유]: Phase3 — dry-run 매니페스트 + (선택) HTTP 업로드 (쿠키 디스크 저장 금지)
"""Import upload 클라이언트."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kakao_import.config import Settings
from kakao_import.logging_util import get_logger
from kakao_import.payload import build_batch_manifest, write_manifest

log = get_logger(__name__)


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
) -> dict[str, Any]:
    """
    dry_run=True(기본): 매니페스트만 기록.
    dry_run=False: KAKAO_IMPORT_SESSION_COOKIE + endpoint 로 전송 (쿠키 저장 안 함).
    """
    photos_root = root / "photos"
    manifest = build_batch_manifest(settings, limit=limit)
    out = out_manifest or (settings.db_path.parent / "last_upload_manifest.json")
    write_manifest(manifest, out)

    if dry_run:
        log.info("upload dry-run only items=%s", manifest["item_count"])
        return {
            "dry_run": True,
            "item_count": manifest["item_count"],
            "manifest": str(out),
            "batch_id": manifest["batch_id"],
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
    for req_payload in manifest["requests"]:
        rel = str(req_payload.get("file_rel") or "").replace("\\", "/")
        # [변경사유]: DB rel_path는 export_root 기준 (예: photos/KakaoTalk_....jpg)
        candidates = [
            root / rel,
            photos_root / Path(rel).name,
        ]
        file_path = next((p for p in candidates if p.is_file()), None)
        if file_path is None:
            results.append(
                {
                    "ok": False,
                    "error": "file_missing",
                    "rel": rel,
                }
            )
            continue
        # [변경사유]: 전송 직전 크기 재확인 (DB byte_size drift 대비)
        try:
            sz = file_path.stat().st_size
        except OSError:
            sz = -1
        if sz > 15 * 1024 * 1024:
            log.warning(
                "skip upload oversized rel=%s bytes=%s",
                rel,
                sz,
            )
            results.append(
                {
                    "ok": False,
                    "error": "FILE_TOO_LARGE_LOCAL",
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
            results.append({"ok": True, "response": resp})
        except Exception as e:  # noqa: BLE001 — 배치 계속
            results.append({"ok": False, "error": str(e), "rel": rel})

    ok_n = sum(1 for r in results if r.get("ok"))
    log.info("upload done ok=%s fail=%s", ok_n, len(results) - ok_n)
    return {
        "dry_run": False,
        "item_count": len(results),
        "ok": ok_n,
        "fail": len(results) - ok_n,
        "manifest": str(out),
        "results": results,
    }

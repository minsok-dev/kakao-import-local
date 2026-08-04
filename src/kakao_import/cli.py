# [변경사유]: Phase1 CLI — init/scan/parse/match/hash/report/run
# [변경사유]: Phase3 — export-payload / upload (dry-run 기본)
"""Click CLI."""

from __future__ import annotations

import json
from pathlib import Path

import click

from kakao_import import __version__
from kakao_import import pipeline as pipe
from kakao_import import upload as upload_mod
from kakao_import.config import load_settings
from kakao_import.db import status_counts
from kakao_import.logging_util import get_logger


@click.group()
@click.version_option(__version__, prog_name="kakao-import")
@click.pass_context
def main(ctx: click.Context) -> None:
    """카카오톡 로컬 수집·매칭 (Phase 1)."""
    settings = load_settings()
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings
    get_logger("kakao_import", settings.log_level)


def _root_opt(ctx: click.Context, root: Path | None) -> Path:
    settings = ctx.obj["settings"]
    target = root or settings.export_root
    if target is None:
        raise click.UsageError("--root 또는 KAKAO_EXPORT_ROOT 필요")
    return Path(target)


@main.command("init")
@click.option("--reset", is_flag=True, help="기존 DB 삭제 후 재생성")
@click.pass_context
def init_cmd(ctx: click.Context, reset: bool) -> None:
    """SQLite 스키마 초기화."""
    pipe.cmd_init(ctx.obj["settings"], reset=reset)
    click.echo(f"OK init db={ctx.obj['settings'].db_path.name} reset={reset}")


# [변경사유]: 하위 호환 별칭
@main.command("init-db")
@click.option("--reset", is_flag=True)
@click.pass_context
def init_db_alias(ctx: click.Context, reset: bool) -> None:
    """init 별칭."""
    ctx.invoke(init_cmd, reset=reset)


@main.command("scan")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.pass_context
def scan_cmd(ctx: click.Context, root: Path | None) -> None:
    """photos/ 인덱스 (파일명 시각)."""
    if not ctx.obj["settings"].db_path.exists():
        pipe.cmd_init(ctx.obj["settings"])
    summary = pipe.cmd_scan(ctx.obj["settings"], _root_opt(ctx, root))
    click.echo(f"OK scan → {summary}")


@main.command("parse")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.pass_context
def parse_cmd(ctx: click.Context, root: Path | None) -> None:
    """chats/*.txt 파싱."""
    if not ctx.obj["settings"].db_path.exists():
        pipe.cmd_init(ctx.obj["settings"])
    summary = pipe.cmd_parse(ctx.obj["settings"], _root_opt(ctx, root))
    click.echo(f"OK parse → {summary}")


@main.command("match")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.pass_context
def match_cmd(ctx: click.Context, root: Path | None) -> None:
    """사진↔메시지 시각 매칭."""
    summary = pipe.cmd_match(ctx.obj["settings"], _root_opt(ctx, root))
    click.echo(f"OK match → {summary}")


@main.command("hash")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.pass_context
def hash_cmd(ctx: click.Context, root: Path | None) -> None:
    """SHA-256 + exact 그룹."""
    summary = pipe.cmd_hash(ctx.obj["settings"], _root_opt(ctx, root))
    click.echo(f"OK hash → {summary}")


@main.command("merge")
@click.option("--mode", type=click.Choice(["safe", "balanced", "auto"]), default=None)
@click.pass_context
def merge_cmd(ctx: click.Context, mode: str | None) -> None:
    """Phase2: exact SHA 텍스트 collapse/merge."""
    summary = pipe.cmd_merge(ctx.obj["settings"], mode=mode)
    click.echo(f"OK merge → {summary}")


@main.command("merge-undo")
@click.option("--id", "merge_id", type=int, default=None, help="text_merge.id")
@click.option("--sha256", default=None, help="exact SHA (active 1건)")
@click.pass_context
def merge_undo_cmd(
    ctx: click.Context, merge_id: int | None, sha256: str | None
) -> None:
    """Phase2: 마지막 active merge 되돌리기 (직전 superseded 복구)."""
    if merge_id is None and not sha256:
        raise click.UsageError("--id 또는 --sha256 필요")
    try:
        out = pipe.cmd_merge_undo(
            ctx.obj["settings"], merge_id=merge_id, sha256=sha256
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(out, ensure_ascii=False, indent=2))


@main.command("merge-decide")
@click.option("--id", "merge_id", type=int, required=True, help="text_merge.id")
@click.option(
    "--action",
    type=click.Choice(["accept", "set-text", "reject"]),
    required=True,
    help="accept=병합승인 / set-text=수동문장 / reject=병합포기",
)
@click.option("--text", default=None, help="set-text 시 병합 문장")
@click.option("--note", default=None, help="수동 결정 메모")
@click.pass_context
def merge_decide_cmd(
    ctx: click.Context,
    merge_id: int,
    action: str,
    text: str | None,
    note: str | None,
) -> None:
    """Phase2: review merge 수동 결정."""
    try:
        out = pipe.cmd_merge_decide(
            ctx.obj["settings"],
            merge_id=merge_id,
            action=action,
            text=text,
            note=note,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(out, ensure_ascii=False, indent=2))


@main.command("report")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--json", "as_json", is_flag=True, help="JSON 출력")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.pass_context
def report_cmd(
    ctx: click.Context, root: Path | None, as_json: bool, output: Path | None
) -> None:
    """매칭 리포트."""
    report = pipe.build_report(
        ctx.obj["settings"],
        _root_opt(ctx, root) if root or ctx.obj["settings"].export_root else None,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"OK report → {output.name}")
    elif as_json:
        click.echo(text)
    else:
        click.echo(
            f"rooms={report['rooms']} messages={report['messages']} "
            f"photo_msgs={report['photo_messages']}"
        )
        click.echo(
            f"photos={report['photos']} confidence={report['confidence']} "
            f"exact={report['exact_groups']} text_merge={report.get('text_merge')}"
        )
        eg = report.get("esencia_golden") or {}
        click.echo(f"esencia_golden passed={eg.get('passed')} reasons={eg.get('reasons')}")
        click.echo(f"review_pending={len(report.get('review_required') or [])}")


@main.command("run")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def run_cmd(ctx: click.Context, root: Path | None, as_json: bool) -> None:
    """Phase1+2: init(필요시)→scan→parse→match→hash→merge→report."""
    out = pipe.cmd_run(ctx.obj["settings"], _root_opt(ctx, root))
    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        click.echo(f"OK run scan={out['scan']} parse={out['parse']}")
        click.echo(f"OK run match={out['match']} hash={out['hash']} merge={out.get('merge')}")
        eg = (out.get("report") or {}).get("esencia_golden") or {}
        click.echo(f"OK esencia_golden passed={eg.get('passed')}")


@main.command("status")
@click.pass_context
def status_cmd(ctx: click.Context) -> None:
    """DB 건수."""
    for k, v in status_counts(ctx.obj["settings"].db_path).items():
        click.echo(f"{k}: {v}")


@main.command("export-payload")
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="매니페스트 JSON 경로 (기본 data/last_upload_manifest.json)",
)
@click.option("--limit", type=int, default=None, help="최대 item 수")
@click.option("--room-key", default=None, help="선택적 room_key")
@click.pass_context
def export_payload_cmd(
    ctx: click.Context,
    output: Path | None,
    limit: int | None,
    room_key: str | None,
) -> None:
    """Phase3: Import 매니페스트만 생성 (전송 없음)."""
    settings = ctx.obj["settings"]
    out = output or (settings.db_path.parent / "last_upload_manifest.json")
    summary = upload_mod.cmd_export_payload(
        settings, out=out, limit=limit, room_key=room_key
    )
    click.echo(f"OK export-payload → {summary}")


@main.command("upload")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    show_default=True,
    help="기본 dry-run. 실전송은 --no-dry-run + 세션 쿠키 env",
)
@click.option("--limit", type=int, default=None)
@click.option("--endpoint", default=None, help="예: https://host/api/admin/ingest/import/kakao")
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="매니페스트 출력 경로",
)
@click.option(
    "--allow-empty-caption/--no-allow-empty-caption",
    default=True,
    show_default=True,
    help="이미지만(인접 메시지 없음) 업로드 허용. 기본 허용(운영 필수)",
)
@click.option(
    "--require-adjacent",
    is_flag=True,
    default=False,
    help="엄격 모드: empty adjacent가 있으면 배치 전체 차단",
)
@click.option(
    "--result-json",
    type=click.Path(path_type=Path),
    default=None,
    help="상세 결과 UTF-8 JSON (기본 data/upload-result.json)",
)
@click.pass_context
def upload_cmd(
    ctx: click.Context,
    root: Path | None,
    dry_run: bool,
    limit: int | None,
    endpoint: str | None,
    output: Path | None,
    allow_empty_caption: bool,
    require_adjacent: bool,
    result_json: Path | None,
) -> None:
    """Phase3: Import 업로드 (기본 dry-run). 이미지만 있는 건도 기본 업로드."""
    settings = ctx.obj["settings"]
    summary = upload_mod.cmd_upload(
        settings,
        root=_root_opt(ctx, root),
        dry_run=dry_run,
        limit=limit,
        endpoint=endpoint,
        out_manifest=output,
        allow_empty_caption=allow_empty_caption,
        require_adjacent=require_adjacent,
        result_json=result_json,
    )
    # [변경사유]: Phase 3.5 P4 — 콘솔은 요약만 (전체 JSON+emoji로 cp949 깨짐 방지)
    upload_mod.echo_summary_safe(summary)
    if summary.get("result_json"):
        click.echo(f"result_json={summary['result_json']}")
    if summary.get("error"):
        raise click.ClickException(str(summary["error"]))


# [변경사유]: Phase 4.0 — similar 탐지 CLI (upload 큐 미변경, decision≠삭제/자동병합)
def _settings_with_db(ctx: click.Context, db: Path | None):
    """ctx settings + 선택적 --db 오버라이드."""
    from dataclasses import replace

    settings = ctx.obj["settings"]
    if db is None:
        return settings
    return replace(settings, db_path=Path(db))


@main.command("similar-detect")
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option(
    "--max-distance",
    type=int,
    default=None,
    help="min(dHash,pHash) 상한 (기본 SIMILAR_MAX_DISTANCE=10)",
)
@click.option("--limit", type=int, default=None, help="서명 계산 상한 (테스트용)")
@click.option("--force-resign", is_flag=True, help="기존 signature 재계산")
@click.pass_context
def similar_detect_cmd(
    ctx: click.Context,
    db: Path | None,
    root: Path | None,
    max_distance: int | None,
    limit: int | None,
    force_resign: bool,
) -> None:
    """사진 signature 계산 + similar 그룹 탐지 (detect-only)."""
    settings = _settings_with_db(ctx, db)
    out = pipe.cmd_similar_detect(
        settings,
        root,
        max_distance=max_distance,
        limit=limit,
        force_resign=force_resign,
    )
    click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    if not out.get("ok"):
        raise click.ClickException(str(out.get("error") or "similar-detect failed"))


@main.command("similar-list")
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.pass_context
def similar_list_cmd(ctx: click.Context, db: Path | None) -> None:
    """similar 그룹 목록 (decision + 유도 upload_policy 표시만)."""
    settings = _settings_with_db(ctx, db)
    rows = pipe.cmd_similar_list(settings)
    click.echo(json.dumps(rows, ensure_ascii=False, indent=2))


@main.command("similar-decide")
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--group-id", type=int, required=True)
@click.option(
    "--decision",
    type=click.Choice(
        ["same_content", "different_content", "partial", "deferred"]
    ),
    required=True,
)
@click.option(
    "--representative-photo-id",
    type=int,
    default=None,
    help="same_content 시 대표 photo_id (선택)",
)
@click.pass_context
def similar_decide_cmd(
    ctx: click.Context,
    db: Path | None,
    group_id: int,
    decision: str,
    representative_photo_id: int | None,
) -> None:
    """content 관계 decision만 저장 - upload/삭제/자동병합 없음."""
    settings = _settings_with_db(ctx, db)
    out = pipe.cmd_similar_decide(
        settings,
        group_id=group_id,
        decision=decision,
        representative_photo_id=representative_photo_id,
    )
    click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    if not out.get("ok"):
        raise click.ClickException(str(out.get("error") or "similar-decide failed"))


# [변경사유]: Phase 4.1 — 로컬 웹 리뷰 UI (썸네일 + decision, upload 미적용)
@main.command("similar-review")
@click.option("--db", type=click.Path(path_type=Path), default=None)
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8765, show_default=True)
@click.option("--no-browser", is_flag=True, help="브라우저 자동 실행 안 함")
@click.pass_context
def similar_review_cmd(
    ctx: click.Context,
    db: Path | None,
    root: Path | None,
    host: str,
    port: int,
    no_browser: bool,
) -> None:
    """썸네일 리뷰 UI - content decision만 저장 (upload/삭제/자동병합 없음)."""
    settings = _settings_with_db(ctx, db)
    target = root or settings.export_root
    if target is None:
        raise click.UsageError("--root 또는 KAKAO_EXPORT_ROOT 필요")
    click.echo(
        "Similar review UI (decision only). "
        f"Open http://{host}:{port}/  - Ctrl+C to stop."
    )
    pipe.cmd_similar_review(
        settings,
        Path(target),
        host=host,
        port=port,
        open_browser=not no_browser,
    )


if __name__ == "__main__":
    main()

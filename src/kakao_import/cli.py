# [변경사유]: Phase1 CLI — init/scan/parse/match/hash/report/run
"""Click CLI."""

from __future__ import annotations

import json
from pathlib import Path

import click

from kakao_import import __version__
from kakao_import import pipeline as pipe
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


@main.command("report")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--json", "as_json", is_flag=True, help="JSON 출력")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.pass_context
def report_cmd(
    ctx: click.Context, root: Path | None, as_json: bool, output: Path | None
) -> None:
    """매칭 리포트."""
    report = pipe.build_report(ctx.obj["settings"], _root_opt(ctx, root) if root or ctx.obj["settings"].export_root else None)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"OK report → {output.name}")
    elif as_json:
        click.echo(text)
    else:
        click.echo(f"rooms={report['rooms']} messages={report['messages']} photo_msgs={report['photo_messages']}")
        click.echo(f"photos={report['photos']} confidence={report['confidence']} exact={report['exact_groups']}")
        eg = report.get("esencia_golden") or {}
        click.echo(f"esencia_golden passed={eg.get('passed')} reasons={eg.get('reasons')}")
        click.echo(f"review_pending={len(report.get('review_required') or [])}")


@main.command("run")
@click.option("--root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def run_cmd(ctx: click.Context, root: Path | None, as_json: bool) -> None:
    """Phase 1 전체: init(필요시)→scan→parse→match→hash→report."""
    out = pipe.cmd_run(ctx.obj["settings"], _root_opt(ctx, root))
    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        click.echo(f"OK run scan={out['scan']} parse={out['parse']}")
        click.echo(f"OK run match={out['match']} hash={out['hash']}")
        eg = (out.get("report") or {}).get("esencia_golden") or {}
        click.echo(f"OK esencia_golden passed={eg.get('passed')}")


@main.command("status")
@click.pass_context
def status_cmd(ctx: click.Context) -> None:
    """DB 건수."""
    for k, v in status_counts(ctx.obj["settings"].db_path).items():
        click.echo(f"{k}: {v}")


if __name__ == "__main__":
    main()

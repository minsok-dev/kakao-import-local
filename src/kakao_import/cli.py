# [변경사유]: Phase1 CLI — init-db / scan / match / status
"""Click CLI entry."""

from __future__ import annotations

from pathlib import Path

import click

from kakao_import import __version__
from kakao_import.config import load_settings
from kakao_import.db import init_schema, status_counts
from kakao_import.logging_util import get_logger
from kakao_import.match import match_sha_only
from kakao_import.scan import scan_export_root


@click.group()
@click.version_option(__version__, prog_name="kakao-import")
@click.pass_context
def main(ctx: click.Context) -> None:
    """카카오톡 로컬 수집·매칭 (Phase 0~1)."""
    settings = load_settings()
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings
    get_logger("kakao_import", settings.log_level)


@main.command("init-db")
@click.pass_context
def init_db_cmd(ctx: click.Context) -> None:
    """SQLite 스키마 적용."""
    settings = ctx.obj["settings"]
    init_schema(settings.db_path)
    click.echo(f"OK init-db → {settings.db_path}")


@main.command("scan")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="내보내기 루트 (미지정 시 KAKAO_EXPORT_ROOT)",
)
@click.pass_context
def scan_cmd(ctx: click.Context, root: Path | None) -> None:
    """export 루트 스캔 → room/media 인덱스."""
    settings = ctx.obj["settings"]
    target = root or settings.export_root
    if target is None:
        raise click.UsageError("--root 또는 .env KAKAO_EXPORT_ROOT 필요")
    summary = scan_export_root(settings.db_path, Path(target))
    click.echo(f"OK scan → {summary}")


@main.command("match")
@click.option("--sha-only/--no-sha-only", default=True, help="SHA exact만 (기본)")
@click.pass_context
def match_cmd(ctx: click.Context, sha_only: bool) -> None:
    """로컬 SHA exact 매칭."""
    settings = ctx.obj["settings"]
    if not sha_only:
        raise click.UsageError("Phase1은 --sha-only 만 지원 (similar는 Phase4)")
    summary = match_sha_only(settings.db_path)
    click.echo(f"OK match → {summary}")


@main.command("status")
@click.pass_context
def status_cmd(ctx: click.Context) -> None:
    """DB 건수 요약."""
    settings = ctx.obj["settings"]
    counts = status_counts(settings.db_path)
    for k, v in counts.items():
        click.echo(f"{k}: {v}")


if __name__ == "__main__":
    main()

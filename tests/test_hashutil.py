# [변경사유]: Phase0 — 해시 단위 테스트
from pathlib import Path

from kakao_import.hashutil import sha256_file


def test_sha256_file(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello-kakao")
    assert sha256_file(p) == sha256_file(p)
    assert len(sha256_file(p)) == 64

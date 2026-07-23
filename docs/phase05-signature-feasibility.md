# Phase 0.5 — Python ↔ danceinfo_image_signature 호환성

<!-- [변경사유]: 2026-07-24 스모크 결과 기록 — Phase 1은 SHA만 진행 -->

## 목적

로컬에서 similar를 쓸 수 있는지 **짧게** 확인. 유사 기능은 구현하지 않음.

## 조사 결과 (2026-07-24)

| 항목 | 값 |
|------|-----|
| 패키지 경로 | `backend/packages/danceinfo_image_signature` |
| import (sys.path) | ✅ 모듈 import 가능 (`PACKAGE_VERSION` / `ALGO_VERSION` / `HASH_BITS` 노출) |
| `canonicalize_and_sign` 실행 | ❌ `ModuleNotFoundError: No module named 'PIL'` |
| 막힌 이유 | kakao-import-local venv에 **Pillow 미설치**. 패키지 자체는 Python binding |
| CLI/subprocess | 별도 CLI 엔트리 없이 라이브러리 API 중심 |
| 공용 벡터 | 패키지 내 `tests/golden_vectors.json` 존재 — cross-check 가능 |

## 선택 (Phase 4 권장)

**1. Python binding 직접 사용**

```text
pip install -e ../backend/packages/danceinfo_image_signature
# (의존성: Pillow 등 requirements에 포함)
```

대안: 연동 전까지만 로컬 SHA, signature는 서버 계산 (옵션 3) — Phase 1~3에 영향 없음.

## Phase 1 영향

없음. Phase 1은 **SHA-256 exact**만 사용하며 본 조사로 구현을 멈추지 않음.

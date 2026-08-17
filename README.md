# kakao-import-local

카카오톡 **로컬 수집·매칭** 도구입니다.  
서버 Import API·ingest·OCR 파이프라인은 **Phase 3** 이후이며, 이 저장소는 **원본을 삭제하지 않는** collector 전용입니다.

## 정책 요약

| 구분 | 로컬 (이 프로젝트) | 서버 |
|------|-------------------|------|
| 입력 | `input/raw/chats/` + `input/raw/photos/` (공용 이미지) | — |
| 매칭 | `KakaoTalk_` **파일명 시각** ↔ 메시지 시각 | 최종 exact / similar |
| Exact | SHA-256 | 서버 전 범위 검사 (Phase 3) |
| 텍스트 | balanced merge (Phase 2) | OCR + 카카오 메시지 → GPT |
| 원본 | **삭제·수정 금지** | 자산 overwrite 금지 |

## Phase (v1.3)

| Phase | 우선 | 내용 | 상태 |
|-------|-----:|------|------|
| **0** | — | 계약·golden·개인정보 | ✅ |
| **0.5** | 낮음 | signature 조사만 | Phase 4 직전 |
| **1** | — | parser + **시각 matcher** + 이력 + SHA + 리포트 | ✅ (ESENCIA golden) |
| **1.5** | 보류 | watcher | **3.5·E2E 후** |
| **2** | — | 텍스트 merge | ✅ |
| **3** | — | Import·exact·OCR/GPT | ✅ 기능 (운영 이슈→3.5) |
| **3.5** | **지금** | 운영 안정화 (caption·gate·file_missing·UTF-8) | **최우선** — [docs/phase3-ops-stabilization.md](./docs/phase3-ops-stabilization.md) |
| **4** | 이후 | similar 그룹 UI | **3.5 전 착수 금지** |
| **5** | 마지막 | 제한 자동 승인 | 이후 |

Phase 1 완료 = ESENCIA golden(`…005030533` / `…005034512` ↔ `오전 12:50` 사진 2줄) 자동 통과 포함.

상세: [docs/development-plan.md](docs/development-plan.md) **v1.3** · Phase3: [docs/phase3-import.md](docs/phase3-import.md) · 안정화: [docs/phase3-ops-stabilization.md](docs/phase3-ops-stabilization.md)  
PC 카톡에서 파일을 꺼내는 도구(별도, 하이브리드 UIA+좌표 3곳): [docs/kakao-pc-collect-plan.md](docs/kakao-pc-collect-plan.md)

## 요구 사항

- Python **3.11+** (이 PC는 3.13 권장)
- Windows PowerShell 기준

## 설치

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
copy .env.example .env
# KAKAO_EXPORT_ROOT=./input/raw  (chats/ + photos/)
```

## CLI (Phase 1+2)

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
.\.venv\Scripts\Activate.ps1
# KAKAO_EXPORT_ROOT=./input/raw  (chats/ + photos/)

kakao-import init --reset
kakao-import run          # scan → parse → match → hash → merge → report
kakao-import merge --mode balanced
kakao-import report --json
# 단계별: scan | parse | match | hash | merge | status
```

Phase 1: ESENCIA golden 자동 통과.  
Phase 2: exact SHA 텍스트 `collapse` / `merged` / `review` (`MERGE_MODE`, 기본 balanced).  
수동: `merge-decide` · 되돌리기: `merge-undo`.

```powershell
# Phase3 — 매니페스트만 (기본). 서버 전송은 인접 메시지만 포함
kakao-import export-payload
kakao-import upload --dry-run
```

## 디렉터리

```
kakao-import-local/
  docs/                 # Phase 0 계약·스키마 설명
  sql/                  # SQLite DDL
  src/kakao_import/     # 패키지
  tests/
  data/                 # gitignore — 로컬 DB
  input/                # gitignore — 작업용 복사본(선택)
```

## 주의

- 상용/스테이징 DB·API 키를 `.env`에 넣지 마세요.
- Phase 4 이전에는 **SHA-256 exact만**. 유사 매칭은 backend `danceinfo_image_signature` 연동 후.

# Phase 3 — 카카오 Import → OCR 직행 (exact hold)

<!-- [변경사유]: 단계1 — Import 승인 게이트 축소, OCR 목록 exact 확인과 통일 -->

| 항목 | 내용 |
|------|------|
| 상태 | **접수 → OCR 직행 / exact_hold** |
| 기준일 | 2026-07-24 |
| 상세(프론트) | `frontend/docs/image-dup-review-phase1.md` |

## 상태 흐름 (현행)

```text
upload POST /api/admin/ingest/import/kakao
  ├─ 신규 → tbl_ocrcontent 생성 + OCR/GPT 자동
  │         Import row status=accepted, next=ocr_queued
  └─ exact → tbl_ocrcontent exact_hold (OCR/GPT 금지)
            Import row status=duplicate_exact, next=dup_review
            → 관리자 OCR 목록에서 등록 안 함 | 기존으로 OCR
```

Import **승인 API/UI는 레거시**로 남길 수 있으나, 정상 경로에서는 사용하지 않는다.  
중복·등록 여부는 **OCR 목록**에서 처리한다.

### auto_migrate

카카오 OCR 생성 시 `auto_migrate=0` (검수 후 이관). 포스터 업로드(`auto_migrate=1`)와 다를 수 있음.

### contentIdx SSOT

| 구분 | 위치 |
|------|------|
| SSOT | `tbl_ocrcontent.migrated_content_idx` |
| 캐시 | Import `response_json` (상세 GET lazy sync) |

## API

| Method | Path | 역할 |
|--------|------|------|
| POST | `/api/admin/ingest/import/kakao` | 접수 + OCR 생성(또는 exact_hold) |
| GET | `/api/admin/ingest/import` | 목록(추적·멱등) |
| GET | `/api/admin/ingest/import/[id]` | 상세 |
| POST | `/api/admin/ocr_data/[id]/resolve_duplicate` | exact_hold 결정 |

레거시: `.../import/[id]/approve|reject` — 단계1 정상 경로 비권장.

## 로컬 업로드

```text
kakao-import upload --no-dry-run --limit N --endpoint $KAKAO_IMPORT_ENDPOINT
```

- `KAKAO_IMPORT_SESSION_COOKIE` (파일 저장 금지)
- payload part에 `Content-Type: application/json` **넣지 않음** (formidable maxFiles 오인 방지)

## 스테이징

프론트 `docs/image-dup-review-phase1.md` §스테이징 + DDL `015` 적용 후:

1. 신규 업로드 → OCR 목록에 일반 행 + OCR 진행
2. exact → `exact 확인` 배지, OCR 미진행
3. 등록 안 함 / 기존으로 OCR

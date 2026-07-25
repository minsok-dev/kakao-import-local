# Phase 3 — 카카오 Import → 공통 exact / OCR 입구

<!-- [변경사유]: 2026-07-25 — exact 전건 hold 폐기. SNS 병합·auto_migrate·F hold. 프론트 정책 문서 기준 -->

| 항목 | 내용 |
|------|------|
| 상태 | **정책 확정 · 프론트 코드는 교체 예정** |
| 기준일 | 2026-07-25 |
| 확정 정책 | `frontend/docs/image-exact-sns-merge-policy.md` |
| 개발 계획 | `frontend/docs/image-exact-sns-merge-dev-plan.md` |
| 이전(폐기) | exact → 전부 OCR `exact_hold` (`image-dup-review-phase1.md`) |

## 목표 상태 흐름

```text
upload POST /api/admin/ingest/import/kakao
  ├─ 신규 → OCR 생성 + OCR/GPT → auto_migrate로 콘텐츠 자동 이관
  ├─ exact + 콘텐츠/OCR → SNS 수집 본문만 추가 (OCR 안 만듦 / hold 아님)
  ├─ exact + reusable asset만 → OCR 행만 (GPT 자동 없음)
  └─ exact 예외(F) → OCR + exact_hold (GPT 금지) → OCR 목록에서 처리
```

크롤과 **exact 이후는 동일 공통 서비스**. Import 테이블은 당분간 입구·멱등·추적용.  
(카카오→크롤 raw 통합은 후속. 이번 범위 외.)

Import **승인 API/UI는 레거시**. happy path에서 사용하지 않는다.

### auto_migrate

- **확정**: 카카오 **신규**도 크롤·포스터 업로드와 같이 자동 이관 가능하게 맞춤 (`auto_migrate=1` 또는 소스 플래그).
- **현재 코드**: `createOcrFromKakaoImport`가 `auto_migrate=0`일 수 있음 → 개발 계획 P4에서 정합.

### contentIdx SSOT

| 구분 | 위치 |
|------|------|
| SSOT | `tbl_ocrcontent.migrated_content_idx` |
| 캐시 | Import `response_json` (상세 GET lazy sync) |

exact로 콘텐츠에만 SNS를 붙인 경우 OCR이 없으므로 `contentIdx`는 Import 추적 JSON 등에 필요 시 기록 (구현 시 계획 문서).

## API

| Method | Path | 역할 |
|--------|------|------|
| POST | `/api/admin/ingest/import/kakao` | 접수 + 공통 exact/신규 처리 |
| GET | `/api/admin/ingest/import` | 목록(추적·멱등) |
| GET | `/api/admin/ingest/import/[id]` | 상세 |
| POST | `/api/admin/ocr_data/[id]/resolve_duplicate` | **F** exact_hold 결정 |

레거시: `.../import/[id]/approve|reject` — 정상 경로 비권장.

## 로컬 업로드

```text
kakao-import upload --no-dry-run --limit N --endpoint $KAKAO_IMPORT_ENDPOINT
```

- `KAKAO_IMPORT_SESSION_COOKIE` (파일 저장 금지)
- payload part에 `Content-Type: application/json` **넣지 않음** (formidable maxFiles 오인 방지)

## 스테이징 (정책 반영 후)

프론트 정책·개발 계획 체크리스트 기준:

1. 신규 → OCR/GPT → 콘텐츠 자동 이관
2. exact+콘텐츠 → SNS만 추가, OCR 목록에 hold 안 쌓임
3. F만 `exact 확인` 필터에 표시

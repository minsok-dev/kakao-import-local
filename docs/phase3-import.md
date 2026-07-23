# Phase 3 — Import API · 서버 exact · OCR/GPT 승인

<!-- [변경사유]: Phase3a 접수·안전 / Phase3b 승인·OCR 최소 구현 -->

| 항목 | 내용 |
|------|------|
| 상태 | **3a 접수·안전** / **3b 승인→OCR 최소 구현** |
| 기준일 | 2026-07-24 |
| Phase 3a | `automated tests passed / staging manual verification pending` |

## 상태 흐름 (3b)

```text
received
  ├─ reject → failed (admin_action=reject, OCR/GPT 없음)
  └─ approve → processing → accepted (ocr_idx, next=ocr_queued)
       └─ OCR 검수(auto_migrate=0) → 이관 시 content_idx lazy sync

duplicate_exact
  ├─ skip → duplicate_exact (admin_action=skip, OCR 없음)
  ├─ reuse_asset → accepted (기존 asset 링크 + OCR)
  └─ reject → failed
```

`auto_register=false` 유지. **승인 전**에는 OCR/GPT/job/content 생성 없음.

## API

| Method | Path | 역할 |
|--------|------|------|
| POST | `/api/admin/ingest/import/kakao` | 3a 접수 |
| GET | `/api/admin/ingest/import` | 목록 |
| GET | `/api/admin/ingest/import/[id]` | 상세 (+ content_idx sync) |
| POST | `/api/admin/ingest/import/[id]/approve` | `{ action, note? }` |
| POST | `/api/admin/ingest/import/[id]/reject` | `{ reason }` |

approve `action`:
- `received` → `approve`
- `duplicate_exact` → `skip` \| `reuse_asset`

권한: `super_admin` + same-origin.

## GPT 참고

- `sns_caption_text`에 `<<<KAKAO_ADJACENT_REF>>>` + 인접/merged만
- 길이·건수 상한 (전체 TXT 금지)
- backend `openai_helper`: 카카오 참고는 명령 금지·충돌 시 필드 비움
- OCR `auto_migrate=0` → 기존 OCR 검수 후 이관

## DDL

신규 DDL 없음. 계보·멱등은 `response_json`(ocr_idx/content_idx/admin_action) + UNIQUE idempotency.

## 관리자 UI

Import 목록/상세 **화면 없음** → 이번 범위는 API·서비스만.  
필요 UI: 목록(status 필터) · 상세(메시지·exact) · 승인/스킵/재사용/거절 버튼.

## 스테이징 검증 (운영자)

### 3a (아직 pending)
1. 정상 접수 / 멱등 / SHA·비이미지 거부 / exact=`duplicate_exact` / OCR 미생성

### 3b
1. `received` 승인 → OCR 1건 · `sns_caption_text`에 카카오 마커
2. 동일 승인 재요청 → 같은 `ocr_idx`
3. 거절 → OCR 없음
4. `duplicate_exact` skip / reuse_asset
5. 비관리자·Origin 거부
6. 승인 전 건은 OCR/GPT 미실행

## 코드

- frontend: `lib/ingest/import/approveKakaoImport.ts`, `rejectKakaoImport.ts`, `createOcrFromKakaoImport.ts`, `formatKakaoGptRef.ts`, `pages/api/.../import/[id]/*`
- backend: `app/openai_helper.py` (카카오 참고 프롬프트)

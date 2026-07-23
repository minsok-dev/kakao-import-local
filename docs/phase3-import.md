# Phase 3 — Import API · 서버 exact · OCR/GPT 승인

<!-- [변경사유]: Phase3a 접수·안전 / Phase3b 승인·OCR·최소 UI / contentIdx SSOT -->

| 항목 | 내용 |
|------|------|
| 상태 | **3a 접수·안전** / **3b 승인→OCR + 최소 관리자 UI** |
| 기준일 | 2026-07-24 |
| Phase 3a | `automated tests passed / staging manual verification pending` |
| Phase 3b | `automated tests passed / staging manual verification pending` (3a와 함께 검증) |

## 상태 흐름 (3b)

```text
received
  ├─ reject → failed (admin_action=reject, OCR/GPT 없음)
  └─ approve → processing(조건부 claim) → accepted (ocr_idx, next=ocr_queued)
       └─ OCR/GPT (auto_migrate=0) → 관리자 OCR 검수·이관
            └─ 상세 GET 시 content_idx 캐시 sync (next=done)

duplicate_exact
  ├─ skip → duplicate_exact (admin_action=skip, OCR 없음)
  ├─ reuse_asset → accepted (기존 asset 링크 + OCR, auto_migrate=0)
  └─ reject → failed
```

`auto_register=false` 유지. **승인 전**에는 OCR/GPT/job/content 생성 없음.

### auto_migrate=0

- 승인 후 **OCR 생성 + GPT 구조화까지**만 진행한다.
- **콘텐츠 이관은 하지 않는다.** 관리자가 OCR 검수 화면에서 확인한 뒤 이관한다.

### contentIdx SSOT / 캐시

| 구분 | 위치 | 역할 |
|------|------|------|
| **SSOT** | `tbl_ocrcontent.migrated_content_idx` | 이관된 content의 진실 원천 |
| **캐시** | Import `response_json.content_idx` | 상세 조회(`GET .../import/[id]`) 시 SSOT에서 lazy 동기화 |

승인 직후 `content_idx`는 항상 `null`이다.

### 동시 승인 · processing 고착

`UPDATE ... SET status='processing' WHERE request_idx=? AND status IN (...)`  
조건부 claim으로 **OCR은 1건만** 생성. 패자는 `IN_PROGRESS` 또는 기존 `ocr_idx` 멱등 재생.

| 상황 | 동작 |
|------|------|
| 정상 오류 (OCR 전) | `failed` 로 복구 → 관리자 재시도 |
| OCR 생성 후 후속 실패 | `accepted` + `ocr_idx` 보존 (재생성 없음) |
| `processing` + `ocr_idx` | `accepted` 치유 + 기존 OCR 반환 |
| `processing` + ocr 없음 + **5분 미만** | `IN_PROGRESS` |
| `processing` + ocr 없음 + **5분 이상** | stale 재claim (`mod_date` CAS) → 재시도 (UI: 고착 재시도) |

별도 worker/상태머신 없음.

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
- 길이·건수 상한 (전체 TXT 금지; backend도 동일 상한 truncate)
- backend `openai_helper`: 카카오 참고는 명령 금지·충돌 시 필드 비움·원문 INFO 로그 미노출
- OCR `auto_migrate=0` → 기존 OCR 검수 후 이관

## DDL

신규 DDL 없음. 계보·멱등은 `response_json`(ocr_idx/content_idx/admin_action) + UNIQUE idempotency.

## 관리자 UI

| 경로 | 역할 |
|------|------|
| `/admin_w/ingest/import` | 목록 (status 필터) |
| `/admin_w/ingest/import/[id]` | 상세: 이미지·인접 메시지·exact·승인/skip/reuse/거절 · OCR/content 링크 |

메뉴: 수집 관리 → 카카오 Import. 권한 `super_admin`.

## 스테이징 검증 (운영자) — 3a + 3b 함께

> 상태: **pending** (자동 테스트만 통과). 아래를 한 번에 수행한다.

### A. Phase 3a 접수
1. 정상 접수 → `received`, staging raw 존재, **OCR/GPT 미생성**
2. 동일 idempotency 재전송 → 멱등 재생
3. SHA 불일치·비이미지 → 거부
4. exact 매칭 → `duplicate_exact` (자동 skip/OCR 없음)

### B. Phase 3b 승인·UI
1. 관리자 UI `/admin_w/ingest/import` 목록·상세 확인
2. `received` **신규 승인** → OCR 1건 · `sns_caption_text`에 카카오 마커 · DB `auto_migrate=0`
3. 동일 건 재승인 / 더블클릭 → 같은 `ocr_idx` (멱등·중복 클릭 방지)
4. 거절 → OCR 없음, 사유 저장
5. `duplicate_exact` → UI에서 skip / reuse_asset
6. OCR 검수(`/admin_w/ocr_data/{ocr_idx}`)에서 이관 후 Import 상세 재조회 → `content_idx` 캐시 반영
7. 비관리자·다른 Origin → 거부
8. **고착**: 승인 중 프로세스 중단 후 `processing`+ocr없음 → 5분 후 UI「고착 재시도」→ OCR 1건
9. **고착**: `processing`+`ocr_idx` 남은 경우 재승인 → accepted 치유·동일 OCR
10. 의도적 오류(raw 삭제 등) → `failed` 후 「실패 건 재시도」가능

## 코드

- frontend: `lib/ingest/import/approveKakaoImport.ts` (claim), `rejectKakaoImport.ts`, `createOcrFromKakaoImport.ts`, `formatKakaoGptRef.ts`, `pages/api/.../import/**`, `pages/admin_w/ingest/import/**`
- backend: `app/openai_helper.py`, `tests/test_openai_helper_kakao_ref.py`

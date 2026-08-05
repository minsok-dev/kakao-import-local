# Phase 3 — 카카오 Import → 공통 exact / OCR 입구

<!-- [변경사유]: 2026-08-03 — Phase 3.5 운영 안정화 링크. caption replay는 3.5에서 처리 -->

| 항목 | 내용 |
|------|------|
| 상태 | **기능 구현 ✅ · 운영 안정화는 Phase 3.5** |
| 기준일 | 2026-08-03 |
| 확정 정책 | `frontend/docs/image-exact-sns-merge-policy.md` |
| 개발 계획 | `frontend/docs/image-exact-sns-merge-dev-plan.md` |
| **운영 안정화** | [phase3-ops-stabilization.md](./phase3-ops-stabilization.md) (**지금 최우선**) |
| 이전(폐기) | exact → 전부 OCR `exact_hold` (`image-dup-review-phase1.md`) |

> **다음 작업은 Phase 3 기능 추가가 아니라 Phase 3.5(P1~P4)다.**  
> 멱등 replay caption 고착 · empty adjacent gate · file_missing · UTF-8.  
> Phase 4 로컬 similar / watcher / 자동 승인은 3.5·E2E 이후.

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
- **파일 상한**: 수신(ingress) **50MiB/파일**. 초과 시 로컬 `FILE_EXCEEDS_INGRESS_LIMIT` / 서버 413.
- <!-- [변경사유]: Phase 4.2+ 묶음 main+sub -->
  **묶음(4.2+)**: `item.sub_images` + multipart `file`(main) + `sub_0`… (최대 5장). Nginx `client_max_body_size`는 다장 total에 맞게 **≥60m** 유지.
- **선최적화(Pillow) 없음** — 원본 전송 후 서버가 크롤과 동일 `optimizeIngestImageBuffer` 적용.
- SHA: 로컬·payload = **원본** SHA(무결성). exact/OCR = 서버 **최적화 후** SHA. main exact(SNS/hold) 시 sub는 미첨부.

### 운영(스테이징/상용) — Nginx 등 앞단

앱 파일 제한만 올려도 프록시가 막으면 API에 도달하지 않습니다. **코드가 아니라 인프라 적용**.

| 항목 | 권장 값 |
|------|---------|
| 앱 `KAKAO_IMPORT_MAX_INGRESS_BYTES` | **50MiB** |
| Nginx `client_max_body_size` | **55~60m** (multipart 오버헤드) |
| proxy/FastCGI `*_read_timeout` / `send_timeout` | 대용량 업로드에 맞게 (예: 120s+) |
| Cloudflare | Business 미만 본문 한도 확인 (필요 시 우회/직접 origin) |

스테이징 적용 예:

```nginx
# /etc/nginx/sites-available/danceinfo-staging (발췌)
client_max_body_size 60m;
proxy_read_timeout 120s;
proxy_send_timeout 120s;
```

적용 후: `sudo nginx -t && sudo systemctl reload nginx`

### 스테이징 E2E (대용량·최적화)

1. 15~40MiB 카카오 원본 JPEG 1장 준비 (로컬 photos/)
2. `kakao-import upload --dry-run` — 해당 파일이 스킵되지 않는지
3. `--no-dry-run` 전송 → 200, 응답 `source_sha256`·`final_sha256_prefix`(12자)·`optimized` (전체 final SHA는 API 미노출)
4. DB/로그: `ingressBytes` / `optimizedBytes` / SHA prefix만 (전체 SHA·경로 없음)
5. 동일 원본을 크롤 경로로도 넣으면 **final SHA 일치** (서버 동일 optimizer)
6. 50MiB+ → 로컬·서버 모두 거부
7. payload SHA 조작 → 400 `SHA_MISMATCH`

## 스테이징 (정책 반영 후)

프론트 정책·개발 계획 체크리스트 기준:

1. 신규 → OCR/GPT → 콘텐츠 자동 이관
2. exact+콘텐츠 → SNS만 추가, OCR 목록에 hold 안 쌓임
3. F만 `exact 확인` 필터에 표시

---

## 로컬 삭제 후 동일 이미지 재등장 (캡션 추가)

<!-- [변경사유]: 2026-08-05 — photo_file 유지/caption-only 오해 정리. 서버 exact가 SSOT -->

**지원 시나리오 (A):** 예전에 올린 파일이 로컬에서 지워졌다가 `photos/`에 **다시 생김** → `run` → `upload`  
→ 서버가 **최적화 후 SHA**로 exact 판정 → 매칭 텍스트는 기존 SNS 캡션 append (`next=sns_appended` 등).

| 구분 | 담당 | 비고 |
|------|------|------|
| 같은 배치 안 바이트 중복 | 로컬 `exact_sha_*` / `excluded_from_upload` | 대표 1장만 큐 |
| “예전에 서버에 올렸음” | **서버** exact (`receiveKakaoImport` → `resolveExact`) | 로컬 DB 기억 불필요 |
| 디스크에 없는 `photo_file` | `prune_missing_photo_files` | 행 유지 금지 (hash/upload 깨짐) |

**비범위 (B):** 로컬 파일 없이 채팅 텍스트만으로 과거 SHA에 캡션 부착 — 어느 SHA인지 자동 구분 불가.  
**후속(선택):** 대역폭 절감용 `uploaded_sha_ledger` + caption-only(multipart 생략). 캡션 추가 자체와는 별개이며 **미구현**.

운영 확인: 재전송 후 응답/로그에 exact SNS append · 서버에 해당 final SHA asset/포스터가 **남아 있어야** 함.

### multi_room 같은 분

같은 분에 여러 방 「사진」이 있어도 **매칭 포기하지 않음**. 로컬 파일 1장에 그 분 **모든 방** 캡션을 union (message_id 중복 제거).  
계약: [phase0-contracts.md](./phase0-contracts.md) §9.
# Phase 3 — Import API · 서버 exact · (OCR/GPT 승인 후속)

<!-- [변경사유]: Phase3 얇은 슬라이스 — 접수·목록·dry-run upload; auto_register=off -->
<!-- [변경사유]: Phase3a 안전 보완 — SHA/magic/멱등/로그·보존·인증 문서 -->

| 항목 | 내용 |
|------|------|
| 상태 | **3a 접수·안전 보완** / 3b 승인→OCR/GPT **미착수** |
| 기준일 | 2026-07-24 |

## 운영: 대화 export 크기

PC 내보내기는 **방 전체 이력**이 올 수 있다.  
로컬 파서는 전체를 읽어 매칭하지만, **서버에는 인접/merged만** 보낸다.

실서비스 권장 운영:

1. 이전 대화를 정리(삭제)한 뒤 **재다운로드·재내보내기** (운영자가 고려 중)
2. (후속 옵션) 로컬 날짜 윈도우 필터 — **지금은 구현하지 않음**

코드로 원본 txt를 잘라 저장하지 않는다 (원본 보존 정책).  
TXT 삭제 후에도 SQLite에 원문/`before_json`이 남을 수 있음 → [privacy-retention.md](./privacy-retention.md) §6.2 (자동 purge 없음).

## 서버 (frontend)

| API | 역할 |
|-----|------|
| `POST /api/admin/ingest/import/kakao` | multipart `payload`+`file` 접수 (파일 1개) |
| `GET /api/admin/ingest/import` | 목록 (`status`, `batch_id`) |
| `GET /api/admin/ingest/import/[id]` | 상세 |

정책:

- `auto_register=off` 강제 (true 요청 거부)
- 서버에서 **SHA-256 재계산** ↔ manifest 비교
- magic(JPEG/PNG/GIF/WebP) + sharp 메타(손상) + 크기 상한
- exact 발견 → `duplicate_exact` **검수 대기** (자동 삭제·crawl `skipped_dup` 아님)
- raw staging만 보관, **media job / OCR / GPT / content enqueue 없음**
- 멱등: `(client_instance_id, idempotency_key)` UNIQUE + race 시 재조회
- 인증: `super_admin` 세션 + same-origin
- Cookie를 로컬 도구에 **파일로 저장 금지**
- 로그: SHA prefix·건수·코드만 (본문·전체 SHA·Cookie·절대경로 금지)

exact 범위: media asset · OCR/콘텐츠 포스터 SHA · 과거 `kakao_pc_export` 요청.

## 인증 (3a vs 후속)

| | 방식 |
|--|------|
| **3a 1차 수동 검증** | 브라우저 관리자 세션 Cookie (`KAKAO_IMPORT_SESSION_COOKIE` env만) |
| **백로그** | Import 전용 API 토큰 (`api_key`) — Cookie 대체 |

## 로컬 CLI

```powershell
kakao-import export-payload -o data/last_upload_manifest.json
kakao-import upload --dry-run
# 실전송 (쿠키는 env만, 디스크 저장 금지)
$env:KAKAO_IMPORT_SESSION_COOKIE="..."
kakao-import upload --no-dry-run --endpoint https://host/api/admin/ingest/import/kakao --limit 1
```

## GPT 계약 (3b 후속 — 예고, 미착수)

```text
ImportItem (request)
→ matched/merged message 저장 (response_json.item)
→ 관리자 승인
→ OCR + import request_idx 연결
→ OCR text + Kakao 메시지 → GPT
→ contentIdx → ImportItem
```

## 스테이징 직접 확인 절차 (운영자)

1. `super_admin`으로 스테이징 로그인 → 브라우저에서 Cookie 복사 (파일 저장 금지)
2. 정상 JPEG 1건: `upload --no-dry-run --limit 1` → `201` · `status=received` · `auto_register=false`
3. 동일 payload 재전송 → `200` · `idempotent_replay=true` · DB `request_idx` 증가 없음
4. 이미 서버에 있는 SHA(또는 방금 접수한 SHA로 다른 idempotency) → `duplicate_exact` 유지(삭제되지 않음)
5. bytes 조작해 SHA 불일치 → `SHA_MISMATCH` 거부
6. `.txt`/깨진 파일 → `NOT_IMAGE` / `CORRUPT_IMAGE`
7. Origin 없는 curl / 비관리자 세션 → `403` / 권한 거부
8. DB 확인: 해당 접수 후 `tbl_ocrcontent`·`tbl_media_processing_job`·`tbl_content` **신규 없음**
9. 서버 로그: Cookie·전체 SHA·메시지 본문 미출력 확인

## 코드

- frontend: `lib/ingest/import/*`, `pages/api/admin/ingest/import/*`, `types/content.ts`
- local: `payload.py`, `upload.py`, CLI `export-payload` / `upload`
- 단위 테스트: `frontend/tests/unit/ingest/kakaoImport*.test.ts`

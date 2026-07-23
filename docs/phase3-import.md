# Phase 3 — Import API · 서버 exact · (OCR/GPT 승인 후속)

<!-- [변경사유]: Phase3 얇은 슬라이스 — 접수·목록·dry-run upload; auto_register=off -->

| 항목 | 내용 |
|------|------|
| 상태 | **3a 접수 완료** / 3b 승인→OCR/GPT 후속 |
| 기준일 | 2026-07-24 |

## 운영: 대화 export 크기

PC 내보내기는 **방 전체 이력**이 올 수 있다.  
로컬 파서는 전체를 읽어 매칭하지만, **서버에는 인접/merged만** 보낸다.

실서비스 권장 운영:

1. 이전 대화를 정리(삭제)한 뒤 **재다운로드·재내보내기** (운영자가 고려 중)
2. (후속 옵션) 로컬 날짜 윈도우 필터 — **지금은 구현하지 않음**

코드로 원본 txt를 잘라 저장하지 않는다 (원본 보존 정책).

## 서버 (frontend)

| API | 역할 |
|-----|------|
| `POST /api/admin/ingest/import/kakao` | multipart `payload`+`file` 접수 |
| `GET /api/admin/ingest/import` | 목록 (`status`, `batch_id`) |
| `GET /api/admin/ingest/import/[id]` | 상세 |

정책:

- `auto_register=off` 강제 (true 요청 거부)
- exact 발견 → `duplicate_exact` **검수 대기** (crawl `skipped_dup` 아님)
- raw staging만 보관, **media job / OCR / GPT enqueue 없음**
- 인증: `super_admin` 세션 + same-origin
- Cookie를 로컬 도구에 **파일로 저장 금지**

exact 범위: media asset · OCR/콘텐츠 포스터 SHA · 과거 `kakao_pc_export` 요청.

## 로컬 CLI

```powershell
kakao-import export-payload -o data/last_upload_manifest.json
kakao-import upload --dry-run
# 실전송 (쿠키는 env만, 디스크 저장 금지)
$env:KAKAO_IMPORT_SESSION_COOKIE="..."
kakao-import upload --no-dry-run --endpoint https://host/api/admin/ingest/import/kakao --limit 1
```

## GPT 계약 (3b 후속 — 예고)

```text
ImportItem (request)
→ matched/merged message 저장 (response_json.item)
→ 관리자 승인
→ OCR + import request_idx 연결
→ OCR text + Kakao 메시지 → GPT
→ contentIdx → ImportItem
```

## 코드

- frontend: `lib/ingest/import/*`, `pages/api/admin/ingest/import/*`, `types/content.ts`
- local: `payload.py`, `upload.py`, CLI `export-payload` / `upload`

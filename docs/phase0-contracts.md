# Phase 0 — Contracts

<!-- [변경사유]: v1.2 — Phase 4.2 similar upload policy 반영 메모 추가 -->

## 1. 수집 경로

개인 카카오톡 대화·첨부를 자동 수집하는 **공식 범용 API가 없으므로**  
PC 다운로드 파일과 대화 내보내기를 사용한다.

원본 보존·마스킹·전송 범위: [privacy-retention.md](./privacy-retention.md)  
샘플·정답: [samples-golden-set.md](./samples-golden-set.md)

## 2. 핵심 파이프라인 계약

```text
방별 photos/ + chats/*.txt (`<room_id>/`). 구 공용 풀은 `_legacy`
→ 대화 TXT 파싱
→ KakaoTalk_ 파일명 시각 ↔ 메시지 절대시각 매칭
→ (SHA exact / 이력) → (텍스트 merge) → Import
```

폴더 스캔·SHA만으로 제품 완료로 보지 않는다.  
매칭 상세·golden: [development-plan.md](./development-plan.md) §2 · [golden-esencia-20260724-0050.md](./golden-esencia-20260724-0050.md)

### 입력 레이아웃 (확정)

```text
input/raw/
  <room_id>/
    chats/     # 해당 방 export txt
    photos/    # 해당 방 이미지
  chats/ + photos/   # 구 레이아웃 → room_id=_legacy
```

## 3. 엔티티 (SQLite) — Phase 0/1 목표

| 테이블/개념 | 역할 |
|-------------|------|
| `schema_meta` | DDL 버전 |
| `source_room` | 채팅방/소스 |
| `import_batch` | 배치 ID, export fingerprint, 처리 시각 |
| `local_message` | 메시지 + **message fingerprint** (멱등) |
| `local_media` | 상대경로 + **file fingerprint** + SHA-256 |
| `media_message_link` | 이미지↔메시지 후보/확정 매칭 |
| `match_result` | exact/similar/none |
| `decision` / `review_queue` | 사용자·자동 결정, 충돌 |
| (Phase3+) | `server_import_id`, `server_ocr_id`, `content_idx` |

DDL: [sql/001_init_schema.sql](../sql/001_init_schema.sql) (이력·fingerprint 컬럼은 Phase 1에서 `002_` 로 확장 예정)

### 멱등 보장

- 동일 export fingerprint / 동일 (room, rel_path) / 동일 message fingerprint → **upsert**, 중복 INSERT 금지  
- 과거 Import 이미지 SHA를 로컬에 축적해 재스캔 시 이력과 비교

## 4. Exact (로컬 Phase 1)

- SHA-256 (파일 바이트)  
- 서버 최종 범위는 Phase 3: OCR·콘텐츠 main/sub, media asset, 과거 Kakao Import (+ 누락 보완)

## 5. Import payload (Phase 3a — 전송 가능, auto_register=off)

상세: [phase3-import.md](./phase3-import.md)

```json
{
  "source": "kakao_local",
  "client_instance_id": "string",
  "idempotency_key": "string",
  "room_key": "string",
  "items": [
    {
      "local_item_id": "string",
      "decision": "same_content|different_content|partial|deferred|upload_one",
      "sha256": "hex",
      "rel_path": "string",
      "matched_messages": [
        {
          "sent_at": "ISO-8601",
          "text": "adjacent only",
          "message_fingerprint": "hex"
        }
      ],
      "match_hints": {
        "local_exact": true,
        "text_relation": "same|merged|conflict|unmatched"
      }
    }
  ]
}
```

- **절대경로 없음**  
- 메시지 = 인접/merged만  
- API는 단건 `item` + multipart `file` (배치는 `batch_id`)

## 6. 서버 후단 계약 (Phase 3 필수 — 여기서 예고)

```text
ImportItem
→ matched/merged message 저장
→ OCR 생성 시 importItemId 연결
→ OCR text + Kakao message → GPT
→ contentIdx → ImportItem
```

ingest에만 쌓이고 GPT에 안 쓰는 경로 금지.  
crawl `skipped_dup` 정책을 카카오 Import에 그대로 적용하지 않음.

## 7. Similar 그룹 (Phase 4 계약 / 4.2 반영)

<!-- [변경사유]: 업로드 제어·UI는 Phase 3.5·E2E 이후. 탐지-only는 병렬 가능. 상세: similar-group-decisions.md -->

[similar-group-decisions.md](./similar-group-decisions.md)  
decision: `same_content` / `different_content` / `partial` / `deferred` (upload policy는 문서에서 분리).

<!-- [변경사유]: Phase 4.2 — 로컬 manifest/upload 단계의 실제 계약 메모 -->
- `same_content`: representative 1건만 upload 후보
- `different_content`: 전원 후보 유지
- `partial`: subgroup 대표 + singleton만 후보 유지
- `deferred`: dry-run / upload 모두 기본 차단
<!-- [변경사유]: Phase 4.2+ — 채팅 매칭 묶음 → 서버 main+sub 1 OCR -->
- **묶음 사진(4.2+)**: similar 적용 **후** 채팅 `image_group` 안에서 **동일 `KakaoTalk_…_HHMMSSmmm` 스템 + `_01`/`_02`…** 인 장만 **1 request = main + sub_images**
  - 접미사 없는 단독 파일(다른 밀리초)은 채팅이 연속이어도 **단건**
  - similar `same_content` ≠ main+sub (similar는 대표 1장 유지)
  - 같은 스템 M≥2(M&lt;슬롯 N) → **있는 장만** 묶음 (Y)
  - 멤버 상한 **5** (main+4 sub); exact 경로(SNS/hold)에서는 main만 처리·sub 미첨부
  - payload: `item.sub_images: [{ sha256, rel_path }]` · multipart `file` + `sub_0`…

## 8. 로그

`kakao_import.logging_util` — 본문 전체 덤프 금지.

## 9. 매칭 — multi_room 같은 분 (캡션 합치기)

<!-- [변경사유]: 2026-08-05 — 여러 방 동시 「사진」이어도 포기하지 않고 파일 1 + 전 방 캡션 union -->

채팅 시각은 **분 단위**라 방 구분이 안 되고, 로컬 파일에는 방 정보가 없다.

| 상황 | 정책 |
|------|------|
| 같은 분에 방 ≥2 + 로컬 후보 ≥1 | **배정 유지**(medium·review). 이유 `multi_room_caption_union` |
| 캡션 | 그 분에 사진이 있던 **모든 방**의 앞/뒤 텍스트를 **같은 group에 add** (message_id 중복 제거) |
| 방 구분 | 방이 바뀔 때 `\n\n<------------- ADD 구분선 ------------>\n\n` 삽입 (`caption_sep.py`) |
| 방마다 설명이 거의 같음 | 업로드·text_merge 단계에서 동일 블록 중복 제거 |
| 같은 분·서로 다른 포스터 + 로컬 1장 | 과다 귀속 가능 — review 표시. 누락보다 과다 우선 |

하지 않음: 초로 “어느 방인지” 판별(채팅에 초 없음).  
상세 구현: `src/kakao_import/matcher.py`

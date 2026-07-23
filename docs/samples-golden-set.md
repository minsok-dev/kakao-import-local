# 실제 카카오 export 샘플 · 정답셋 (Phase 0)

<!-- [변경사유]: Phase 1 코드보다 샘플·정답이 선행 — parser/matcher 검증용 -->

## 목적

대화 parser와 이미지↔메시지 matcher의 **정답(golden)** 을 고정한다.  
개인정보는 가리되 **줄바꿈·시각·첨부 표시 형식은 유지**한다.

## 저장 위치

| 경로 | 커밋 | 설명 |
|------|------|------|
| `fixtures/samples/` (예정) | 마스킹본만 | export txt + 더미/마스킹 이미지 |
| `fixtures/golden/*.json` (예정) | ✅ | image → message 매핑 정답 |
| 실명 원본 | ❌ 커밋 금지 | 로컬 `input/` (gitignore) |

## 최소 시나리오 체크리스트

| ID | 시나리오 | 정답에 포함할 것 |
|----|----------|------------------|
| S01 | 일반 메시지 | 메시지 파싱 |
| S02 | 사진 1장 | image → message |
| S03 | 사진 여러 장 | 각 image → message(들) |
| S04 | 사진 **전** 설명 | 직전 텍스트 후보 |
| S05 | 사진 **후** 설명 | 직후 텍스트 후보 |
| S06 | 날짜 경계 | 일자 헤더 넘어 매칭 규칙 |
| S07 | 동일 이미지 반복 전송 | 동일 SHA, 서로 다른 메시지 시점 |
| S08 | 동일 이미지 + **다른** 메시지 | SHA exact + 텍스트 상이 |
| S09 | 시스템 메시지 | 파서 무시/별도 타입 |
| S10 | 내보내기 파일 겹침 | fingerprint로 멱등 |

## 정답 기록 형식 (예시)

```json
{
  "sample_id": "S03",
  "export_fingerprint": "<sha256-of-txt>",
  "images": [
    {
      "rel_path": "photos/001.jpg",
      "sha256": "<hex>",
      "message_ids": ["10", "11"],
      "match_status": "matched"
    },
    {
      "rel_path": "photos/002.jpg",
      "sha256": "<hex>",
      "message_ids": ["20"],
      "match_status": "matched"
    },
    {
      "rel_path": "photos/003.jpg",
      "sha256": "<hex>",
      "message_ids": [],
      "match_status": "ambiguous"
    }
  ]
}
```

텍스트 표기:

```text
image A → message 10, 11
image B → message 20
image C → ambiguous
```

## 마스킹 규칙 (요약)

- 실명 → `UserA`, 전화번호·개인 URL 마스킹  
- 줄바꿈, `[사진]`, 시각(`오전 10:12`) 패턴은 유지  
- 상세: [privacy-retention.md](./privacy-retention.md)

## 상태

| 항목 | 상태 |
|------|------|
| 시나리오 목록 | ✅ 문서화 |
| 실제 마스킹 fixture | ❌ 미착수 (운영자가 로컬 제공 후 작성) |
| golden JSON | ❌ 미착수 |

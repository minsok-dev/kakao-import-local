# Phase 3.5 — 카카오 Import 운영 안정화

<!-- [변경사유]: 2026-08-03 — Phase 4 전 데이터 품질·멱등 replay 안정화. caption은 same-source replace-if-richer -->

| 항목 | 내용 |
|------|------|
| 문서 버전 | **1.1** |
| 기준일 | 2026-08-03 |
| 선행 | Phase 3a/3b 기능 구현 (가져오기·exact·OCR 흐름) |
| 후행 | E2E 검증 → (병렬) Legacy/Similar 미디어 트랙 → **Phase 4** |
| 구현 상태 | **P1~P4 코드·단위테스트 완료** (스테이징/상용 E2E는 수동) |
| 관련 | [development-plan.md](./development-plan.md) · [phase3-import.md](./phase3-import.md) · `frontend/lib/ingest/exact/appendSnsCaption.ts` |

---

## 1. 한 줄 목표

Phase 3 **기능은 유지**한 채, 운영 replay·실패·빈 인접 메시지 상황에서  
**기존 데이터가 깨지거나 빈 caption에 고착되지 않게** 한다.

지금은 **Phase 4(로컬 similar UI) / watcher / 자동 승인을 하지 않는다.**

---

## 2. 현재 상태 판단

| 항목 | 상태 |
|------|------|
| 로컬 → Import API → ingest | ✅ 가능 |
| OCR/GPT 연결 | ✅ 가능 |
| Exact SNS 병합 구조 | ✅ 존재 (`mergeSnsCaptionAppend`) |
| 서버 Similar hold | ✅ 서버 담당 (로컬 중복 구현 불필요) |
| 멱등 replay 시 caption 개선 반영 | ❌ 고착 (UPDATE 없음) |
| empty adjacent 업로드 게이트 | ❌ 부족 |
| 삭제된 로컬 파일 정리 | ⚠️ 부분 (`file_missing` 실패만) |
| CLI UTF-8 / 요약 출력 | ⚠️ cp949 등에서 요약 깨짐 |

**핵심 사고 (덮어쓰기가 아님):**

```text
1차 upload: matched_messages=[] → caption에 "(인접 메시지 없음)" 저장
2차: 로컬 매칭 개선으로 본문 있음 → 동일 idempotency key
   → early return (UPDATE 없음)
   → 좋은 caption이 반영되지 않음 (고착)
```

---

## 3. Priority (이번 패치 범위)

### P1 — caption replay 정책 (최우선)

**문제:** 멱등 hit 시 caption을 갱신하지 않아, 빈/placeholder caption이 고착된다.

**원칙:**

| Case | existing | incoming | 결과 |
|------|----------|----------|------|
| 1 | placeholder / 빈 값 | 실질 본문 | **교체(fill)** |
| 2 | 카카오 GPT ref 본문 | 같은 출처·더 풍부한 본문 | **교체(replace-if-richer)** — append 금지 |
| 3 | 좋은 본문 | 빈 값 / placeholder | **유지** |
| 4 | 비카카오 SNS | 카카오 ref | `mergeSnsCaptionAppend` (Exact와 동일) |
| 5 | 동일 canonical | 동일 | **no-op** |

**왜 Case 2에서 Exact append를 쓰지 않는가**

카카오는 매번 `formatKakaoMessagesForGptRef`로 **전체 caption을 재생성**한다.  
blind append 시:

```text
1차: 메시지 A
2차: 메시지 A+B
→ A 블록 + --- + A+B 블록  (A 중복)
```

Exact(다른 SNS 출처 추가)와 **카카오(같은 출처·매칭만 개선)** 는 정책을 분리한다.

**구현 방향**

```text
mergeKakaoCaption(existing, incoming)  # 또는 resolveKakaoCaptionOnReplay
  → placeholder / empty 판정
  → 둘 다 카카오 ref면 richer 비교 후 교체
  → 이종 SNS면 mergeSnsCaptionAppend
  → caption_action: filled | replaced | appended | noop 로그
```

placeholder 예: 마커 `<<<KAKAO_ADJACENT_REF>>>` + 본문이 `(인접 메시지 없음)` 뿐.

멱등 hit 경로 (`receiveKakaoImport` early return)에서도:

1. incoming caption resolve  
2. OCR / 이관 content `sns_caption_text`에 반영될 때만 UPDATE  
3. skip 이유·action을 `@lib/logger`에 기록  

Exact/similar/신규 OCR happy path는 **변경하지 않는다.**

**중복 방지 참고:** Exact용 `mergeSnsCaptionAppend`는 이미 블록 canonical·가변 메타 무시 no-op이 있다.  
P1에서 “한 줄만 확인”으로 끝내지 말고, **카카오 same-source는 replace**를 명시한다.

---

### P2 — upload 전 `matched_messages` 분류

```text
dry-run / 실 upload
  → EMPTY_CONTEXT·FILE_MISSING·READY 통계

실 upload 기본:
  READY + EMPTY(이미지만) → 업로드
  FILE_MISSING → 스킵만 (배치 전체 차단 없음)

엄격 모드만:
  --require-adjacent → empty 있으면 배치 차단
```

**운영 원칙:** 포스터만 있는 이미지도 올려야 하므로 empty로 **전체 중지하면 안 된다.**  
`EMPTY_CONTEXT`는 경고·통계용이며, 기본 전송 대상에 포함한다.

---

### P3 — `file_missing` 정리

- upload 전 파일 존재 확인 (이미 있으면 유지)  
- 없으면 `file_missing`으로 기록·스킵 (일반 `failed`와 구분)  
- `hash`/`run` 시 없는 파일 **prune** 또는 재실행 시 **제외**  
- SQLite 상태머신·서버 DB migration **불필요**

---

### P4 — CLI UTF-8 · 요약 출력

- 데이터 손상 아님 — 운영자 로그 문제  
- 콘솔: `OK / FAIL / EMPTY_CONTEXT / FILE_MISSING` 요약만  
- 상세: UTF-8 `upload-result.json` (또는 동등)  
- Windows cp949에서 emoji JSON `UnicodeEncodeError` 방지  

---

## 4. 이번에 하지 않는 것

| 항목 | 이유 |
|------|------|
| Phase 4 로컬 Similar UI | 서버 Similar hold가 담당. 중복 구현 |
| Phase 1.5 watcher 자동 실행 | 잘못된 데이터 자동 유입 위험. 안정화 후 |
| Phase 5 자동 승인 | 목표가 자동 등록이 아니라 데이터 신뢰성 |
| 카카오→`tbl_ingest_raw` 통합 | 후속 |
| Exact/similar/신규 OCR 흐름 변경 | 회귀 금지 |

---

## 5. E2E 검증 (안정화 후)

| Case | 시나리오 | 기대 |
|------|----------|------|
| A | 새 이미지 + 인접 텍스트 | OCR 생성 · caption 저장 |
| B | 동일 이미지 replay · caption 개선 | placeholder→본문 반영 (고착 해소) |
| C | 다른 SNS 출처 추가 | Exact `mergeSnsCaptionAppend` |
| D | empty adjacent | 게이트 경고/차단 (`--allow-empty-caption` 시만 통과) |
| E | 로컬 파일 삭제 후 upload | `file_missing`, 요약에 반영 |
| F | 동일 caption 재전송 | no-op · 중복 append 없음 |

---

## 6. 권장 개발 순서

```text
[완료] Media Asset / Similar 서버 / Exact SNS 구조 / 카카오 Phase 3 기능

↓

[지금] Phase 3.5 운영 안정화
  1. caption replay (replace-if-richer)
  2. matched_messages gate
  3. file_missing prune/제외
  4. UTF-8 로그

↓

[검증] §5 E2E

↓

[이후 · 병렬 가능]
  Legacy 백필 확대
  Similar enforce 운영 점검

↓

Phase 4 로컬 Similar UI
↓
Phase 5 제한 자동 승인
```

Cursor/구현 요청은 **「Phase 3 기능 추가」가 아니라 「Phase 3.5 P1~P4 + E2E」** 로 묶는다.

---

## 7. 변경 이력

| 버전 | 내용 |
|------|------|
| 1.0 | 초안 — 고착 원인 정정, same-source replace-if-richer, P2~P4, E2E, 미룸 항목 |
| **1.1** | P1~P4 구현 반영 — `mergeKakaoCaption` / replay refresh / upload gate·prune·UTF-8 요약 |

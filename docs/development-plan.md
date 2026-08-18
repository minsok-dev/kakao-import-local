# 카카오톡 로컬 수집 · Import — 개발 계획서

<!-- [변경사유]: v1.4 — Phase 4.2 similar upload policy 반영. deferred 차단·grouped_photo 후보 메타 -->

| 항목 | 내용 |
|------|------|
| 문서 버전 | **1.4** |
| 기준일 | 2026-08-04 |
| 레포 | `kakao-import-local` (로컬) · `frontend` / `backend` (Phase 3~) |
| 입력 샘플 | `input/raw/<room_id>/chats/` + `photos/` (gitignore). 구 평탄 레이아웃은 `_legacy` |
| 확정 golden | [golden-esencia-20260724-0050.md](./golden-esencia-20260724-0050.md) |
| **현재 초점** | **Phase 4.2 similar upload policy 반영** (`deferred` 차단 + dry-run/result 노출) |

관련: [phase-plan.md](./phase-plan.md) · [phase0-contracts.md](./phase0-contracts.md) · [privacy-retention.md](./privacy-retention.md) · [samples-golden-set.md](./samples-golden-set.md) · [phase05-signature-feasibility.md](./phase05-signature-feasibility.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [how-to-provide-samples.md](./how-to-provide-samples.md) · [phase3-import.md](./phase3-import.md) · [phase3-ops-stabilization.md](./phase3-ops-stabilization.md) · [kakao-pc-collect-plan.md](./kakao-pc-collect-plan.md) · [poster-classifier-plan.md](./poster-classifier-plan.md) · [poster-classifier-dev.md](./poster-classifier-dev.md) · **[ingest-dedup-reject-plan.md](./ingest-dedup-reject-plan.md)** (SHA 재사용·거부 목록, 2026-08-19 초안·착수 전)

---

## 1. 한 줄 목표

PC 카카오톡에서 받은 **대화 txt + 공용 사진 폴더**를 로컬에서  
**파싱 → (파일명 시각) 메시지 매칭 → SHA/텍스트 정리 → Import** 하고,  
서버에서 **OCR + 카카오 인접 메시지 → GPT → 관리자 검수 → 콘텐츠**로 이어진다.

개인 카카오톡을 자동 수집하는 **공식 범용 API는 없으므로**, PC 다운로드·내보내기 파일을 사용한다.  
Instagram/밴드 **크롤 어댑터와 동일시하지 않는다.**

---

## 2. 확정된 입력·매칭 (실측 반영)

### 2.1 디렉터리 (1급 레이아웃)

```text
input/raw/
  <room_id>/
    chats/          # 해당 방 대화 txt
    photos/         # 해당 방 이미지
  chats/ + photos/  # 구 레이아웃(_legacy) — golden·기존 파일 호환
```

- 방별로 txt·사진을 나눈다. 매칭은 **같은 room_id 안에서만**.
- 구 `chats/`+`photos/` 공용 풀은 `_legacy` 로 스캔한다.

### 2.2 대화 형식 (PC 내보내기)

```text
{방제목} 님과 카카오톡 대화
저장한 날짜 : ...
--------------- YYYY년 M월 D일 요일 ---------------
[닉네임] [오전|오후 H:MM] 본문
[닉네임] [오전|오후 H:MM] 사진
[닉네임] [오전|오후 H:MM] 사진 N장
```

첨부 마커는 **줄 끝** `사진` / `사진 N장` / `동영상` / `이모티콘` / `파일:…` 만.  
본문 중 “얼굴사진” 등은 첨부 아님.

### 2.3 이미지↔메시지 매칭 (핵심 규칙)

| 방식 | 판정 |
|------|------|
| txt 안에 `KakaoTalk_….png` **문자열** | ❌ 없음 (실측 0) |
| 파일명 `KakaoTalk_YYYYMMDD_HHMMSSmmm` → **datetime 파싱** 후 메시지 절대시각과 대조 | ✅ **주 경로** |

**확정 golden**

| 파일 | 파일명 시각 | 메시지 |
|------|-------------|--------|
| `KakaoTalk_20260724_005030533.png` | 00:50:30 | `…0516_47_889_group.txt` / 2026-07-24 `[ESENCIA KOREA] [오전 12:50] 사진` |
| `KakaoTalk_20260724_005034512.png` | 00:50:34 | 같은 분 두 번째 `사진` |

직후 12:53·12:54 안내 문구 = 인접 텍스트(GPT·merge용).

**Matcher 알고리즘 (Phase 1 필수)**

1. `KakaoTalk_` 파일만 시각 추출 (비표준 파일명은 별도/수동).
2. 메시지 절대시각 = **직전 일자 헤더 날짜** + `[오전/오후 h:mm]`.
3. 동일 분(또는 짧은 윈도우)의 `사진` / `사진 N장` 후보 수집.
4. 같은 분에 사진 줄 K개 · 파일 K개 → **초 단위 정렬 후 1:1**.
5. <!-- [변경사유]: 부분 매칭 — 누락보다 과다 귀속 우선 -->
   후보 개수 불일치여도 **있는 파일은 순서대로 배정**(medium+review). 부족 슬롯·남는 파일만 review.
   매칭된 파일은 **동일 group_text**를 공유한다.
6. 후보가 여러 방·여러 화자 → `ambiguous` → review.
7. 디스크에 없는 `[사진]` 슬롯은 “파일 없음” (메시지 슬롯 ≫ 파일 수 — 정상).
8. 동일 SHA 1파일 ↔ 여러 방 메시지 = **다대다** 허용.
9. <!-- [변경사유]: 선행 텍스트 ≤2분 귀속 -->
   설명 텍스트: 사진 **앞**(같은 발신자·≤2분) + 사진 **뒤**(기존 max_gap) 모두 수집(앞→뒤 순서).

### 2.4 실측으로 알아 둔 규모

- chats 7개, photos **660**
- 메시지상 사진 슬롯 ≫ 660 → **파일 있는 쪽 기준**으로 매칭
- export 파일명의 `0516_47` 등은 **내보내기 시각**, 매칭에 쓰지 않음

---

## 3. 전체 흐름

```text
[PC 카톡] 서랍 저장 + 대화 내보내기
        │
        ▼
input/raw/photos + input/raw/chats
        │
        ▼
[kakao-import-local]
  parse chats → messages
  index photos (SHA + 파일명 시각)
  match (시각 1:1 / ambiguous)
  local history (멱등)
  SHA exact (로컬·이력)
  (Phase2) text merge / modes
  (Phase3.5) caption replay · empty gate · file_missing · UTF-8  ✅
  (Phase4.1) similar review UI · partial subgroup  ✅
  (Phase4.2) similar upload policy + deferred 차단 + grouped-photo 후보 메타  ← 지금
  report: matched | ambiguous | unmatched
        │
        ▼  Phase 3
[Import API]  auto_register=off, 관리자 검수
        │
        ▼
ImportItem → matched/merged message 저장
  → OCR (importItemId)
  → OCR text + Kakao 인접 메시지 → GPT
  → contentIdx 기록
        │
        ▼
관리자 검수 → 서비스 콘텐츠
```

일반 방문자는 Import UI를 보지 않음. 공개 사이트는 기존과 동일.

---

## 4. Phase 구조 (확정)

| Phase | 우선 | 내용 | 완료의 뜻 / 상태 |
|-------|-----:|------|------------------|
| **0** | — | 계약·golden·개인정보·SQLite 초안·payload | ✅ 규칙·정답 문서 합의 |
| **0.5** | 낮음 | signature Python 사용 **조사만** | 연동 방식 1택 (Phase 4 직전 재확인) |
| **1** | — | parser + **시각 matcher** + 이력 + SHA + 리포트 | ✅ golden 자동 통과 |
| **1.5** | 보류 | watcher / 자동 실행 | **3.5 안정화·E2E 후** (잘못된 자동 유입 방지) |
| **2** | — | 텍스트 정규화·merge·safe/balanced/auto | ✅ 구현 완료 |
| **3** | — | Import·인증·서버 exact → **SNS 병합 공통 서비스** | ✅ 기능 구현 (스테이징 수동 검증·운영 이슈는 3.5) |
| **3.5** | 완료 | **운영 안정화** — caption replay · matched_messages gate · file_missing · UTF-8 | 데이터 고착·오염 해소 + E2E |
| **4** | 진행중 | similar + **그룹 합침/분리 UI** + upload policy | 4.1 review UI ✅ / 4.2 로컬 업로드 반영 진행 |
| **5** | 마지막 | 제한 자동 승인 | 화이트리스트만 |

### 우선순위 한 줄

```text
[기능 완료] Phase 0~3
→ [완료] Phase 3.5 운영 안정화 + E2E
→ (병렬 가능) Legacy 백필 / Similar enforce 점검
→ Phase 4.2 similar upload policy / grouped-photo 구조 보강
→ Phase 1.5 watcher · Phase 5 제한 자동화
```

**판단:** Phase 3.5 안정화가 끝난 뒤에는,  
Phase 4.2에서 **similar decision을 실제 업로드 후보에 반영**하는 것이 다음 우선이다.

---

## 5. Phase별 상세

### Phase 0 — 계약·정답·개인정보

| 할 일 | 산출 |
|-------|------|
| 입력 레이아웃·매칭 규칙 고정 | 본 문서 §2 |
| golden 추가 (최소 시나리오) | `docs/golden-*.md`, 이후 `fixtures/golden/` |
| 개인정보·보존 | [privacy-retention.md](./privacy-retention.md) |
| SQLite: fingerprint·batch·link·decision 초안 | `sql/001` + `002` 예정 |
| Import payload 초안 (절대경로 금지, 인접 메시지만) | [phase0-contracts.md](./phase0-contracts.md) |

**상태:** 레이아웃·주 매칭 규칙·1건 golden **확정**. 마스킹 fixture·추가 golden은 계속.

---

### Phase 0.5 — Signature feasibility (구현 금지)

`danceinfo_image_signature`를 이 PC Python에서 쓸 수 있는지 확인 후 하나만 고른다.

1. Python binding 직접  
2. CLI/subprocess  
3. 로컬 SHA만 · signature는 서버  
4. 동일 규격 재구현 + cross-language fixture  

체크리스트: [phase05-signature-feasibility.md](./phase05-signature-feasibility.md)

---

### Phase 1 — Parser · Matcher · History · SHA (**최우선**)

**완료 기준 (전부 만족):**

```text
chats/*.txt 파싱
+ photos/ 인덱싱 (SHA + 파일명 시각)
+ 시각 기반 이미지↔메시지 매칭 (golden 통과)
+ SQLite 멱등 재실행 (중복 행 없음)
+ SHA exact (로컬·과거 이력)
+ matched / ambiguous / unmatched / no-file 리포트
+ 원본 파일 불변
```

| 모듈 | 역할 |
|------|------|
| `parser` | 일자 헤더·메시지·첨부 마커 |
| `photo_index` | 공용 폴더, SHA, `KakaoTalk_` 시각 |
| `matcher` | §2.3 알고리즘 |
| `db` | batch / fingerprint / media_message_link / upsert |
| `match_sha` | exact |
| `report` | CLI/파일 리포트 |
| `cli` | `init-db`, `scan`, `parse`, `match`, `status`, `report` |

**넣지 않음:** similar, Import 업로드, 완성형 GUI, watcher.

**상태:** ✅ **구현 완료** (ESENCIA golden 통과).

**SQLite가 보장할 것**

- export fingerprint, 이미지 상대경로+fingerprint, 메시지 fingerprint  
- batch id, 처리 결과, 사용자 결정  
- 서버 import/OCR id (Phase 3에서 채움)  
- 과거 Import 이미지 SHA 축적  
- 동일 입력 재실행 시 UNIQUE upsert로 **중복 INSERT 금지**

---

### Phase 1.5 — Watcher · 자동 실행

폴더 감시, 안정화 대기, (선택) 작업 스케줄러.

**보류:** Phase **3.5** 안정화·E2E 통과 전 착수하지 않는다.  
자동화하면 empty adjacent·매칭 실패 데이터가 그대로 유입될 수 있다.

PC 카톡에서 txt·사진을 **꺼내는** 도구는 1.5 watcher가 아니다. 별도 계약: [kakao-pc-collect-plan.md](./kakao-pc-collect-plan.md).

---

### Phase 2 — 텍스트 merge · 모드

**상태: 구현 완료** (`merge` / `merge-undo` / `merge-decide`, `MERGE_MODE`, `sql/002`+`003`)

**기본: balanced**

```text
exact 이미지 + 동일 텍스트     → 자동 collapse
exact 이미지 + 다른 텍스트     → 고유 문장 병합 → 중요 충돌 시 review
similar / unmatched            → review (similar=Phase4, unmatched=Phase1 matcher)
```

옵션: `safe` | `balanced` | `auto`  
`auto`에서도 **similar 자동 통합 자체 금지**.  
(현재 `auto`와 `balanced` 병합 로직은 동일 — similar 금지 명시용)

병합 시 보존: 원문, 시각, 출처 메시지 ID, 전/후, 자동·수동 결정, 충돌, **되돌리기**.

| CLI | 역할 |
|-----|------|
| `merge [--mode]` | 자동 merge |
| `merge-undo --id` / `--sha256` | active → superseded, 직전 이력 복구 |
| `merge-decide --id --action accept\|set-text\|reject` | review 수동 결정 |

---

### Phase 3 — Import · 서버 exact · OCR/GPT

**상태**:  
- 3a 접수·안전 ✅ — `automated tests passed / staging manual verification pending`  
- 3b 승인→OCR + 최소 UI ✅ — [phase3-import.md](./phase3-import.md)  
- exact SNS 병합 정책·코드: `frontend/docs/image-exact-sns-merge-policy.md` (구조 ✅)  
- **운영 이슈(caption 고착·empty gate 등) → Phase 3.5**

**미포함**: similar 차단 본구현, watcher, 자동 승인, 카카오→raw 통합, 카카오 자동 수집

**인증 (선결정)**  
- 초기: `auto_register=off`, 관리자 검수 후 Import  
- Cookie를 로컬 프로그램에 저장 **금지**  
- 이후 자동화 시 Import 전용 토큰

**대화 export 크기 (운영)**  
- PC 내보내기는 전체 이력이 올 수 있음 → 로컬은 전체 파싱, 서버는 인접만  
- 실서비스: 이전 대화 삭제 후 재다운로드 권장 (도구가 원본 txt 자르지 않음)

**ingest 재사용**

| 재사용 | 재사용 안 함 |
|--------|----------------|
| raw 이후 parse, OCR, GPT, 콘텐츠 | Band/IG 크롤, SNS URL 규칙 |
| | exact → 무조건 `skipped_dup` |

카카오 exact: `skip` / `review` / `reuse asset + OCR` 중 **별도 정책**.

**서버 exact 범위 (자동화 전 확인·보완)**

```text
OCR main/sub · 콘텐츠 main/sub · media asset · 과거 Kakao Import
(+ OCR sub SHA, ocrcontent_media·content_media 전수, asset/signature 없는 레거시)
```

**GPT 계약 (필수)**

```text
ImportItem
→ matched/merged message 저장
→ OCR + importItemId
→ OCR text + Kakao 인접 메시지 → GPT
→ contentIdx → ImportItem (캐시)
```

- **contentIdx SSOT**: `tbl_ocrcontent.migrated_content_idx`
- **Import `response_json.content_idx`**: 상세 조회 시 lazy sync 캐시 (승인 직후 null)
- **auto_migrate**: 확정 정책상 카카오 **신규**는 크롤과 같이 자동 이관 정합 (`frontend/docs/image-exact-sns-merge-policy.md`).

ingest에만 쌓이고 GPT에 안 쓰는 경로 **금지**.

관리자 UI: `/admin_w/ingest/import` 목록·상세 + 기존 OCR/콘텐츠 검수.

---

### Phase 3.5 — 운영 안정화 (**지금 최우선**)

상세 전문: **[phase3-ops-stabilization.md](./phase3-ops-stabilization.md)**

기능 추가가 아니라 **데이터 품질·멱등 replay 안정성**이다.

| P | 내용 | 요지 |
|---|------|------|
| **P1** | caption replay | 문제는 overwrite가 아니라 **고착**. placeholder→fill. `sns_caption_text` 는 카카오·이종 모두 **`mergeSnsCaptionAppend`**. 빈 incoming→유지 |
| **P2** | matched_messages gate | dry-run 허용. 실전송 기본 경고/차단. `--allow-empty-caption` 예외 |
| **P3** | file_missing | 존재 확인·스킵·prune/제외. DB migration 없음 |
| **P4** | UTF-8 CLI | 콘솔 요약만 + UTF-8 결과 파일 |

**완료 기준:** P1~P4 구현 + [E2E Case A~F](./phase3-ops-stabilization.md#5-e2e-검증-안정화-후) 통과.  
**하지 않음:** Phase 4 / watcher / 자동 승인 / Exact·similar happy path 변경.

---

### Phase 4 — Similar · 그룹 결정 UI / 업로드 반영

**착수 조건:**  
- **업로드 제어·시각 리뷰 UI (4.2):** Phase **3.5** + E2E 완료 후 착수  
- **탐지-only (4.0):** signature + 그룹 탐지 + 로그/(선택) decision 저장 — upload 미변경이면 **3.5와 병렬 가능**

- Phase 0.5에서 고른 방식으로 signature  
- **N≥2 그룹 content decision:** `same_content` / `different_content` / `partial` / `deferred`  
- **upload policy (decision과 분리):** `same_content` → 대표 1장, `different_content` → 전체, `partial` → subgroup 대표+단독, `deferred` → 기본 차단
- 재결정: 전송 전 로컬 decision만 변경 (원본 파일 불변)  
- **시각 리뷰 UI 필수** (Python 로컬 웹 또는 관리자 Next — Phase 4 본구현 착수 시 선택)  
  <!-- [변경사유]: Phase 4.1 — 로컬 `similar-review` 썸네일 UI(decision only) 제공 -->
  - **4.1:** `kakao-import similar-review` — 로컬 브라우저 썸네일 + decision + **partial 서브그룹** (upload 미적용)  
  - **4.2:** upload policy로 큐 반영 + dry-run/result JSON 사유 노출 ✅  
  <!-- [변경사유]: Phase 4.2+ 구현 — 채팅 매칭 묶음 main+sub -->
  - **4.2+:** 채팅 `image_group` 묶음 → 서버 1 OCR main+sub ✅ (similar는 대표 1장 유지 · 불일치 시 있는 장만 · 상한 5 · exact 시 sub 미첨부)
- similar **자동 병합·자동 삭제 없음**  
- 서버 Similar hold가 이미 있으므로, 로컬 similar는 **배치 안 정리·운영 보조** (서비스 전체 중복 방지 본경로 아님)

#### 추가 검토 메모 — 동일 시간대·동일 발신자 묶음 사진의 1콘텐츠 등록

<!-- [변경사유]: 2026-08-04 — 운영 검토 요청. 카카오 `사진`/`사진 N장` 묶음을 1개 콘텐츠의 main+sub로 등록하는 후속 요구 기록 -->

현재 카카오 import는 서버 업로드 자체는 여전히 **사진 1장 중심**이다.  
즉, 카카오에서 같은 발신자가 같은 시각대에 올린 묶음 사진이라도:

- 매칭용으로는 `photo` / `photo_multi` 슬롯 묶음
- Similar 관점에서는 유사 이미지 그룹
- 업로드/콘텐츠 관점에서는 **개별 사진 단위**

로 처리하며, **하나의 콘텐츠에 main + sub poster 세트로 자동 등록하지 않는다.**

운영에서 검토할 후속 요구:

```text
같은 발신자 + 같은 분(또는 짧은 윈도우) + 연속 사진/사진 N장
→ 1개 콘텐츠 후보 그룹으로 해석
→ 대표(main) 1장 + sub 나머지
```

대표 사례:

```text
[달콩, Dalkong] [오후 1:23] 사진
[달콩, Dalkong] [오후 1:23] 사진
[달콩, Dalkong] [오후 1:23] 사진
[달콩, Dalkong] [오후 1:23] [CASE-B] 화요 바차타 특강 안내
```

위 케이스는 현재 로직에서는 **사진 슬롯 3개 + 후속 설명 1개**로 본다.  
로컬 파일이 1장뿐이면 count mismatch로 `unmatched`가 되기 쉽고, 후속 설명도 대표 1장에 자동 귀속되지 않는다.

향후 구현 시 검토할 규칙:

1. **그룹 기준:** 동일 발신자·동일 분(또는 tolerance)·연속 `사진` / `사진 N장`
2. **등록 단위:** 1개 콘텐츠 후보 그룹
3. **대표 선택:** 첫 장 / 수동 지정 / 해상도·용량 우선 등 별도 규칙
4. **sub 등록:** 대표 외 멤버를 서브 포스터로 연결
5. **텍스트 귀속:** 후속 group_text를 그룹 전체 설명으로 보고 대표 콘텐츠에 저장
6. **개수 불일치:** 채팅 슬롯 수 ≠ 로컬 파일 수일 때 보수적으로 review 또는 partial 처리
7. **Similar/Exact와 충돌:** 같은 콘텐츠 그룹화와 similar decision, exact reuse 간 우선순위 정리 필요

**4.2+ 반영 범위:**  
- similar 이후 채팅 매칭 그룹을 1 upload request(main+`sub_images`)로 붕괴.
- 서버 `/api/admin/ingest/import/kakao` multipart 다장 → OCR `sub_poster_urls` + media job.
- exact/SNS/hold 경로에서는 main만 처리(sub 미첨부).

→ [similar-group-decisions.md](./similar-group-decisions.md)

---

### Phase 5 — 제한 자동 승인

검증된 규칙만; 감사 로그·롤백; similar auto-merge / overwrite 금지 유지.  
**3.5·4 이후.** 목표가 “자동 등록”이 되기 전에 **데이터 신뢰성**이 먼저다.

---

## 6. 나중에 해도 되는 것 (3.5를 막지 않음)

Electron/PySide 완성형 GUI, 트레이, 시작 시 자동 실행, 멀티 PC 동기화,  
service token 완전 자동 전송, similar 자동 통합,  
기존 콘텐츠 자동 보완, 대표 이미지 자동 교체, legacy 대량 백필, 운영 대시보드,  
카카오→`tbl_ingest_raw` 통합.

로컬 포스터 vs 잡사진 분류는 위 “나중” 목록이 아니라 **병렬 트랙**이다.  
계약 [poster-classifier-plan.md](./poster-classifier-plan.md) · 개발 [poster-classifier-dev.md](./poster-classifier-dev.md).

**PC 카톡 준자동 수집**(검색·Ctrl+S·서랍 50칸·좌표 3곳·Documents→photos)은 import와 **분리**한다.  
계약: [kakao-pc-collect-plan.md](./kakao-pc-collect-plan.md). 하이브리드(UIA+최소 좌표). 끝나면 `run`+`similar-detect`만 호출하고 upload는 similar-review 후.

Legacy 백필·Similar enforce는 카카오 3.5와 **축이 다름** — 3.5·E2E 후 **병렬 가능**.

---

## 7. 레포 경계

| | kakao-import-local | frontend | backend |
|--|--------------------|----------|---------|
| 0~2, 1.5 | ✅ | — | 0.5만 패키지 |
| 3 | upload CLI | ✅ Import/exact/GPT 연결 | OCR 입력 |
| **3.5** | gate · file_missing · UTF-8 | ✅ **caption replay merge** | — |
| 4 | 로컬 리뷰 UI(선택) | 관리자 리뷰 UI | signature |

---

## 8. 즉시 다음 액션

1. **Exact SNS 병합 · F hold · DDL 015/016** — **상용 운영 중** (2026-08-09 운영자 확인). 문서 불일치 해소됨.
2. **Phase 4.2+** 검증: [phase42-bundle-verification-checklist.md](./phase42-bundle-verification-checklist.md) — **2026-08-06 현재까지 오류 없음** (미관측: 멤버>5·50MiB만)
3. (병렬) Legacy 백필 · Similar enforce 운영 점검
4. (후속·선택) Phase 1.5 watcher · Phase 5
5. caption-only ledger · similar 서명 30MiB — **2026-08-09 구현**

---

## 9. 변경 이력

| 버전 | 내용 |
|------|------|
| 1.0 | 초기 Phase 0~5 |
| 1.1 | parser 우선·이력·개인정보·0.5·서버 exact/GPT·similar 그룹 |
| 1.2 | **공용 photos+방별 chats 확정**, **파일명 시각 매칭 주 경로**, ESENCIA golden, Phase 1 완료=해당 테스트 통과, 계획서 재정리 |
| **1.3** | **Phase 3.5 운영 안정화 최우선**. caption 고착·fill + sns append. P2 gate·P3 file_missing·P4 UTF-8. Phase 4/1.5/5 보류 조건. Phase 1 상태 ✅ 정합 |
| **1.4** | **Phase 4.2 반영**. similar decision 기반 upload 후보 필터, `deferred` 기본 차단, dry-run/result 사유 노출, `grouped_photo_candidates` 메타 추가 |
| **1.5** | **Phase 4.2+**. 채팅 매칭 묶음 → 서버 main+sub 1 OCR (C+Y, 상한 5, exact 시 sub 미첨부) |
| **1.6** | `_01` 파싱 · multi_room 캡션 union+ADD 구분선 · 터미널 한글 요약. 실데이터 검증 **현재까지 오류 없음** |
| **1.7** | Exact SNS·F hold·015/016 **상용 운영** 문서 동기화 (2026-08-09) |
| **1.8** | caption-only ledger · similar 서명 30MiB |
| **1.9** | PC 카톡 수집 도구 계약 — [kakao-pc-collect-plan.md](./kakao-pc-collect-plan.md) (import와 분리, 2026-08-17) |
| **1.10** | PC 수집 하이브리드 실측 — Ctrl+S txt, 서랍 가상스크롤·다운로드 좌표 3곳, 워터마크 배치 중단 |

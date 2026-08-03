# 카카오톡 로컬 수집 · Import — 개발 계획서

<!-- [변경사유]: v1.3 — Phase 3.5 운영 안정화(최우선). caption replace-if-richer·gate·file_missing·UTF-8. Phase 4 보류 -->

| 항목 | 내용 |
|------|------|
| 문서 버전 | **1.3** |
| 기준일 | 2026-08-03 |
| 레포 | `kakao-import-local` (로컬) · `frontend` / `backend` (Phase 3~) |
| 입력 샘플 | `input/raw/chats/` + `input/raw/photos/` (gitignore) |
| 확정 golden | [golden-esencia-20260724-0050.md](./golden-esencia-20260724-0050.md) |
| **현재 최우선** | **[Phase 3.5 운영 안정화](./phase3-ops-stabilization.md)** (Phase 4 전) |

관련: [phase-plan.md](./phase-plan.md) · [phase0-contracts.md](./phase0-contracts.md) · [privacy-retention.md](./privacy-retention.md) · [samples-golden-set.md](./samples-golden-set.md) · [phase05-signature-feasibility.md](./phase05-signature-feasibility.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [how-to-provide-samples.md](./how-to-provide-samples.md) · [phase3-import.md](./phase3-import.md) · [phase3-ops-stabilization.md](./phase3-ops-stabilization.md)

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
  chats/          # 방별 대화 export (*.txt)만
  photos/         # 모든 방 이미지 공용 풀
```

- 방별로 사진을 나누지 **않는다** (중복 보관 비효율).
- 스캐너는 `photos_root` + `chat_files[]` 분리. “방 폴더 안 사진” 가정 **폐기**.

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
5. 후보가 여러 방·여러 화자·개수 불일치 → `ambiguous` → review.
6. 디스크에 없는 `[사진]` 슬롯은 “파일 없음” (메시지 슬롯 ≫ 파일 수 — 정상).
7. 동일 SHA 1파일 ↔ 여러 방 메시지 = **다대다** 허용.

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
  (Phase3.5) caption replay · empty gate · file_missing · UTF-8  ← 지금
  (Phase4) similar groups + 결정 UI  ← 안정화·E2E 후
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
| **3.5** | **지금 최우선** | **운영 안정화** — caption replay · matched_messages gate · file_missing · UTF-8 | 데이터 고착·오염 해소 + E2E |
| **4** | 이후 | similar + **그룹 합침/분리 UI** | review only · **3.5 완료 전 착수 금지** |
| **5** | 마지막 | 제한 자동 승인 | 화이트리스트만 |

### 우선순위 한 줄

```text
[기능 완료] Phase 0~3
→ [지금] Phase 3.5 운영 안정화 + E2E
→ (병렬 가능) Legacy 백필 / Similar enforce 점검
→ Phase 4 로컬 similar UI
→ Phase 1.5 watcher · Phase 5 제한 자동화
```

**판단:** 카카오는 “기능 개발”이 아니라 **운영 안정화** 단계다.  
새 큰 기능(Phase 4+)보다 **데이터 품질·반복 실행 안정성**이 우선이다.  
상세: [phase3-ops-stabilization.md](./phase3-ops-stabilization.md)

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
| **P1** | caption replay | 문제는 overwrite가 아니라 **고착**. same-source는 **replace-if-richer**(append 금지). placeholder→fill. 빈 incoming→유지. 이종 SNS만 `mergeSnsCaptionAppend` |
| **P2** | matched_messages gate | dry-run 허용. 실전송 기본 경고/차단. `--allow-empty-caption` 예외 |
| **P3** | file_missing | 존재 확인·스킵·prune/제외. DB migration 없음 |
| **P4** | UTF-8 CLI | 콘솔 요약만 + UTF-8 결과 파일 |

**완료 기준:** P1~P4 구현 + [E2E Case A~F](./phase3-ops-stabilization.md#5-e2e-검증-안정화-후) 통과.  
**하지 않음:** Phase 4 / watcher / 자동 승인 / Exact·similar happy path 변경.

---

### Phase 4 — Similar · 그룹 결정 UI

**착수 조건:**  
- **업로드 제어·시각 리뷰 UI (4.2+):** Phase **3.5** + E2E 완료 후.  
- **탐지-only (4.0):** signature + 그룹 탐지 + 로그/(선택) decision 저장 — upload 미변경이면 **3.5와 병렬 가능**.

- Phase 0.5에서 고른 방식으로 signature  
- **N≥2 그룹 content decision:** `same_content` / `different_content` / `partial` / `deferred`  
- **upload policy (decision과 분리):** `same_content` → 대표 1장, `different_content` → 전체, `deferred` → 없음  
- 재결정: 전송 전 로컬 decision만 변경 (원본 파일 불변)  
- **시각 리뷰 UI 필수** (Python 로컬 웹 또는 관리자 Next — Phase 4 본구현 착수 시 선택)  
- similar **자동 병합·자동 삭제 없음**  
- 서버 Similar hold가 이미 있으므로, 로컬 similar는 **배치 안 정리·운영 보조** (서비스 전체 중복 방지 본경로 아님)

→ [similar-group-decisions.md](./similar-group-decisions.md)

---

### Phase 5 — 제한 자동 승인

검증된 규칙만; 감사 로그·롤백; similar auto-merge / overwrite 금지 유지.  
**3.5·4 이후.** 목표가 “자동 등록”이 되기 전에 **데이터 신뢰성**이 먼저다.

---

## 6. 나중에 해도 되는 것 (3.5를 막지 않음)

Electron/PySide 완성형 GUI, 트레이, 시작 시 자동 실행, 멀티 PC 동기화,  
service token 완전 자동 전송, similar 자동 통합, CLIP,  
기존 콘텐츠 자동 보완, 대표 이미지 자동 교체, legacy 대량 백필, 운영 대시보드,  
카카오→`tbl_ingest_raw` 통합.

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

1. **Phase 3.5 P1~P4** 구현 ([phase3-ops-stabilization.md](./phase3-ops-stabilization.md))  
   - 서버: 멱등 replay 시 `mergeKakaoCaption` / replace-if-richer  
   - 로컬: empty adjacent gate · file_missing prune · UTF-8 요약  
2. **E2E** Case A~F (신규 / replay 개선 / Exact append / empty gate / file_missing / no-op)  
3. (이후·병렬) Legacy 백필 · Similar enforce 운영 점검  
4. Phase **4** — 3.5 완료 전 착수하지 않음  

---

## 9. 변경 이력

| 버전 | 내용 |
|------|------|
| 1.0 | 초기 Phase 0~5 |
| 1.1 | parser 우선·이력·개인정보·0.5·서버 exact/GPT·similar 그룹 |
| 1.2 | **공용 photos+방별 chats 확정**, **파일명 시각 매칭 주 경로**, ESENCIA golden, Phase 1 완료=해당 테스트 통과, 계획서 재정리 |
| **1.3** | **Phase 3.5 운영 안정화 최우선**. caption 고착(덮어쓰기 아님)·same-source replace-if-richer. P2 gate·P3 file_missing·P4 UTF-8. Phase 4/1.5/5 보류 조건. Phase 1 상태 ✅ 정합 |

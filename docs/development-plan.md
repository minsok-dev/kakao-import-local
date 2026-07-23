# 카카오톡 로컬 수집 · Import — 개발 계획서

<!-- [변경사유]: v1.2 — 실측 입력 레이아웃·파일명 시각 매칭·golden 반영, Phase 정의 명확화 -->

| 항목 | 내용 |
|------|------|
| 문서 버전 | **1.2** |
| 기준일 | 2026-07-24 |
| 레포 | `kakao-import-local` (로컬) · `frontend` / `backend` (Phase 3~) |
| 입력 샘플 | `input/raw/chats/` + `input/raw/photos/` (gitignore) |
| 확정 golden | [golden-esencia-20260724-0050.md](./golden-esencia-20260724-0050.md) |

관련: [phase-plan.md](./phase-plan.md) · [phase0-contracts.md](./phase0-contracts.md) · [privacy-retention.md](./privacy-retention.md) · [samples-golden-set.md](./samples-golden-set.md) · [phase05-signature-feasibility.md](./phase05-signature-feasibility.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [how-to-provide-samples.md](./how-to-provide-samples.md) · [phase3-import.md](./phase3-import.md)

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
  (Phase4) similar groups + 결정 UI
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

| Phase | 우선 | 내용 | 완료의 뜻 |
|-------|-----:|------|-----------|
| **0** | 지금 | 계약·golden·개인정보·SQLite 초안·payload | 규칙·정답 문서 합의 |
| **0.5** | 지금 | signature Python 사용 **조사만** | 연동 방식 1택 고정 |
| **1** | **최우선** | parser + **시각 matcher** + 이력 + SHA + 리포트 | **위 golden 자동 통과** |
| **1.5** | 이후 | watcher / 자동 실행 | Phase 1 통과 후 |
| **2** | 다음 | 텍스트 정규화·merge·safe/balanced/auto | 충돌·되돌리기 보존 |
| **3** | 다음 | Import·인증·서버 exact 범위·OCR/GPT | 스테이징 1건 E2E |
| **4** | 이후 | similar + **그룹 합침/분리 UI** | review only |
| **5** | 마지막 | 제한 자동 승인 | 화이트리스트만 |

### 우선순위 한 줄

```text
golden·계약 → signature 조사
→ parser/matcher(시각) → SQLite 멱등 → SHA
→ 텍스트 merge → Import/OCR/GPT → similar UI → 제한 자동화
```

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

**현재 코드:** 폴더 스캔·SHA stub만 → Phase 1 **미완료**.

**SQLite가 보장할 것**

- export fingerprint, 이미지 상대경로+fingerprint, 메시지 fingerprint  
- batch id, 처리 결과, 사용자 결정  
- 서버 import/OCR id (Phase 3에서 채움)  
- 과거 Import 이미지 SHA 축적  
- 동일 입력 재실행 시 UNIQUE upsert로 **중복 INSERT 금지**

---

### Phase 1.5 — Watcher · 자동 실행

폴더 감시, 안정화 대기, (선택) 작업 스케줄러. Phase 1 golden 이후.

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
- 3b 승인→OCR + 최소 UI ✅ — [phase3-import.md](./phase3-import.md) (`staging manual verification pending`, **3a와 함께 검증**)  
**미포함**: similar, watcher, 자동 승인, 카카오 자동 수집, DDL 추가

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
- **auto_migrate=0**: 승인 후 OCR/GPT만 · 이관은 관리자 OCR 검수 후

ingest에만 쌓이고 GPT에 안 쓰는 경로 **금지**.

관리자 UI: `/admin_w/ingest/import` 목록·상세 + 기존 OCR/콘텐츠 검수.

---

### Phase 4 — Similar · 그룹 결정 UI

- Phase 0.5에서 고른 방식으로 signature  
- **N≥2 그룹**: `merge_all` / `separate_all` / `partial` / `deferred`  
- 재결정: `re_merge` / `split` (전송 전=로컬 decision)  
- **시각 리뷰 UI 필수** (Python 로컬 웹 또는 관리자 Next — Phase 4 착수 시 선택)  
- similar 자동 통합 없음  

→ [similar-group-decisions.md](./similar-group-decisions.md)

---

### Phase 5 — 제한 자동 승인

검증된 규칙만; 감사 로그·롤백; similar auto-merge / overwrite 금지 유지.

---

## 6. 나중에 해도 되는 것 (1~3을 막지 않음)

Electron/PySide 완성형 GUI, 트레이, 시작 시 자동 실행, 멀티 PC 동기화,  
service token 완전 자동 전송, similar 자동 통합, CLIP,  
기존 콘텐츠 자동 보완, 대표 이미지 자동 교체, legacy 대량 백필, 운영 대시보드.

---

## 7. 레포 경계

| | kakao-import-local | frontend | backend |
|--|--------------------|----------|---------|
| 0~2, 1.5 | ✅ | — | 0.5만 패키지 |
| 3 | upload CLI | ✅ Import/exact/GPT 연결 | OCR 입력 |
| 4 | 로컬 리뷰 UI(선택) | 관리자 리뷰 UI | signature |

---

## 8. 즉시 다음 액션

1. **Phase 0.5** signature 설치·golden 1회 → 방식 고정  
2. **Phase 1**  
   - `parser` (일자·메시지·`사진`)  
   - `photo_index` (`KakaoTalk_` 시각 + SHA)  
   - `matcher` → **ESENCIA golden 테스트**  
   - 멱등 DDL + report  
3. golden 추가 샘플 2~3건 (사진 N장, 날짜 경계, ambiguous)  
4. Phase 2 이후는 1 통과 후  

---

## 9. 변경 이력

| 버전 | 내용 |
|------|------|
| 1.0 | 초기 Phase 0~5 |
| 1.1 | parser 우선·이력·개인정보·0.5·서버 exact/GPT·similar 그룹 |
| **1.2** | **공용 photos+방별 chats 확정**, **파일명 시각 매칭 주 경로**, ESENCIA golden, Phase 1 완료=해당 테스트 통과, 계획서 재정리 |

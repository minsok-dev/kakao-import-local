# PC 앨범(묶음) 원자 업로드 — 현황·대안

<!-- [변경사유]: 2026-08-26 — 운영 관찰 중. 코드/정책 변경 없음. 이후 진행 여부·방향 결정용 기록 -->
<!-- [변경사유]: 2026-08-28 — 캡션 유실·1장+앨범 사례 논의 반영. A+B 방향·과거 단건 비보정·상한 10 코드 근거 추가. 구현은 아직 없음 -->
<!-- [변경사유]: 2026-08-28 — A+B+매칭/캡션 구현: collapse→similar 순서, stem-only 묶음, stem sibling·슬롯 윈도우 -->
<!-- [변경사유]: 2026-08-28 — §11 non_poster/uncertain·앨범 게이트 미결 문제·합의 정책(미구현) 기록. A+B 단위테스트 62 pass 재확인 -->

> **상태: 채택 (A+B 구현됨 / non_poster 앨범 예외는 미구현)**  
> 2026-08-28: A+B + 매칭/캡션 반영.  
> **§11** 포스터 분류↔앨범 업로드 불일치는 **정책 합의만**, 코드 미반영.  
> 서버 exact+sub·과거 단건 보정은 **미실시**.

관련:

- [phase42-bundle-verification-checklist.md](./phase42-bundle-verification-checklist.md) — 현재 묶음 계약·검증
- [similar-group-decisions.md](./similar-group-decisions.md) — similar ≠ main+sub (현행 문서; 업로드는 앨범 단위 보완)
- 코드: `src/kakao_import/payload.py` (`collapse_grouped_photo_bundles` → `_apply_similar_policy`)
- 매칭: `src/kakao_import/matcher.py` (`_candidate_photos_for_slots`, `_attach_album_stem_siblings`)
- 서버 상한: `frontend/lib/ingest/import/validateKakaoImportImage.ts` (`KAKAO_IMPORT_MAX_BUNDLE_MEMBERS`)

---

## 0. 구현 요약 (2026-08-28)

| 항목 | 내용 |
|------|------|
| 순서 | 후보 생성 → **album stem collapse** → **similar(묶음 단위)** → caption union |
| B | `bundle_candidate` / chat `group_id` 없이 동일 KakaoTalk stem+`_01` 이면 묶음 |
| 캡션 | 멤버 중 최장 `matched_messages` 를 main 에 1회 승격 |
| similar | 멤버 중 deferred 1장이라도 → 묶음 전체 hold; same_content 는 대표가 멤버 안에 있으면 keep |
| 매칭 | 슬롯 시각 구간±tolerance 후보; 배정된 stem 의 미배정 형제 → 같은 `group_key` |
| 상한 | 기존 `MAX_BUNDLE_MEMBERS=10` 유지 (초과=단건 leftovers) |
| 과거 데이터 | 보정 안 함 |

테스트: `tests/test_phase42_bundle_collapse.py`, `test_matcher_album_atomic.py`, `test_album_similar_policy.py`

## 1. 배경·운영에서 본 문제

카카오톡 PC 저장 앨범은 파일명으로 묶인다.

```text
KakaoTalk_YYYYMMDD_HHMMSSSSS.jpg      ← 본파일 (seq=0)
KakaoTalk_YYYYMMDD_HHMMSSSSS_01.jpg
KakaoTalk_YYYYMMDD_HHMMSSSSS_02.jpg
…
```

운영 관찰:

- 앨범 N장이 **각각 단건 OCR/콘텐츠**로 올라가거나,
- similar `same_content` 때문에 **대표 1장만** 올라가고 형제는 skip 되는 경우가 있다.
- 포스터가 아닌 장(안내문·콜라주 등)도 **같은 메시지 정보**이므로, 제품 관점에서는 **한 콘텐츠에 같이** 있어야 한다는 의견이 있음.
- **캡션:** 단건으로 쪼개진 뒤 **한 장만** `group_text`가 붙고 나머지는 빈 캡션인 사례 (매칭 분 버킷 + 장별 caption 경로).

전제 (2026-08-28 방향):

> PC 앨범 스템이 같으면 **무조건 한 콘텐츠(main+sub)** 로 올린다.  
> 캡션은 **묶음(또는 동일 chat group)에 1회** 싣는다.

---

## 2. 현재 동작 (코드·계약 기준)

### 2.1 처리 순서

**현행(구현 후, 2026-08-28):**

```text
업로드 후보 생성
  → collapse_grouped_photo_bundles (album stem, bundle_candidate 불필요)
  → similar policy (묶음 member_photo_ids 단위)
```

**이전(4.2):**

```text
업로드 후보 생성
  → similar policy로 비대표/deferred 제거
  → collapse (남은 것만, bundle_candidate 필요)
```

### 2.2 묶음이 성립하는 조건 (현행)

대략 모두 만족해야 함:

1. similar 필터 **이후** 큐에 **같은 스템 멤버 ≥2**
2. 채팅 `group_candidate.bundle_candidate` (`slot_count >= 2`)
3. 동일 KakaoTalk 시각 스템 + sequence≥1 (`_01` 이상) 1장 이상
4. 멤버 상한 `MAX_BUNDLE_MEMBERS` (**10**) — 초과분은 **단건 잔류** (드롭 아님). §2.5 참고

### 2.3 단건으로 쪼개지는 대표 원인

| 원인 | 결과 |
|------|------|
| similar `same_content` 비대표 skip | 형제 제거 → 묶을 멤버 부족 → 단건 |
| `bundle_candidate` 실패(매칭) | 파일명 앨범이어도 collapse 스킵 |
| `non_poster` → `excluded_from_upload` | 앨범 멤버가 SQL에서 제외 → 쪼개짐 |
| 멤버 **>10** | **10장만** 묶고 나머지 **단건** (§2.5) |
| exact 경로 sub 미첨부 (서버 v1 계약) | main만 처리, sub 유실 가능 |
| 증분: 일부만 먼저 `uploaded` | 고아 단건 |
| 매칭 minute 버킷 (첫 사진 메시지만) | 슬롯·파일 시각이 분 경계면 일부만 assignment/`group_text` → **빈 캡션 단건** |

### 2.4 로그·매니페스트 주의 (조사 시)

- `run-report.json` / 카톡 요약: **건수만**, 파일명·묶음 상세 **없음**
- `last_upload_manifest.json` / `upload-result.json`: upload마다 **덮어쓰기** (이력 없음)
- 원본 파일명 SSOT: 로컬 SQLite `photo.file_name`
- 이후 `kakao-import upload` 단독 실행이 `run-report`를 덮어쓰면, 직전 collect 요약이 사라질 수 있음 (`source: kakao-import-upload`)

앨범 이슈 재현·확인 시: **해당 upload 직후** 매니페스트를 보존하거나 DB `file_name`으로 조회할 것.

### 2.5 묶음 상한 10장 — 코드 근거 (현행)

**로컬·서버 모두 main+sub 합계 최대 10장**으로 맞춰 있다. (2026-08-26에 5→10 상향.)

| 위치 | 상수 | 동작 |
|------|------|------|
| `kakao-import-local` `payload.py` | `MAX_BUNDLE_MEMBERS = 10` | `collapse_grouped_photo_bundles(..., max_members=MAX_BUNDLE_MEMBERS)` |
| `frontend` `validateKakaoImportImage.ts` | `KAKAO_IMPORT_MAX_BUNDLE_MEMBERS = 10` | receive: `rawSubs.length + 1 > 10` 거부 |
| `frontend` `pages/api/admin/ingest/import/kakao.ts` | 동일 상수 | `maxFiles: 10`, 요청당 이미지 >10 거부 |

collapse truncate 로직 (`payload.collapse_grouped_photo_bundles`):

```text
동일 stem 멤버를 album 순서로 정렬
  if len(ordered) > max_members:   # 기본 10
      leftovers ← ordered[10:]     # 업로드 후보에서 버리지 않음 → 단건으로 남김
      ordered   ← ordered[:10]     # 이 10장만 main + sub_images
  → _attach_bundle_to_main(ordered)
     main = ordered[0]
     sub_images = ordered[1:]      # 최대 9개 (합계 10)
```

단위 테스트: `tests/test_phase42_bundle_collapse.py::test_max_members_truncate_overflow_stays_single`  
— 12장 입력 시 묶음 1건(멤버 10) + 단건 2건.

문서: `phase3-import.md` / `phase0-contracts.md` — multipart `file` + `sub_0`…, 최대 10장.

**의미:**

- **10장 이하** 동일 stem + 성립 조건 충족 → **1 request** (예: 본파일+`_01`…`_08` = 9장이면 main 1 + sub 8).
- **11장 이상** 동일 stem → **앞 10장만 묶음**, 나머지 장수는 **각각 단건** (유실 방지용 leftovers). 원자 업로드(A) 채택 시에도 상한을 10에 둘지·초과분까지 한 콘텐츠로 넣을지(대안 D)는 별도 결정.

---

## 3. 제품 관점 요구 (2026-08-28 방향)

1. 동일 PC 앨범 스템 → **항상 1 request (main + sub_images)** (상한 내; §2.5)
2. 포스터가 아닌 장도 **같은 콘텐츠에 포함** — **§11 합의(미구현)**. 현행은 장 단위 `non_poster` 제외로 앨범이 쪼개질 수 있음
3. similar / 부분 uploaded가 **앨범을 쪼개지 않음** (similar 묶음 단위는 A로 반영; non_poster는 §11)
4. **이미 단건으로 올라간 과거 데이터 보정은 하지 않음** (2026-08-28 합의 — 대안 E 비채택)

---

## 4. 대안 목록

### A. similar보다 앨범 원자성 우선 — **방향 채택**

- 묶음 붕괴를 similar **앞**으로 옮기거나,
- similar 적용 시 **같은 album_stem은 전원 keep** (비대표 skip 금지).
- similar/hold/skip은 **묶음 단위**로만 적용.

### B. 파일명만으로 묶기 (채팅 매칭 독립) — **방향 채택**

- `bundle_candidate` 없어도 동일 room + 동일 album_stem + `_01` 이상이면 collapse.
- 매칭 실패해도 앨범은 한 콘텐츠.

### C. 증분·부분 업로드 원자성

- 앨범 멤버가 전부 ready일 때만 전송.
- 한 장이라도 missing/hold면 **묶음 전체 hold**.

### D. 상한·exact

- `MAX_BUNDLE_MEMBERS` 초과분도 같은 콘텐츠 첨부(단건 분리 금지) — **상한 자체는 이미 10**. 초과분 원자화·exact+sub는 별도.
- exact hit 시에도 sub를 기존 content에 붙일지 **서버 계약 변경** 검토.

### E. 이미 쪼개진 데이터 — **비채택**

- 과거 단건 보정 스크립트/수동 병합 **하지 않음** (2026-08-28).

### 참고: 현행(바꾸기 전) 동작

- 앨범에 similar 대표만 올리고 형제 `similar_non_representative` skip
- 앨범 멤버 non_poster 단독 제외

---

## 5. similar / 업로드제외가 묶음에 끼일 때

### 5.1 현행 코드

멤버를 **올리기 전에** 빼서 그룹이 깨진다.

| 상황 | 처리 |
|------|------|
| non_poster | 후보 SQL/`excluded_from_upload`에서 **제외** → 앨범 쪼개짐 |
| similar deferred | 멤버 **전부 미업로드** (`upload_none`) |
| similar same_content | **대표 1장만** → collapse 실패 → 단건 |
| similar different_content | 멤버 유지 → collapse 가능 |
| partial | 서브그룹 대표·solo만 → 앨범 쪼개질 수 있음 |

### 5.2 A 채택 시 권장 규칙 (구현 전 합의안)

| 멤버 상태 | 처리 |
|-----------|------|
| 앨범 안 일부 deferred | **앨범 전체 hold** (장 단위 skip 금지) |
| 앨범 안 same_content (형제끼리) | **앨범 전체 1 OCR** (대표만 올리지 않음) |
| 앨범 안 일부 non_poster | 제품안: **sub로 포함**. 현행 제외와 충돌 → 구현 시 확정 |
| 앨범 밖 similar | 앨범을 한 덩어리로 두고 상대와만 판단 |

「보류면 멤버를 나눠 업로드하고 전원에 전체 텍스트」경로는 **쓰지 않음** (원자성과 반대).

---

## 6. 「1장 + 묶음」OCR 등록 (합의안)

채팅이 `사진` + `사진 N장`이어도, **등록 단위는 PC 파일명 stem**이다.

```text
예) 커버(다른 stem·시각) 1장 + KakaoTalk_…_HHMMSSmmm + _01…_08
```

| OCR/콘텐츠 | 이미지 | 캡션 |
|------------|--------|------|
| A | 커버 단건 1장 | 매칭되면 **동일 `group_text` 공유** |
| B | 앨범 main + sub (상한 내) | **main에 group_text 1회** (sub는 이미지 첨부) |

→ OCR **2건**. 「슬롯 10 = 콘텐츠 1」이 아님.

추가로 구현 시 필요한 매칭/캡션 보완:

1. 연속 `사진`/`사진 N장` 후보를 **첫 메시지 분만이 아니라** 슬롯±tolerance / 파일명 시각으로 배정 → 같은 `group_id`·`group_text` 공유  
2. 캡션은 **장마다 재계산하지 않고** 묶음(또는 group)에 1번  
3. (선택) after-수집이 `msg_kind=file`에서 `break`하는 현행 — Spotify/mp3 **뒤** 문장 포함 여부는 별도 정책

---

## 7. 결정 전에 운영에서 보면 좋은 것

| 관찰 항목 | 보는 곳 |
|-----------|---------|
| 앨범인데 `bundled_groups` 비었는지 | upload 직후 `last_upload_manifest.json` |
| 형제가 `similar_non_representative` skip인지 | 동 매니페스트 `similar_policy.skipped` |
| `bundle_candidate` 없이 파일명만 앨범인지 | match/group + `file_name` |
| non_poster로 멤버 빠졌는지 | poster classify / upload skip 로그 |
| 단건 OCR N건 vs 기대 1건 | 관리자 OCR·콘텐츠 이미지 목록 수 |
| **10장 초과** 앨범 빈도 | photos 폴더 스템별 장수 |

---

## 8. 결정 체크리스트

- [x] **진행 여부:** 한다 (구현됨)
- [x] **채택 대안:** **A + B** (+ §6 매칭/캡션)
- [ ] **앨범 non_poster 예외 (§11):** 미구현 — 합의만
- [ ] **서버 exact + sub 첨부** 계약 변경 여부
- [x] **과거 단건 데이터** 보정: **안 함**
- [x] 문서 본 파일 §0·§2.1·**§11** 갱신
- [x] **단위 테스트 (A+B):** 2026-08-28 재실행 **62 passed**  
  (`test_phase42_bundle_collapse`, `test_matcher_album_atomic`, `test_album_similar_policy`, `test_phase1`, `test_phase3_payload`, `test_phase4_similar`, `test_phase35_upload_ops`)
- [ ] 스테이징: match → upload dry-run 으로 `bundled_groups`·캡션 확인

---

## 9. 운영 메모 (사례)

| 일자 | stem / room | 증상 | 매니페스트·비고 |
|------|-------------|------|-----------------|
| 2026-08-26 | (예) `…_181308200` 등 | 앨범이 단건·similar skip으로 쪼개진 것으로 관측 | 전체방 일부 실패 후 강남 재수집 등과 겹침 |
| 2026-08-26 | `KakaoTalk_20260825_164956120` + `_01`~`_04` | 파일명상 5장 앨범 — 단건 여부 **미확정**(매니페스트 덮어쓰기) | |
| 2026-08-28 | 커버 `…225958479` + 앨범 `…230006025`(+`_01`…`_08`) | `bundled_groups: []`, 단건 다수; **한 장만 캡션**, 나머지 빈 캡션; similar deferred로 일부 `upload_none` | A+B 구현으로 재발 완화 기대. 과거 단건 보정 안 함 |

---

## 10. 한 줄 요약

**구현(2026-08-28):** album stem 을 **similar 앞**에서 묶고(`bundle_candidate` 불필요), 캡션은 main 1회, similar는 묶음 단위 hold/keep. 상한 10·과거 단건 보정 없음.  
**남은 후속:** §11 non_poster 앨범 게이트, exact+sub 서버 계약, 스테이징 dry-run 검증.

---

## 11. 미결 — 앨범 × 포스터 분류 (2026-08-28 논의, **코드 미반영**)

### 11.1 현행 코드 문제

| 항목 | 현행 |
|------|------|
| `uncertain` | 업로드 **됨** (`excluded_from_upload=0`) |
| `non_poster` | 업로드 **안 됨** (`build_upload_items`에서 `poster_skip` continue) |
| 앨범 stem | A+B로 묶이지만, non_poster 장은 **후보에 아예 없음** → **해당 장만 빼고** 나머지끼리 묶음 (앨범 쪼개짐 / 안내문 유실) |
| 상태 | 빠진 장은 `excluded_non_poster` 가능 — **묶음 전체 hold가 아님** |

즉 A+B 원자 업로드와 “non_poster 장 단위 제외”가 **충돌**한다.

### 11.2 합의 정책 (구현 대기)

| 앨범(동일 KakaoTalk stem + `_01`≥1) | 처리 |
|-------------------------------------|------|
| 멤버 중 **`poster` 또는 `uncertain` ≥1** | non_poster 형제도 **같은 묶음에 포함** 업로드 |
| 멤버 **전원 `non_poster`** | **묶음 전체 스킵** (잡사진만 있는 앨범 걸러냄) |
| 단독 non_poster | 현행 유지 — 스킵 |
| `poster_classify` 라벨 | 학습·리뷰용으로 **유지** (업로드 게이트만 예외) |
| main 선정 | `poster`/`uncertain` 우선 (seq=0이 non이어도) |

**비채택:** “첫 장(seq=0)만 보고 통째로 올리기” — 표지·안내가 본파일인 앨범에서 포스터 전체 스킵 위험.

**후순위(별도 기능):** human이 non→poster 재지정 후 **묶음 재업로드** — 증분 `skip_shas` / idempotency / 이미 uploaded 형제와 충돌하므로 게이트와 별도 설계 필요.

### 11.3 구현 시 코드 사이드이펙트 (주의)

게이트만 풀면 생길 수 있는 것:

1. `upload_state` — `excluded_non_poster`와 실제 업로드 불일치 → 장부를 묶음 `uploaded` 등으로 맞춤 필요  
2. idempotency / `media_fingerprint` — sub 구성 변경 → 과거 단건과 **다른 키**(중복 OCR 가능; 과거 보정은 안 함)  
3. main이 non_poster — 서버 대표 썸네일 이상 → main 우선순위 필요  
4. `MAX_BUNDLE_MEMBERS=10` — non 포함으로 포스터가 leftovers 단건 가능  
5. `similar_detect`는 non을 스킵 — 업로드 멤버와 similar_map 구멍 (1차는 similar 미변경 가능)  
6. exact `representative` / `em.excluded_from_upload` — stem 형제 추가 로드 없으면 예외가 실제로 안 탐  
7. exact 제외와 poster 제외는 **별 경로** — 혼동 주의  

### 11.4 단위 테스트 현황

- **A+B·매칭·similar 묶음:** 2026-08-28 재실행 **62 passed** (위 §8 목록).  
- **§11 non_poster 앨범 게이트:** 아직 구현·테스트 **없음**.

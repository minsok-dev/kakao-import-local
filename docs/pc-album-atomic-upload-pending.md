# PC 앨범(묶음) 원자 업로드 — 현황·대안 (미결정)

<!-- [변경사유]: 2026-08-26 — 운영 관찰 중. 코드/정책 변경 없음. 이후 진행 여부·방향 결정용 기록 -->

> **상태: 미결정 (보류)**  
> 이 문서는 **향후 검토·결정용**이다.  
> **구현·배포·계약 변경은 아직 없다.** 추가 운영을 본 뒤  
> 「진행 여부」와 「진행 방향」을 따로 정한다.

관련:

- [phase42-bundle-verification-checklist.md](./phase42-bundle-verification-checklist.md) — 현재 묶음 계약·검증
- [similar-group-decisions.md](./similar-group-decisions.md) — similar ≠ main+sub (현행)
- 코드: `src/kakao_import/payload.py` (`_apply_similar_policy` → `collapse_grouped_photo_bundles`)

---

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

전제 후보(아직 채택 여부 미정):

> PC 앨범 스템이 같으면 **무조건 한 콘텐츠(main+sub)** 로 올린다.

---

## 2. 현재 동작 (코드·계약 기준)

### 2.1 처리 순서

```text
업로드 후보 생성
  → similar policy로 비대표/deferred 제거   ← 여기서 형제 빠질 수 있음
  → collapse_grouped_photo_bundles          ← 남은 것만 묶음
```

체크리스트에도 **「similar ≠ main+sub」** — similar로 걸러진 뒤 **남은** 채팅/파일명 그룹만 묶는다.

### 2.2 묶음이 성립하는 조건 (현행)

대략 모두 만족해야 함:

1. similar 필터 **이후** 큐에 **같은 스템 멤버 ≥2**
2. 채팅 `group_candidate.bundle_candidate` (`slot_count >= 2`)
3. 동일 KakaoTalk 시각 스템 + sequence≥1 (`_01` 이상) 1장 이상
4. 멤버 상한 `MAX_BUNDLE_MEMBERS` (현재 5) — 초과분은 단건 잔류

### 2.3 단건으로 쪼개지는 대표 원인

| 원인 | 결과 |
|------|------|
| similar `same_content` 비대표 skip | 형제 제거 → 묶을 멤버 부족 → 단건 |
| `bundle_candidate` 실패(매칭) | 파일명 앨범이어도 collapse 스킵 |
| `non_poster` → `excluded_from_upload` | 앨범 멤버가 SQL에서 제외 → 쪼개짐 |
| 멤버 >5 | 5장만 묶고 나머지 단건 |
| exact 경로 sub 미첨부 (서버 v1 계약) | main만 처리, sub 유실 가능 |
| 증분: 일부만 먼저 `uploaded` | 고아 단건 |

### 2.4 로그·매니페스트 주의 (조사 시)

- `run-report.json` / 카톡 요약: **건수만**, 파일명·묶음 상세 **없음**
- `last_upload_manifest.json` / `upload-result.json`: upload마다 **덮어쓰기** (이력 없음)
- 원본 파일명 SSOT: 로컬 SQLite `photo.file_name`
- 이후 `kakao-import upload` 단독 실행이 `run-report`를 덮어쓰면, 직전 collect 요약이 사라질 수 있음 (`source: kakao-import-upload`)

앨범 이슈 재현·확인 시: **해당 upload 직후** 매니페스트를 보존하거나 DB `file_name`으로 조회할 것.

---

## 3. 제품 관점 후보 요구 (미채택)

운영 논의를 위해 적어 둔 요구안이다. **아직 확정 아님.**

1. 동일 PC 앨범 스템 → **항상 1 request (main + sub_images)**
2. 포스터가 아닌 장도 **같은 콘텐츠에 포함**
3. similar / non_poster / 부분 uploaded가 **앨범을 쪼개지 않음**
4. (선택) 이미 단건으로 올라간 과거 데이터 보정은 별도

현행 Phase 4.2 계약·체크리스트와 **충돌**하므로, 채택 시 문서·테스트·서버 exact-sub 계약까지 같이 손봐야 한다.

---

## 4. 대안 목록 (결정용)

우선순위는 **제안일 뿐**, 채택 순서도 미정.

### A. similar보다 앨범 원자성 우선 (본경로 후보)

- 묶음 붕괴를 similar **앞**으로 옮기거나,
- similar 적용 시 **같은 album_stem은 전원 keep** (비대표 skip 금지).
- similar/hold/skip은 **묶음 단위**로만 적용.

### B. 파일명만으로 묶기 (채팅 매칭 독립)

- `bundle_candidate` 없어도 동일 room + 동일 album_stem + `_01` 이상이면 collapse.
- 매칭 실패해도 앨범은 한 콘텐츠.

### C. 증분·부분 업로드 원자성

- 앨범 멤버가 전부 ready일 때만 전송.
- 한 장이라도 missing/hold면 **묶음 전체 hold**.

### D. 상한·exact

- `MAX_BUNDLE_MEMBERS` 상향 또는 초과분도 같은 콘텐츠 첨부(단건 분리 금지).
- exact hit 시에도 sub를 기존 content에 붙일지 **서버 계약 변경** 검토.

### E. 이미 쪼개진 데이터

- 관리자 수동 병합, 또는 album_stem 기준 보정 스크립트 (후순위).

### 참고: 적용하지 않는 쪽이 현행

- 앨범에 similar 대표만 올리고 형제 `similar_non_representative` skip 유지
- 앨범 멤버 non_poster 단독 제외 유지

---

## 5. 결정 전에 운영에서 보면 좋은 것

코드 수정 전에 사례를 모으면 방향을 고르기 쉽다.

| 관찰 항목 | 보는 곳 |
|-----------|---------|
| 앨범인데 `bundled_groups` 비었는지 | upload 직후 `last_upload_manifest.json` |
| 형제가 `similar_non_representative` skip인지 | 동 매니페스트 `similar_policy.skipped` |
| `bundle_candidate` 없이 파일명만 앨범인지 | match/group + `file_name` |
| non_poster로 멤버 빠졌는지 | poster classify / upload skip 로그 |
| 단건 OCR N건 vs 기대 1건 | 관리자 OCR·콘텐츠 이미지 목록 수 |
| 5장 초과 앨범 빈도 | photos 폴더 스템별 장수 |

가능하면 재현 stem 예시를 이 문서 §7에 추가한다.

---

## 6. 결정 체크리스트 (나중에 채울 것)

- [ ] **진행 여부:** 한다 / 안 한다 / 일부만
- [ ] **채택 대안:** A / B / C / D / E (복수 가능)
- [ ] **비앨범 similar·non_poster 현행 유지 여부**
- [ ] **서버 exact + sub 첨부** 계약 변경 여부
- [ ] **과거 단건 데이터** 보정 범위
- [ ] 문서 갱신: phase42 체크리스트, similar-group-decisions
- [ ] 구현·테스트·스테이징 검증 일정

결정 후: 이 문서 상단 상태를 `채택` / `기각` / `일부 채택`으로 바꾸고, 구현 PR·이슈에 링크한다.

---

## 7. 운영 메모 (사례 추가란)

| 일자 | stem / room | 증상 | 매니페스트·비고 |
|------|-------------|------|-----------------|
| 2026-08-26 | (예) `…_181308200` 등 | 앨범이 단건·similar skip으로 쪼개진 것으로 관측 | 전체방 일부 실패 후 강남 재수집 등과 겹침. 상세 stem은 운영 DB/매니페스트로 보강 |
| | `KakaoTalk_20260825_164956120` + `_01`~`_04` | 파일명상 5장 앨범 — 단건 여부 **미확정**(당시 매니페스트 덮어쓰기) | OCR에는 콘텐츠 존재 가능. `run-report`만으로는 파일명 확인 불가 |
| | | | |

---

## 8. 한 줄 요약

**지금은 앨범을 similar 이후에만 묶으므로 단건 분리가 난다.**  
「무조건 한 콘텐츠」로 바꾸려면 A(+B) 등 정책 변경이 필요하나, **아직 결정하지 않았고 운영을 더 본 뒤 진행 여부·방향을 정한다.**

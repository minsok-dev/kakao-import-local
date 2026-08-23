# Similar 그룹 결정 모델 (Phase 4, 계약은 Phase 0에 예고)

<!-- [변경사유]: Phase 4 업로드 제어·UI = Phase 3.5·E2E 이후. 탐지-only는 3.5와 병렬 가능 -->
<!-- [변경사유]: merge_all/separate_all → same_content/different_content. decision ≠ upload policy. 자동 병합·삭제 금지 -->

> **착수 조건 (업로드 제어·리뷰 UI):** [phase3-ops-stabilization.md](./phase3-ops-stabilization.md) + E2E 완료 전  
> Phase 4의 **upload policy 적용·시각 리뷰 UI**를 시작하지 않는다.  
> 서버 Similar hold가 서비스 전체 중복 방지 **본경로**이며, 로컬 similar는 **배치 안 정리·운영 보조**다.
>
> **탐지-only (Phase 4.0 스파이크):** signature 공통 + 그룹 탐지 + 로그/목록 + (선택) decision 저장만은  
> **3.5와 병렬 가능**.  
> <!-- [변경사유]: Phase 4.2 반영 — 이제 upload 큐는 decision을 실제로 따른다 -->
> **Phase 4.2부터는 upload/dry-run 이 decision 기반으로 후보를 필터링하고, `deferred` 는 기본 차단한다.**

## 목적

비슷한 이미지(N≥2)를 확인한 뒤, **이미지 similarity ≠ 콘텐츠 동일**임을 전제로:

- **같은 콘텐츠 후보**로 볼지  
- **다른 콘텐츠**로 각각 볼지  
- (또는) **일부만** 같은 콘텐츠로 볼지  
- 미결정으로 **보류**할지  

를 명시한다.  
이어서(별도) **무엇을 업로드 큐에 넣을지**는 upload policy로 표현한다.  
(페어 전용 UI만으로는 부족 — N≥2 그룹 단위)

## 단위: similar_group

```text
similar_group G1 = { A, B, C }

  content decision:     same_content | different_content | partial | deferred
  upload policy:        (decision에서 유도 — 아래 표)
  later (전송 전):      decision 변경만 (원본 파일·멤버 행 불변)
```

## Content decision

사람이 “이 멤버들을 같은 콘텐츠로 볼지”에 대한 결정.  
**서버 SNS merge / DB 자동 병합과 동의어가 아니다.**

| decision | 의미 | UI 라벨 예 |
|----------|------|------------|
| `same_content` | 멤버들을 **하나의 콘텐츠 후보**로 본다 | 같은 콘텐츠 |
| `different_content` | 멤버마다 **다른 콘텐츠**로 본다 | 다른 콘텐츠 |
| `partial` | 서브그룹 혼합 — 일부 same, 일부 단독 | 부분 |
| `deferred` | 미결정 | 보류 |

전송 전 재결정: `same_content` ↔ `different_content` (또는 `partial` 조정).  
원본 파일·로컬 멤버십은 유지한다.

### partial 서브그룹 (Phase 4.1)

<!-- [변경사유]: 혼합 그룹(템플릿 유사·콘텐츠 일부만 동일) 처리 절차·스키마 명시 -->

한 similar 그룹 안에 **같은 콘텐츠 + 다른 콘텐츠**가 섞인 경우:

```text
예) G14 = {228, 229, 231, 232, 233}
  서브그룹 p1: {229, 233}     ← same → 대표 1장
  단독:        {228},{231},{232} ← 각각 업로드 후보
```

| 항목 | 내용 |
|------|------|
| DB | `similar_image_member.subgroup_key` / `is_subgroup_rep` (`005_phase41_partial_subgroup.sql`) |
| 규칙 | 모든 멤버가 정확히 1개 서브그룹에 속함. **size≥2 묶음 ≥1개** 필수 (아니면 `different_content`) |
| 단독 | `subgroup_key = solo-{photo_id}` |
| UI | `similar-review` → **부분** → 멤버 **선택** → **선택 묶기** → **부분 저장** |
| upload (4.2+) | 묶음마다 대표 1장 + 단독은 각자 (`upload_partial`) |

**지금(4.1+):** decision·서브그룹 저장. **Phase 4.2**부터 upload가 decision을 따름.  
**주의:** `similar-detect` 재실행 시 그룹은 재구성되지만, **멤버 집합(fingerprint)이 동일한 non-deferred decision(+partial 서브그룹)은 복원**된다. 멤버가 바뀌면 새 그룹은 `deferred`로 남는다.

실무 가이드:

1. 혼합이면 **부분**으로 묶기 (권장)  
2. 서브그룹 UI를 쓰기 전이거나 애매하면 **다른 콘텐츠**(전원 업로드)로 보수적 처리  
3. 통째 **같은 콘텐츠**는 다른 반 포스터 유실 위험이 있어 혼합 그룹에 쓰지 않음  

### Deprecated alias (문서·구초안만)

| 구 용어 | 현재 |
|---------|------|
| `merge_all` | `same_content` |
| `separate_all` | `different_content` |
| `re_merge` / `split` | 전송 전 decision 재변경 |

신규 설계·구현·payload는 **구 용어를 쓰지 않는다.**

## Upload policy (decision과 분리)

decision이 확정된 뒤, **업로드 큐에 무엇을 넣을지**의 유도 규칙.  
decision 문자열과 upload action 문자열을 문서·로그에서 **섞어 쓰지 않는다.**

| decision | upload policy | 업로드 |
|----------|---------------|--------|
| `same_content` | `upload_representative` | 대표 **1장**만 큐에 포함 |
| `different_content` | `upload_all_members` | 멤버 **전체** 업로드 |
| `partial` | 서브그룹별 위 규칙 적용 | 서브그룹 수만큼 item |
| `deferred` | `upload_none` | 이번 배치 업로드 **제외** |

대표 미지정 시 임시 규칙(시각·용량 등) 가능 — 최종은 리뷰에서 지정.

<!-- [변경사유]: Phase 4.2 정책 확정 — dry-run/실업로드 차단과 결과 노출 -->
### Phase 4.2 현재 동작

1. `build_batch_manifest` 단계에서 similar decision을 읽어 업로드 후보를 먼저 거른다.
2. `same_content` 는 대표 1장만 남기고 나머지는 `similar_non_representative` 로 기록한다.
3. `partial` 는 **서브그룹 대표 + singleton** 만 남기고 나머지는 `similar_partial_non_representative` 로 기록한다.
4. `different_content` 는 전원 유지한다.
5. `deferred` 가 하나라도 남아 있으면:
   - `dry-run`: `blocked=true`, `SIMILAR_DEFERRED_BLOCKED` 로 요약/결과 JSON 기록
   - `upload`: 실제 전송 전에 전체 차단

결과 파일(`upload-result.json`, `last_upload_manifest.json`)에는 아래가 함께 남는다.

- `similar_policy.skipped`
- `similar_policy.deferred_groups`
- `grouped_photo_candidates` (동일 시간대·동일 발신자 묶음 사진의 향후 main+sub 후보 메타)

## 묶음 사진 → main+sub (Phase 4.2+)

<!-- [변경사유]: 2026-08-05 — C+Y 확정 구현. similar와 축 분리 -->
<!-- [변경사유]: 2026-08-15 — `_01` 동일 시각 스템만 묶음. 연속 단독 사진은 단건 -->

채팅 매칭 `image_group` 안에서 **PC 앨범 파일명**(`KakaoTalk_시각.png` + `KakaoTalk_시각_01.png` …)만 묶음 등록한다.  
연속 「사진」 메시지·같은 분만으로는 묶지 않는다. similar `same_content`는 **업로드 대표 1장**이며 main+sub 등록과 무관하다.

| 규칙 | 내용 |
|------|------|
| 시점 | similar policy **이후** 큐에서 같은 `group_id` + 같은 시각 스템 붕괴 |
| 조건 | 스템 멤버 ≥2 **그리고** 한 장이라도 `_01` 이상(sequence≥1) |
| 대표 | 같은 스템 안 `sequence` 최소(본파일=0), 동점이면 slot_index |
| 불일치 Y | 같은 스템 M≥2 → 있는 장만 묶음; `_01` 없는 단독은 단건 |
| 상한 | 멤버 5 (main+4 sub). 초과 스템 멤버는 드롭하지 않고 **단건으로 잔류** |
| payload | `item.sub_images[{sha256,rel_path}]` · multipart `file`+`sub_i` |
| exact | main만 기존 C/D/E/F; 신규 OCR(`ocr_queued`)일 때만 sub 첨부 |

대표 사례(달콩 1:23 사진×3 + 설명)는 matcher 슬롯 묶음 → 업로드 1건(main+subs) + caption은 main `matched_messages`.

## 하지 않음 (명시)

- **자동 삭제 없음** — 디스크 파일·로컬 DB 멤버 행을 삭제하지 않음
- similarity는 **업로드 후보 정리**일 뿐, 서버 Exact / Similar hold / SNS merge를 **대체하지 않음**
- 서버 콘텐츠 행을 로컬 Similar가 합치지 않음

<!-- [변경사유]: same_content/partial 업로드 캡션은 멤버 설명을 union (파일 병합 아님) -->
업로드 본문: `same_content`·`partial` 묶음은 멤버들의 채팅 설명을 **대표 1장 payload에 합쳐** 올린다.
exact SHA(바이트 동일)와 같이 중복 본문은 1회만, 방 사이는 ADD 구분선.

## 전송 전 vs 전송 후

| 시점 | content decision / upload |
|------|---------------------------|
| Import **전** | 로컬 DB decision·upload policy 매핑 변경으로 충분 |
| Import **후** | 서버 ImportItem / OCR / asset 정책 (force-link·리뷰 등) |

## UI 필수 (Phase 4)

- 그룹 목록 → 멤버 썸네일 N장 나란히 (+ 클릭 시 큰 이미지)  
- 액션: 같은 콘텐츠 / 다른 콘텐츠 / **부분(서브그룹)** / 보류  
- 같은 그룹에 대해 decision 재변경 가능 (멱등)  
- **4.1:** decision·서브그룹 저장까지 (로컬 `similar-review`)  
- **4.2+:** upload 건수·대표·excluded 멤버가 decision과 일치하는지 표시·적용  

## Phase 매핑

| 단계 | 내용 | 3.5 관계 |
|------|------|----------|
| Phase 0 | 본 문서·payload에 `group_id`·`decision` 필드 예고 | — |
| Phase 1 | 그룹 테이블 stub 가능, similar 계산 안 함 | — |
| **Phase 4.0** | signature 공통 + 그룹 **탐지** + 로그/목록 + (선택) decision 저장. **upload 동작 변경 없음** | **3.5와 병렬 가능** |
| **Phase 4.1** | 로컬 **썸네일 리뷰 UI** + **partial 서브그룹** — decision만 저장, **upload 미적용** | **3.5와 병렬 가능** |
| **Phase 4.2** | upload policy 적용 (`same_content`/`different_content`/`partial`/`deferred`) + dry-run/result 가시화 | **3.5 + E2E 이후** |
| Phase 4.2+ | grouped-photo main/sub 서버 연동 등 후속 고도화 | 후속 판단 |

## 상태

계약 초안 ✅ (용어: `same_content` / `different_content`, decision≠upload policy).  
<!-- [변경사유]: Phase 4.1 partial 서브그룹 저장·UI -->
**Phase 4.0 (탐지-only):** `similar-detect` / `similar-list` / `similar-decide` + SQLite `004` ✅  
**Phase 4.1 (리뷰 UI + partial):** `kakao-import similar-review` — 썸네일·라이트박스·부분 묶기 + `005` ✅  
decision/서브그룹 저장은 upload 큐·파일 삭제·자동 병합을 수행하지 않음.  
<!-- [변경사유]: Phase 4.2 완료 상태 반영 -->
**Phase 4.2:** upload policy 기반 큐 필터 + `deferred` 기본 차단 + 결과 JSON 노출 ✅  
<!-- [변경사유]: Phase 4.2+ — 채팅 매칭 묶음 main+sub 서버 연동 -->
**Phase 4.2+:** 채팅 매칭 묶음(`image_group`) → 1 OCR main+sub 서버 등록 ✅  
- 트리거: similar 이후 `group_id` 클러스터 ≥2 · similar `same_content`는 대표 1장 유지  
- 불일치: 있는 장만 묶음 · 상한 5장 · exact(SNS/hold) 시 sub 미첨부

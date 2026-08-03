# Similar 그룹 결정 모델 (Phase 4, 계약은 Phase 0에 예고)

<!-- [변경사유]: Phase 4 업로드 제어·UI = Phase 3.5·E2E 이후. 탐지-only는 3.5와 병렬 가능 -->
<!-- [변경사유]: merge_all/separate_all → same_content/different_content. decision ≠ upload policy. 자동 병합·삭제 금지 -->

> **착수 조건 (업로드 제어·리뷰 UI):** [phase3-ops-stabilization.md](./phase3-ops-stabilization.md) + E2E 완료 전  
> Phase 4의 **upload policy 적용·시각 리뷰 UI**를 시작하지 않는다.  
> 서버 Similar hold가 서비스 전체 중복 방지 **본경로**이며, 로컬 similar는 **배치 안 정리·운영 보조**다.
>
> **탐지-only (Phase 4.0 스파이크):** signature 공통 + 그룹 탐지 + 로그/목록 + (선택) decision 저장만은  
> **3.5와 병렬 가능**. 이 단계에서는 **upload 큐 동작을 바꾸지 않는다** (전 멤버 후보 유지 가능).

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
| `same_content` | `upload_representative` | 대표 **1장**만 큐에 포함. members는 `excluded_from_upload` |
| `different_content` | `upload_all_members` | 멤버 **전체** 업로드 |
| `partial` | 서브그룹별 위 규칙 적용 | 서브그룹 수만큼 item |
| `deferred` | `upload_none` | 이번 배치 업로드 **제외** |

대표 미지정 시 임시 규칙(시각·용량 등) 가능 — 최종은 리뷰에서 지정.

## 하지 않음 (명시)

- **자동 병합 없음** — caption / SNS / 서버 콘텐츠를 로컬 Similar가 자동으로 합치지 않음  
- **자동 삭제 없음** — 디스크 파일·로컬 DB 멤버 행을 삭제하지 않음  
- similarity는 **업로드 후보 정리**일 뿐, 서버 Exact / Similar hold / SNS merge를 **대체하지 않음**

## 전송 전 vs 전송 후

| 시점 | content decision / upload |
|------|---------------------------|
| Import **전** | 로컬 DB decision·upload policy 매핑 변경으로 충분 |
| Import **후** | 서버 ImportItem / OCR / asset 정책 (force-link·리뷰 등) |

## UI 필수 (Phase 4 본구현 · 3.5+E2E 이후)

- 그룹 목록 → 멤버 썸네일 N장 나란히  
- 액션: 같은 콘텐츠 / 다른 콘텐츠 / 부분 / 보류  
- 같은 그룹에 대해 decision 재변경 가능 (멱등)  
- upload 건수·대표·excluded 멤버가 decision과 일치하는지 표시

## Phase 매핑

| 단계 | 내용 | 3.5 관계 |
|------|------|----------|
| Phase 0 | 본 문서·payload에 `group_id`·`decision` 필드 예고 | — |
| Phase 1 | 그룹 테이블 stub 가능, similar 계산 안 함 | — |
| **Phase 4.0** | signature 공통 + 그룹 **탐지** + 로그/목록 + (선택) decision 저장. **upload 동작 변경 없음** | **3.5와 병렬 가능** |
| **Phase 4.2+** | upload policy 적용 + 리뷰 UI + (이후) caption 정책 | **3.5 + E2E 이후** |

## 상태

계약 초안 ✅ (용어: `same_content` / `different_content`, decision≠upload policy).  
구현 전 Phase 4 본구현 착수 시 와이어프레임 추가.

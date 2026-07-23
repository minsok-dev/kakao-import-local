# Similar 그룹 결정 모델 (Phase 4, 계약은 Phase 0에 예고)

<!-- [변경사유]: 2장 이상 합침/분리/재결정·업로드 단위 — 리뷰에서 누락된 UX를 문서화 -->

## 목적

비슷한 이미지(N≥2)를 확인한 뒤:

- **각각 따로 등록**할지  
- **하나로 합칠지** (또는 일부만 합칠지)  
- 나중에 **다시 합치거나 / 다시 쪼개서** 업로드할지  

를 명시적으로 다루기 위함. (페어 전용 UI만으로는 부족)

## 단위: similar_group

```text
similar_group G1 = { A, B, C }
  decision:
    merge_all      → upload 1건 (대표 + members)
    separate_all   → upload N건
    partial        → 예: {A,B} merge + {C} alone
  later:
    re_merge / split  → 전송 전이면 로컬 decision만 변경 (원본 파일 불변)
```

## 결정 값

| decision | 의미 | 업로드 |
|----------|------|--------|
| `merge_all` | 그룹을 한 콘텐츠/자산 후보로 | 1 batch item |
| `separate_all` | 멤버마다 독립 | N items |
| `partial` | 서브그룹 혼합 | 서브그룹 수만큼 |
| `deferred` | 미결정 | 업로드 제외 |

## 전송 전 vs 전송 후

| 시점 | 합침↔분리 |
|------|-----------|
| Import **전** | 로컬 DB decision 변경으로 충분 |
| Import **후** | 서버 ImportItem/OCR/asset 정책 따름 (force-link·리뷰 등) — Phase 3/4에서 API로 정의 |

## UI 필수

- 그룹 목록 → 멤버 썸네일 N장 나란히  
- 액션: 합침 / 각각 / 부분 / 보류  
- 재실행: 같은 그룹에 대해 decision 변경 가능 (멱등)

## Phase 매핑

- Phase 0: 본 문서·payload에 `group_id`·`decision` 필드 예고  
- Phase 1: 그룹 테이블은 stub 가능, similar 계산은 안 함  
- Phase 4: signature + UI + decision 저장 + upload 매핑  

## 상태

계약 초안 ✅ — 구현 전 Phase 4 착수 시 와이어프레임 추가.

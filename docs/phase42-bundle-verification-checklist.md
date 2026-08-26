# Phase 4.2+ 묶음(main+sub) — 검증 체크리스트

<!-- [변경사유]: 2026-08-05 — 단위 테스트만 통과. 실데이터·스테이징 E2E 미실시 -->
<!-- [변경사유]: 2026-08-06 — 실데이터 dry-run·실전송·similar 리뷰까지 진행. 현재까지 오류 없음 기록 -->

| 항목 | 내용 |
|------|------|
| 범위 | 채팅 매칭 `image_group` → 1 OCR main+sub |
| 단위 테스트 | 로컬 collapse ✅ · 서버 payload/enqueue/API contract ✅ |
| 실데이터 / 스테이징 | **진행·현재까지 오류 없음** (2026-08-06) |
| 부가 반영 | `_01` 앨범 파싱 · multi_room 캡션 union · ADD 구분선 · 터미널 한글 요약 |

제외(의도적 비범위): 묶음 풀기 UI · 메인 수동 지정 · exact 경로 sub 자동 부착 · similar→main+sub

<!-- [변경사유]: 2026-08-26 — 앨범 원자 업로드는 미결정. 운영 관찰 후 별도 결정 -->
> **보류(미결정):** PC 앨범을 similar/non_poster보다 우선해 **무조건 main+sub**로 올릴지 —  
> [pc-album-atomic-upload-pending.md](./pc-album-atomic-upload-pending.md)  
> (현황·대안만 기록. 코드·계약 변경 없음.)

---

## A. 로컬 dry-run

전제(파일명): PC 앨범 `KakaoTalk_…_01.png` 등은 `photo_name` 파싱 후 `kakao-import run` 재실행 필요.
재파싱 전 `name_parse_ok=0`이면 매칭·묶음에 안 들어감.

- [x] `kakao-import upload --dry-run` (또는 export-payload) 실행
- [x] `last_upload_manifest.json` / `upload-result.json`에 `bundled_groups`, `bundle_collapsed_count` 존재
- [x] 채팅 `사진`×N(N≥2) + 로컬 **본파일+`_01`… 동일 시각 스템** ≥2 → request 1건, `item.sub_images` 길이 = 스템 장수−1
- [x] 접미사 없는 단독 2장(다른 밀리초, 같은 분) → **단건 2개** (오묶음 금지)
- [x] main = 같은 스템에서 `sequence` 최소(본파일)의 `rel_path` / `sha256`
- [x] 슬롯 3 · 같은 스템 파일 2(Y) → 멤버 2로 묶임, 단건으로 쪼개지지 않음 *(해당 케이스 관측·계약 유지)*
- [x] 슬롯 ≥2 · 파일 1 → **단건** (묶음 아님) *(계약·단위 테스트)*
- [x] 멤버 >5 → 5장만 묶음, 초과는 **단건 잔류** + 로그 truncate *(단위 테스트)*
- [x] similar `same_content` 비대표는 스킵되고, **남은** 채팅 그룹만 묶임(similar≠main+sub)
- [x] `deferred` 그룹 있으면 기존처럼 upload 차단 *(리뷰에서 deferred↔same_content 전환 확인)*

## B. 스테이징 실전송 (신규 OCR 경로)

전제: frontend·로컬 도구 배포, `KAKAO_IMPORT_SESSION_COOKIE` / endpoint, Nginx body ≥60m

- [x] 묶음 1건 `--no-dry-run` → 201/200, `next=ocr_queued` *(및 단건·exact 혼합 배치 OK)*
- [x] `tbl_ocrcontent`: `main_poster_url` 1 · `sub_poster_urls` / SHA 정합 *(운영 확인 — 오류 없음)*
- [x] media job: main + sub · OCR/콘텐츠 경로 정상 *(오류 없음)*
- [x] 자동 이관 후 관리자 콘텐츠에 main+sub 포스터 표시 *(오류 없음)*
- [x] 멱등 재전송 시 replay·캡션 보완 경로 동작 *(오류 없음)*
- [x] caption: `matched_messages` 기반 `sns_caption_text` (첨부 마커 미포함) · multi_room ADD 구분선

## C. Exact / hold (sub 미첨부)

- [x] main이 exact → SNS append / hold / idle 시 로그 `bundle_subs_skipped_exact` 또는 동등 next (`sns_appended` / `dup_review` 등) 관측
- [x] 해당 요청으로 content에 sub가 **추가되지 않음** (v1 계약) — 오류 없음

## D. 회귀·부하

- [x] 단건(sub 없음) 업로드 기존과 동일
- [ ] 파일 1장 50MiB 초과 → 로컬/서버 거부 *(실데이터 미관측)*
- [x] 묶음 다장 total이 Nginx 한도에 걸리지 않음
- [x] 업로드 pace(sleep) 유지 · OCR 큐 과부하 없음 *(장당 idle 로그 확인)*

## E. 기록

| 일자 | 환경 | 담당 | 결과 요약 |
|------|------|------|-----------|
| 2026-08-05~06 | 로컬→스테이징 Import | 운영 | dry-run·`--no-dry-run`·similar-detect/review·묶음·exact/SNS·캡션(multi_room) 진행. **현재까지 발견 오류 없음.** 미관측만: 멤버>5 truncate, 50MiB 거부 |

관련: [similar-group-decisions.md](./similar-group-decisions.md) · [phase3-import.md](./phase3-import.md) · [development-plan.md](./development-plan.md) · [phase0-contracts.md](./phase0-contracts.md) §9

# Phase 4.2+ 묶음(main+sub) — 미검증 체크리스트

<!-- [변경사유]: 2026-08-05 — 단위 테스트만 통과. 실데이터·스테이징 E2E 미실시 -->

| 항목 | 내용 |
|------|------|
| 범위 | 채팅 매칭 `image_group` → 1 OCR main+sub |
| 단위 테스트 | 로컬 collapse ✅ · 서버 payload/enqueue/API contract ✅ |
| 실데이터 / 스테이징 | **미검증** (본 체크리스트) |

제외(의도적 비범위): 묶음 풀기 UI · 메인 수동 지정 · exact 경로 sub 자동 부착 · similar→main+sub

---

## A. 로컬 dry-run

- [ ] `kakao-import upload --dry-run` (또는 export-payload) 실행
- [ ] `last_upload_manifest.json` / `upload-result.json`에 `bundled_groups`, `bundle_collapsed_count` 존재
- [ ] 채팅 `사진`×N(N≥2) + 로컬 파일 ≥2 → request 1건, `item.sub_images` 길이 = N−1(또는 있는 장−1)
- [ ] main = `slot_index` 최소 사진의 `rel_path` / `sha256`
- [ ] 슬롯 3 · 파일 2(Y) → 멤버 2로 묶임, 단건으로 쪼개지지 않음
- [ ] 슬롯 ≥2 · 파일 1 → **단건** (묶음 아님)
- [ ] 멤버 >5 → 5장만 유지, 초과는 로그 truncate
- [ ] similar `same_content` 비대표는 스킵되고, **남은** 채팅 그룹만 묶임(similar≠main+sub)
- [ ] `deferred` 그룹 있으면 기존처럼 upload 차단

## B. 스테이징 실전송 (신규 OCR 경로)

전제: frontend·로컬 도구 배포, `KAKAO_IMPORT_SESSION_COOKIE` / endpoint, Nginx body ≥60m

- [ ] 묶음 1건 `--no-dry-run` → 201/200, `next=ocr_queued`
- [ ] `tbl_ocrcontent`: `main_poster_url` 1 · `sub_poster_urls` CSV · `sub_poster_sha256_json` 길이 일치
- [ ] media job: main(`sort_order=0`) + sub(`1..n`) · OCR 링크 후(가능하면) content 승계
- [ ] 자동 이관 후 관리자 콘텐츠에 main+sub 포스터 표시
- [ ] 멱등 재전송(동일 묶음 SHA 집합) → replay, 중복 OCR 없음
- [ ] caption: main `matched_messages` 기반 `sns_caption_text` (첨부 마커 미포함)

## C. Exact / hold (sub 미첨부)

- [ ] main이 exact → SNS append / hold / idle 시 로그 `bundle_subs_skipped_exact`
- [ ] 해당 요청으로 content에 sub가 **추가되지 않음** (v1 계약)

## D. 회귀·부하

- [ ] 단건(sub 없음) 업로드 기존과 동일
- [ ] 파일 1장 50MiB 초과 → 로컬/서버 거부
- [ ] 묶음 다장 total이 Nginx 한도에 걸리지 않음(필요 시 60m 확인)
- [ ] 업로드 pace(sleep) 유지 · OCR 큐 과부하 없음

## E. 기록

| 일자 | 환경 | 담당 | 결과 요약 |
|------|------|------|-----------|
| | 스테이징 | | |

관련: [similar-group-decisions.md](./similar-group-decisions.md) · [phase3-import.md](./phase3-import.md) · [development-plan.md](./development-plan.md)

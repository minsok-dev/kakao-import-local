# 카카오 수집과 네이버 밴드 수집 — 규칙 차이

<!-- [변경사유]: 2026-11-01 — 로컬 운영 경로의 수집·제외·중단 규칙을 대조하고, 서로 옮기면 안 되는 것과 보완 후보를 구분 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-11-01 |
| 카카오 | `kakao-pc-collect` → `kakao-import-local` (`run` → `poster-classify` → `similar-detect` → `upload`) |
| 밴드 | `sns-import-local` → frontend Playwright 어댑터(`naver-band-batch`) → `POST /api/admin/ingest/import/sns` → 서버 `ingest_parse` |
| 상태 | 1·2 보완 반영 (2026-11-01). 3·4는 하지 않음 |

한 줄로 보면, 밴드는 **글 1건에 본문과 사진이 이미 붙어** 있고, 카카오는 **사진 서랍과 대화 텍스트를 받은 뒤 시각으로 붙인다.**

## 1. 규칙 대조

| 규칙 | 카카오 | 네이버 밴드 |
|------|--------|-------------|
| 수집 단위 | 서랍 칸의 이미지 파일. 캡션은 `chats/*.txt`를 나중에 매칭 | 피드 글 1건. `external_post_id` + 본문 + 첨부 사진 |
| 어디 저장하나 | 로컬 `input/raw/<방>/photos`. 확정 비포스터는 `non_posters/` | 서버 `temp_smsimages` + `tbl_ingest_raw`. 로컬에 사진 보관소를 두지 않음 |
| 다시 받지 않는 기준 | 방별 워터마크(파일명 스템). 없으면 `photos`·`non_posters`의 가장 큰 스템. 그 이름도 이미 받은 것으로 봄 | 글 id 커서(최근 2개, `INGEST_BAND_WATERLINE_N`)와 `source_idx + external_post_id` 중복 |
| 처음 얼마나 받나 | 워터마크가 없는 방만 최근 3일 (`FIRST_RUN_LOOKBACK_DAYS`) | 소스의 `recent_days_limit`(달력일, 오늘 포함). 등록일을 못 읽으면 그 글은 자르지 않음 |
| 한 번에 몇 장 | 방별 서랍 칸 수(보통 10~50). 앨범 칸 1개에서 파일이 여러 장 나올 수 있음 | 피드 배치 기본 50글. 글당 사진 **앞에서 10장** (`BAND_MAX_IMAGES_PER_POST`) |
| 글과 사진 | 파일명 시각과 메시지 시각. 방 폴더 밖으로 캡션이 섞이지 않음 | 그 글의 본문이 캡션. 제목이 없으면 본문 첫 줄 |
| 올리지 않는 것 | 확정 `non_poster`, similar 비대표, similar 보류(hold). 사람 리뷰 후 재업로드 | 이미지 없는 글, 영상만 있는 글, 프로필·이모지·스티커·가로 180px 미만 URL. 본문이 없는 사진 글은 올라감 |
| 같은 그림 | 로컬 perceptual hash. 이미 정한 동일/다름/부분은 SHA로 기억. 리뷰에는 새 사진과 기존 대표만 | 같은 글 id만 건너뜀. 바이트가 같은 파일은 서버 exact가 나중에 봄. 다시 압축된 같은 포스터는 새 글이면 다시 raw가 됨 |
| 이미 올린 파일 | 로컬 `uploaded_sha_ledger`. 파일을 지워도 같은 SHA는 다시 올리지 않음 | 글 id가 있으면 `skipped_dup`. 거부 SHA는 서버 `tbl_ingest_reject_sha` (`already_rejected`, OCR 생략) |
| 사람 확인 | `similar-review`, `poster-review`. 스케줄은 UI를 열지 않고 보류만 남김 | 수집 단계 리뷰 없음. 하류는 `ingest_parse` → exact → OCR |
| 이미지 가공 | 로컬은 원본 유지. 최적화는 업로드 후 서버 | 크롤 저장 시 서버 `optimizeIngestImageBuffer` |
| 실패 | 방 단위 중단 사유(워터마크, 이미 보유, 첫 수집 3일) | 글 단위. 상세 실패는 세고 다음 글. 세션 오류면 이후 밴드 소스 skip |

밴드 로컬 수집과 서버 `ingest_crawl`은 **같은 어댑터**다. 로컬은 DB 대신 `raw-exists` API로 중복을 묻고, 업로드만 `import/sns`로 한다.

## 2. 일부러 다른 것

이 차이는 채널 모양 때문이다. 맞추려고 바꾸지 않는다.

- 카카오 캡션 매칭을 밴드 글 id 구조로 바꾸지 않는다. 서랍에는 글 id가 없다.
- 밴드 커서(글 id)를 카카오 파일명 스템 자리에 넣지 않는다. 카카오의 “이미 받음”은 파일명이다.
- 밴드 사진을 카카오처럼 `photos/`·`non_posters/`에 쌓지 않는다. 밴드 원본 보관은 서버 raw다.
- 카카오 채팅방 초기화는 수집 정리 절차가 아니다. 서랍 원본이 사라진다.

## 3. 보완

3번(글당 10장)과 4번(이미 수집한 글은 다시 열지 않음)은 하지 않는다.

1·2는 밴드에만 적용한다. 인스타·카카오 업로드 경로는 그대로다.

| 번호 | 동작 |
|------|------|
| 1 비포스터 | 카카오와 같은 포스터 모델. 작은 스티커는 모델 없이 `non_poster`. 글의 사진이 전부 비포스터면 raw만 남기고 OCR을 만들지 않는다. 모델이 없으면 그 장은 올린다 |
| 2 비슷한 포스터 | 비포스터를 뺀 뒤 남은 장이 전부 이미 등록된 포스터와 비슷하면(기존 similar 거리) OCR 행을 만들지 않는다. 새 포스터가 한 장이라도 있으면 그 장만 올린다. 서명 계산이 실패하면 올린다 |

로컬 수집은 업로드 전에 거른다. 서버에서 직접 넣은 밴드 raw는 `ingest_parse`가 같은 규칙으로 한 번 더 거른다. 걸러진 글은 `external_post_id`가 남아 다음 수집에서 중복으로 끝난다. 캡션은 raw 본문에만 있고, 기존 글에 자동으로 합치지 않는다.

코드: `kakao_import/poster_gate.py`, `frontend/lib/ingest/local/bandCollectGate.ts`, `POST /api/admin/ingest/local/similar`.

### 카카오에 없고, 밴드에는 있는 것

1. **기간이 소스 설정.** 밴드 `recent_days_limit`는 소스마다 있다. 카카오의 3일은 워터마크가 없는 첫 수집에만 코드로 고정이다. 방이 생긴 뒤 워터마크가 있으면 그 이후 파일명만 본다.
2. **영상만 있는 글·UI 이미지.** 밴드는 영상 전용 글, 프로필·이모지·스티커·너무 작은 URL을 버린다. 카카오는 서랍에서 `KakaoTalk_` 이미지만 복사하므로 이 필터가 필요 없다. mp4는 이미 이미지로 보지 않는다.

### 둘 다 해당 없음

- 이미 업로드한 카카오 파일을 용량 때문에 지울 때, DB 장부와 `watermarks.json`은 남긴다. 밴드 글 삭제나 채팅방 초기화로 대신하지 않는다.
- 서버에 이미 올라간 글은 로컬 파일을 지워도 내려가지 않는다.

## 4. 코드 위치

| 규칙 | 위치 |
|------|------|
| 카카오 워터마크·3일·`non_posters` 이름 | `kakao-pc-collect/src/kakao_pc_collect/pipeline.py` |
| 카카오 비포스터 폴더 | `kakao-import-local/docs/poster-classifier-plan.md` §4.0 |
| 카카오 similar SHA 기억 | `kakao-import-local/docs/similar-group-decisions.md` |
| 밴드 배치·중복·날짜 컷 | `frontend/lib/ingest/crawl/adapters/naver-band-batch.ts` |
| 밴드 이미지 10장·UI URL 제외 | `frontend/lib/ingest/crawl/utils.ts` `filterBandPostImageUrls` |
| 밴드 커서 2개 | `frontend/lib/ingest/crawl/runner.ts` `INGEST_BAND_WATERLINE_N` |
| 밴드 로컬 업로드 | `sns-import-local/docs/phase0-contracts.md` |

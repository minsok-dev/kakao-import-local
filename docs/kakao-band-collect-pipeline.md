# 카카오·밴드 수집 처리 (수집 PC 포함)

<!-- [변경사유]: 2026-11-05 — 서버 exact만 설명하면 카카오 동일 파일이 틀린다. 수집 PC 장부 스킵이 먼저다. -->
<!-- [변경사유]: 2026-11-05 — 같은 SHA라도 문장이 새로우면 caption-only 로 보낸다. §4 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-11-05 |
| 카카오 | `kakao-pc-collect` → `kakao-import-local` → `POST` 카카오 import |
| 밴드 | `sns-import-local` → frontend 로컬 크롤 → `POST /api/admin/ingest/import/sns` → `ingest_parse` |
| 이 문서가 바로잡는 설명 | 같은 바이트가 서버에 들어가면 문장을 `sns_caption_text`에 붙인다는 말은 **요청이 서버에 도착한 뒤**만 맞다. 카카오는 문장이 같을 때 수집 PC가 요청을 만들지 않는다. 문장이 새로우면 파일 없이 그 문장만 보낸다 |

한 줄로 보면, 밴드는 **글 1건에 본문과 사진이 이미 붙어** 있고 새 글 id면 서버까지 간다. 카카오는 **사진 서랍과 대화를 받은 뒤 시각으로 붙인다.** 한 번 성공한 SHA는 문장이 같을 때 다시 보내지 않고, 문장이 바뀌면 그 문장만 보낸다.

규칙 차이 표는 `kakao-vs-band-collect-rules.md`. 이 문서는 실제로 어느 프로그램이 막고, 서버에 도착하면 무엇이 일어나는지다.

## 1. 카카오

### 1.1 수집 PC가 파일을 받기까지

`kakao-pc-collect`가 단톡 서랍을 `input/raw/<방>/photos`로 복사한다. 확정 비포스터는 같은 방의 `non_posters/`로 옮긴다. 그 폴더는 다시 스캔하지 않고, 그 파일명은 이미 받은 것으로 본다.

다시 받지 않는 기준은 방별 워터마크(파일명 스템)다. 워터마크가 없는 방만 최근 3일(`FIRST_RUN_LOOKBACK_DAYS`)을 본다. 로컬 이미지를 지워도 `kakao_local.db`와 `watermarks.json`은 남긴다. 채팅방을 초기화하는 정리가 아니다.

`kakao-import-local`이 이어서 한다.

1. 대화 텍스트와 사진 시각을 맞춘다. 사진 메시지 본문은 보통 `사진`이다. 홍보 문장은 그 다음 텍스트 메시지다.
2. 바이트 SHA로 exact 그룹을 만든다. 대표 사진 1장만 업로드 후보가 된다.
3. 포스터 모델이 `poster` / `uncertain` / `non_poster`를 매긴다. `non_poster`는 올리지 않는다.
4. perceptual hash로 similar 그룹을 만든다. 사람 결정 전 기본은 `deferred` → 업로드 정책 `upload_none`. 스케줄 실행은 리뷰 화면을 열지 않고 보류만 남긴다.
5. 앨범(같은 묶음, 슬롯 2장 이상)은 대표 1장 + `sub_images`로 합친 뒤 업로드 목록에 넣는다.

### 1.2 수집 PC가 서버로 보내기 전에 막는 것

업로드 목록을 만들기 전에 `load_incremental_caption_plan`이 `skip_shas`를 채운다.

- `upload_candidate`가 `uploaded`, hold, `excluded_*`, `failed_terminal`이고 `needs_rebuild=0`인 멤버 SHA
- `uploaded_sha_ledger`에서 `rejected=0`인 `source_sha256`

`build_upload_items`는 보류·비포스터·실패 SHA를 캡션 SQL 없이 뺀다. 장부에 있고 `rejected=0`인 SHA는 캡션을 다시 만든다. 장부 `caption_fingerprint`와 같거나 본문이 `사진`뿐이면 뺀다. 홍보 문장이 새로 있으면 앨범으로 합치지 않고, SHA마다 문장만 보내는 항목으로 남긴다. `needs_rebuild`는 이 경로에서 켜지 않는다. 켜면 파일이 다시 올라간다. `needs_rebuild`는 similar 결정, 사람이 포스터를 다시 라벨한 경우, hold 캡션이 낡았다고 표시한 경우에만 켜진다.

목록에 남은 항목도 `sync_upload_candidates`가 한 번 더 거른다. 문장 지문이 장부와 같으면 `uploaded`로 남긴다. 문장 지문만 다르면 `caption_delta`로 전송 대상에 남긴다. 파일을 통째로 다시 보내지는 않는다.

파일 없이 문장만 보내는 경로(`caption_only`)는 `upload.py`에 있다. 조건은 셋이다.

- 그 항목이 이미 업로드 목록에 들어 있을 것
- `sub_images`가 없을 것
- SHA가 장부에 있고 거부가 아닐 것

이미 올린 SHA는 앨범으로 합치지 않고 장마다 한 건이다. 묶음 caption-only는 서버가 `CAPTION_ONLY_REQUIRES_FILE`(409)로 거절한다.

### 1.3 서버에 도착한 뒤

도착한 요청만 아래를 탄다. 최적화한 파일 SHA를 기존 포스터·OCR·미디어와 비교한다.

| 서버가 본 것 | 결과 |
|--------------|------|
| 거부 목록 SHA이고 살아있는 글·OCR이 없음 | `already_rejected`. OCR 없음 |
| 연결된 글이 하나 | 새 OCR 없음. 이번 문장만 그 글의 `sns_caption_text`에 붙이고 `has_new_sns_content=1`. 본문·날짜는 그대로 |
| 연결된 OCR이 하나(아직 글이 아님) | 그 OCR 캡션에만 붙임 |
| 글 또는 OCR이 여러 개 | OCR 목록 `exact 확인`. 비전 OCR 없음. 관리자가 어느 글에 붙일지, 새 OCR로 갈지, 버릴지 고름 |
| 파일만 재사용 가능 | 새 OCR을 만들어 비전으로 넘김 |
| 지문만 비슷하고 바이트가 다름 | 카카오는 `IMAGE_SIMILAR_MODE=enforce`일 때만 `similar 확인`. 기본값 off면 새 OCR |

`caption_only`로 도착하면 파일을 다시 최적화하지 않고, 장부 SHA로 exact만 시도한다. exact가 없으면 `CAPTION_ONLY_NO_EXACT`이고 수집 PC가 파일을 한 번 다시 보낸다. 묶음(`sub_images`) caption-only는 받지 않는다.

매주반복(`is_fixed_schedule=2`)의 날짜 이동은 수집이 아니다. 00:20 `weekly_schedule_roll`이 따로 민다.

### 1.4 2026-11-04 `(전국라틴댄스)` 11:28

수집 실행 `20261104-221920`. 상용 DB는 원인 확인에 필요 없었다.

비담님 사진 두 장(`112849333.png`, `112855472.png`)과 문장 `2026년의 마지막 페이지, SS라틴과 함께!`는 로컬에서 한 묶음으로 붙어 있었다. 두 파일 SHA는 10월 31일 경기라틴 업로드와 같다.

| 사진 | 처음 업로드 | OCR | 당시 캡션 |
|------|-------------|-----|-----------|
| SS라틴 11월 정규강습 | 10월 31일 13:26, request 3310 | 7698 | SS라틴 11월 정규강습 개강 |
| 클럽 카디즈 11월 강습 | 10월 31일 13:25, request 3303 | 7691 | 클럽 카디즈 11월 라틴댄스 강습 |

후보는 `uploaded`, `needs_rebuild=0`, 장부 `rejected=0`이라 11월 4일 HTTP에 없다. 5931·6176에 문장이 안 붙고 새 OCR도 없는 이유는 서버 exact가 거절해서가 아니다. 요청이 없다.

같은 시각 이승우(깜보) 사진은 별개다. `112806181.png`는 10월 23일 사진과 similar `deferred`(그룹 `sg-0339`)라 서버에 안 갔다. `112809546.png`는 SHA가 새 파일이라 OCR 7862로 올라갔고, 캡션은 방 이름과 대화명뿐이다. 연말 문장은 그 사진 묶음에 없다.

## 2. 밴드

밴드 로컬 수집과 서버 `ingest_crawl`은 같은 어댑터다. 로컬은 DB 대신 `raw-exists`로 중복을 묻고, 업로드는 `import/sns`다. 수집 PC에 카카오식 SHA 장부는 없다.

### 2.1 수집 PC

- 단위는 피드 글 1건. `external_post_id` + 본문 + 사진.
- 멈추는 기준은 글 id 커서(최근 2개, `INGEST_BAND_WATERLINE_N`)와 `source_idx + external_post_id` 중복. 중복으로 `duplicate`가 오면 그 배치는 멈춘다.
- 기간은 소스의 `recent_days_limit`. 피드 배치는 기본 50글. 글당 사진은 앞에서 10장.
- 글자만 있는 글, 영상만 있는 글, 프로필·이모지·스티커·가로 180px 미만 URL은 버린다.
- 비포스터 판정은 카카오 포스터 모델을 붙인 프로세스다. 글의 사진이 전부 `non_poster`면 빈 이미지로 올리고 서버가 raw만 남긴다(`band_gated`). 한 장이라도 포스터·불확실·판정 불가면 그 글은 올린다.
- 지문 거리로 글을 버리지 않는다. 2026-11-04에 그 제외를 뺐다. raw 3381(`61527284:12058`)은 그 이전 규칙으로 사진 없이 `reject_skip:band_gate:similar`가 됐다. 코드를 양쪽(수집 PC frontend, 상용 frontend)에 반영한 뒤 그 raw를 지우고 같은 글을 다시 받아야 한다.

### 2.2 서버

새 글 id면 사진이 서버에 저장되고 `ingest_parse`가 돈다.

1. 거부 목록. 사진이 1장이면 막힌 SHA는 글 전체를 건너뛴다. 2장 이상이고 하나라도 안 막히면 막힌 장까지 포함해 올린다. 전부 막히면 건너뛴다.
2. 전부 비포스터면 OCR 없이 raw만 남긴다.
3. SHA가 같으면 카카오 서버와 같은 exact다. 글이 하나면 `sns_caption_text`만 붙인다. 여러 글이면 `exact 확인`. 파일만 있으면 새 OCR.
4. 지문만 비슷하면 밴드는 전역 `IMAGE_SIMILAR_MODE`가 off여도 `similar 확인`으로 간다. 비전은 보내지 않는다. 판정이 실패하면 그 글은 새 OCR로 남긴다(fail-open). 카카오·인스타의 similar는 전역 모드를 그대로 따른다.

이미 받은 글 id는 다음 수집에서 다시 열지 않는다. 그래서 한 번 비포스터·거부로 기록된 글은 코드를 고쳐도 raw를 지우기 전에는 재수집되지 않는다.

## 3. 세 가지를 섞지 말 것

| | 바이트가 같음 (SHA) | 지문만 비슷함 | 관리자가 지운 거부 SHA |
|--|---------------------|----------------|------------------------|
| 카카오 수집 PC | 문장이 같으면 HTTP 없음. 문장이 새로우면 파일 없이 그 문장만 보냄 (`caption_delta`) | 결정 전이면 `similar_deferred`로 보류. 대표만 올리거나 멤버를 올리는 것은 사람 결정 후 | 서버가 `already_rejected`를 주면 장부 `rejected=1`. 이 예외로 다시 열지 않음 |
| 카카오 서버 | 요청이 있을 때만 exact → 문장 붙임 / exact 확인 / 새 OCR | 기본 off면 새 OCR. enforce면 similar 확인 | 살아있는 참조가 없을 때만 차단 |
| 밴드 수집 PC | SHA로 막지 않음. 새 글 id면 업로드 | 거리로 버리지 않음 | 글 단위로 서버 응답을 따름 |
| 밴드 서버 | exact로 문장을 붙이거나 확인으로 올림 | 항상 similar 확인 | 1장이면 글 스킵. 여러 장 중 일부만 막히면 전부 업로드 |

거부 목록과 similar 보류와 exact 문장 붙이기는 서로 다른 저장소다. 밴드 거리 10은 서버 `assessSimilarCandidates`다. 카카오 로컬 similar 기억(`similar_sha_memory`)과는 별개다.

## 4. 2026-11-05 수정 — 카카오 동일 파일의 새 문장

11월 4일 `(전국라틴댄스)` 11:28 문장 `2026년의 마지막 페이지, SS라틴과 함께!`가 5931·6176에 붙지 않았다. 서버가 거절한 것이 아니다. 수집 PC가 10월 31일에 올린 같은 SHA를 업로드 목록에 넣지 않아 HTTP 요청이 없었다.

그때까지 코드는 장부 SHA를 캡션이 바뀌어도 `uploaded`로 유지했다. 서버의 exact·caption-only는 요청이 와야 문장을 `sns_caption_text`에 붙인다. 그 요청을 수집 PC가 만들게 바꿨다.

### 바꾼 뒤의 동작

| 조건 | 동작 |
|------|------|
| 장부 문장 지문과 같음, 또는 본문이 `사진`뿐 | 지금처럼 보내지 않음 |
| 장부에 있고 `rejected=0`, 홍보 문장이 새로움 | 파일 없이 그 문장만 보냄. 후보 이유는 `caption_delta` |
| similar 보류, 비포스터, 실패, `rejected=1` | 열지 않음 |
| 밴드 | 수정 없음. 새 글 id는 원래 서버 exact를 탐 |

사진이 두 장이면 앨범(`sub_images`)으로 합치지 않는다. SHA마다 caption-only 한 건이다. 묶어서 보내면 서버가 `CAPTION_ONLY_REQUIRES_FILE`(409)을 주고, 수집 PC가 파일을 다시 올린다. 11월 4일 SS라틴·카디즈는 SHA가 서로 다르므로 각 한 건이다. 다음 수집에서 그 문장이 두 글에 붙는다.

서버 답은 그대로다. 글이 하나면 `sns_caption_text`만 갱신하고 `has_new_sns_content=1`이다. 본문 `content_text`와 날짜는 바꾸지 않는다. 글이 여러 개면 `exact 확인`. exact를 못 찾으면 409 뒤에 파일을 한 번 보낸다. 성공하면 장부 `caption_fingerprint`가 바뀌어 같은 문장은 다음 실행에서 다시 보내지 않는다. 매주반복 날짜 롤과 새 콘텐츠 생성은 이 수정에 없다.

### 고친 파일

| 파일 | 내용 |
|------|------|
| `src/kakao_import/payload.py` `build_upload_items` | 장부 SHA는 캡션을 다시 만든다. 지문이 다르면 앨범에서 빼고 `caption_only` 항목으로 남긴다 |
| `src/kakao_import/upload_state.py` `load_incremental_caption_plan` | 보류·비포스터·실패는 `hard_skip_shas`. 거부되지 않은 장부는 `ledger_caption_by_sha`에 문장 지문을 남긴다 |
| `src/kakao_import/upload_state.py` `sync_upload_candidates` | 문장 지문만 다르면 `uploaded`로 덮지 않는다. 이유는 `caption_delta` |
| `src/kakao_import/upload.py` | 수정 없음. 목록에 들어온 한 장짜리 장부 SHA는 원래 파일 없이 보낸다 |
| `tests/test_upload_state.py` | 문장 지문이 달라진 장부 미디어는 전송 대상이 되는지로 바꿈 |

`needs_rebuild=1`을 이미 올린 후보 전체에 켜지 않았다. 그러면 파일이 다시 올라간다.

## 5. 코드

| 단계 | 위치 |
|------|------|
| 카카오 워터마크·`non_posters` 이름 | `kakao-pc-collect` `pipeline.py` |
| 캡션 조립·앨범 제외·`caption_only` 항목 | `kakao-import-local` `payload.py` `build_upload_items` |
| 장부 문장 지문·`caption_delta` | `kakao-import-local` `upload_state.py` |
| caption-only 전송 | `kakao-import-local` `upload.py` |
| 장부 기록 | `kakao-import-local` `ledger.py` |
| 카카오 수신 exact / caption-only | `frontend` `lib/ingest/import/receiveKakaoImport.ts` |
| exact를 글·OCR·보류로 나누기 | `frontend` `lib/ingest/exact/classifyExactTargets.ts` |
| 밴드 비포스터만 제외 | `frontend` `lib/ingest/local/bandCollectGate.ts` |
| 밴드 서버 파싱 | `frontend` `lib/ingest/parse/runIngestParseBatch.ts` |
| 밴드 거부 장수 규칙 | `frontend` `lib/ingest/import/decideSnsRejectUpload.ts` |

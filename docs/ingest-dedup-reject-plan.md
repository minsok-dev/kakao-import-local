# 카카오 Import — SHA 재사용 · similar_hold 자산화 · 거부 목록

<!-- [변경사유]: 2026-08-19 — 거부 기본 넣기·컨텐츠 동일 선택 확정. 관리 화면·묶음 장별 UI는 이번 파이프라인 이후 -->

| 항목 | 내용 |
|------|------|
| 문서 버전 | **0.2** |
| 기준일 | 2026-08-19 |
| 상태 | **1차 코드 완료. 거부 목록 DDL 018 은 스테이징 적용 필요. 관리 화면은 이후** |
| 레포 | `kakao-import-local` · `frontend` (`lib/ingest`, `lib/ocr`, `lib/media`) · DDL은 `frontend/docs/sql/media-asset-dedupe/` |
| 관련 | [development-plan.md](./development-plan.md) · [phase3-import.md](./phase3-import.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [phase0-contracts.md](./phase0-contracts.md) · `frontend/docs/sql/media-asset-dedupe/signature-and-dup-review-policy.md` · `frontend/docs/image-exact-sns-merge-policy.md` |

서버측 짧은 안내: `frontend/docs/kakao-ingest-dedup-reject-plan.md`

---

## 0. 문서 상태

- 지금까지 대화에서 확정·기각·보류한 내용을 **빠짐없이** 적는다.
- 구현 시 기존 동작·UI는 요청 범위 외로 바꾸지 않는다. 타입은 공통 파일, 로그는 로거, 쿼리는 로거 출력.
- **이번 개발:** §10 1차 A~F 최소(삭제 확인의 거부 체크박스 + ingest 차단). 사용자가 진행을 명시한 뒤 착수.
- **이번 이후(서버 별도):** 거부 목록 조회·해제 전용 화면. 묶음 장별 SHA 수동 선택 UI는 없음(서버가 첨부 SHA 전부 자동).

---

## 1. 한 줄 목표

같은 카카오 파일이 매일 다시 올라와도 **OCR이 무한 생성되지 않게** 하고, 관리자가 OCR/컨텐츠를 지울 때는 **거부 목록에 넣을지 선택**해서, 넣은 SHA는 다시 안 들어오고 안 넣은 SHA는 나중에 다시 등록할 수 있게 한다.

Similar 사람 확인은 유지한다. 막을 것은 **같은 파일을 또 사람 확인·또 OCR INSERT**하는 것, 그리고 **필요 없다고 지운 파일이 재수집으로 되살아나는 것**이다.

---

## 2. 배경 — 실제로 겪은 일

### 2.1 주간 포스터가 한 장이 아닌 이유

- 단톡 주간 공지(예: 강남턴)는 **긴 글 → 사진 여러 장(각각 다른 시각의 단독 사진 메시지) → 안내 글** 형태가 많다.
- 로컬 collapse는 **카카오톡 `_01` 동일 타임스탬프 앨범만** 묶는다. ms가 다른 연속 단독 사진은 **오묶음 방지**로 묶지 않는다.
- 실제 앨범(`093527319` + `_01`)이 21:45 단독 장들과 **바이트 동일**이면 exact SHA로 앨범 파일이 제외되고 단독 장만 업로드된다.
- 로컬 payload에는 캡션이 실려도, 서버는 1건 `ocr_queued` + 나머지는 `dup_review`(similar_hold). `exact.found=false`, `asset_idx=null` 인 경우가 있었다.
- 캡션이 “한 장에만 붙은 것처럼” 보인 이유: 파일이 안 올라간 게 아니라 **similar_hold라 게시되지 않은 것**.

### 2.2 캡션 union (이 계획 범위 밖, 이미 작업됨)

- `caption_sep.py`: `[단톡방: {title}]`, 같은 방 전후 텍스트는 `CHAT_SPLIT_SEPARATOR`.
- similar `same_content` / `partial` 대표에 멤버 캡션 union.
- **다음 `kakao-import upload`부터** 적용. 이미 올라간 서버 행은 자동 수정 안 함.

### 2.3 수집·재실행 (현행, 유지)

- PC collect는 증분. 워터마크 이전 그리드는 다시 안 받는다. **리셋하려고 로컬 사진을 지우지 말 것.**
- `kakao-import run`은 `photos/` **전체** 재스캔.
- `similar-detect`는 그룹을 **다시 만들고** 로컬 결정은 **`deferred`로 리셋**. collect 자동 경로는 `run` + `poster-classify` + `similar-detect`이고 **upload는 안 함**.
- 업로드 큐는 “새 파일만”이 아니라 exact 대표를 매번 태운다. 같은 SHA는 원래 caption-only(`uploaded_sha_ledger`)여야 하는데, 로그에 **`uploaded_sha_ledger missing — run init_schema`** 가 실제로 났다.
- prune은 `photo_file`만 지운다. **장부(SHA 키)는 남는다.** 서버 컨텐츠도 안 지워진다.

### 2.4 핵심 버그 (이번 계획의 원인)

1. **similar_hold가 asset/exact signature를 안 남김**  
   다음 실행에서도 exact를 못 찾고 다시 similar → 또 OCR INSERT.
2. **멱등키에 `photo_id`가 들어 있음** (`payload.py`: `photo:{photo_id}:{sha[:16]}`).  
   로컬 DB 재생성·경로 변경·재수집이면 같은 파일이 새 요청이 된다.
3. **로컬 장부 테이블 누락**이면 caption-only가 동작하지 않아 **파일+요청이 매일 다시** 나간다.
4. 사람이 similar에서 “같은 콘텐츠”로 합쳐도, 카카오 SHA가 **자기 asset으로 안 남으면** 다음엔 또 similar.

정책 충돌: `signature-and-dup-review-policy.md`는 현재 **similar 판정 중 asset/signature INSERT 금지**. 이번 계획은 카카오 ingest의 **hold 확정 경로에서는 판정 전/중에 asset을 영속**하도록 이 정책을 **의도적으로 바꾼다.** (비교 단계 메모리 signature는 유지 가능. 금지하는 것은 “hold OCR만 만들고 asset 없음”.)

---

## 3. 문제 재정의

| 아님 | 맞음 |
|------|------|
| Similar 판정 자체를 끈다 | Similar는 유지 |
| 로컬 사진을 지워 재업로드를 막는다 | 파일 삭제는 해결책이 아님 |
| 같은 그림이 세상에 두 번 쓰이면 안 된다 | 강사 프로필·장소 안내 등은 **여러 강의에 같은 그림**이 정당함 |
| 삭제한 OCR은 항상 다시 등록되어야 한다 | 사용자가 **필요 없어서** 지운 것은 다시 오면 안 됨. 다시 쓸 수도 있으니 **삭제 시 거부 목록 여부 선택** |

막을 것:

- 같은 바이트가 로컬 재실행마다 **새 OCR**이 되는 것
- 같은 바이트가 매번 **새 similar_hold**가 되어 사람이 또 확인하는 것
- 거부 목록에 넣은 SHA가 재수집으로 **되살아나는 것**

---

## 4. 외부 검토 3건 — 채택 / 수정 / 기각

기술 리뷰 3건(컨텐츠 주소 중복제거, 멱등성, ingestion receipt 계층)을 이 코드베이스 기준으로 가렸다.

### 4.1 채택 (해야 함)

- 로컬 장부 스킵(최적화) + 서버 SHA/asset 안전망 + similar_hold 자산화.
- 로컬 장부는 최적화, **서버가 정합성의 최후 보루**. 장부가 깨져도(`uploaded_sha_ledger missing`) 서버가 막아야 함.
- 멱등키에서 **불안정한 `photo_id` 제거**.
- **전역 `UNIQUE(source_sha256)` / “SHA당 OCR 1행”은 너무 강함** → **같은 미디어 그룹의 활성 OCR 재사용**.
- 멱등키를 `source_sha256`만으로 두면 **캡션 변경이 재시도로 무시**됨 → **미디어 지문 + 캡션 지문 + operation version**.
- OCR 중복 방지와 요청 멱등성은 **다른 계층**.
- 캡션은 기존 대표문을 **무조건 덮어쓰지 않음**. 기존 `sns_appended` 재사용. 비어 있을 때만 대표 채움.
- similar 전에 asset 영속. `dup_review` asset이 삭제 큐에 안 들어가게 **OCR↔media 참조** 유지.
- 원본 SHA와 최적화(canonical) SHA 구분. exact는 asset 조회로.
- `createOcr` 앞 SELECT만으로는 동시 요청에 두 행이 생김. 서버 재사용 넣을 때 **DB UNIQUE 또는 동등한 원자적 처리**를 같이 (한 PC 5초 간격이라 급하지는 않음).
- 성공을 HTTP 200만으로 기록하지 않음. 서버가 `ocr_idx` / `request_idx` / result를 주고 장부에 저장.
- 캡션 해시 전: CRLF 통일·앞뒤/행끝 공백·과도한 빈 줄만. **날짜·가격·장소·강사·해시태그·연락처·문장 순서는 지우지 않음.**

### 4.2 수정해서 채택

- “1번(로컬)만 해도 거의 멈춘다” vs “2번(서버)이 더 급하다”: **역할이 다름. 1차에서 같이.**  
  로컬 스킵 = 매일 전송량. 서버 asset-first+재사용 = OCR 적재 **버그 수정**.
- 묶음 지문: 지금은 멤버 SHA **정렬 집합**. 포스터는 표지가 의미 있음 → **2차에서 main+순서**. 1차 핵심 아님.
- 로컬 “성공이면 HTTP 영구 스킵”은 위험 → **파일 없는 caption-only(또는 receipt 조회)** 로 서버가 살아 있는지/거부인지 말하게 한다. 일상은 파일이 안 나감.
- 새 `ingestion_receipt` 테이블은 과함. 있는 **`tbl_ingest_import_request` 확장**.
- “SHA당 OCR 1행” 문구는 폐기. 원칙: **같은 미디어(앨범)에 대해 활성 hold/작업 OCR을 반복 생성하지 않는다.**

### 4.3 하면 좋지만 1차 아님 (2차)

- 앨범 `media_group_fingerprint`에 대표(main)+순서 포함.
- 캡션 정규화(공백/줄바꿈만).
- 동시 INSERT용 UNIQUE (재사용 넣을 때 같이 넣는 정도는 1차에 포함 가능).
- `kakao-import ledger-reconcile` — DB 복원 후. 매일 전수 확인 아님.
- 멀티PC용 pre-flight SHA 목록 API — 서버 안전망 생긴 뒤 옵션.

### 4.4 하지 않음 (1차·기본 경로)

- hold 상태 웹훅 / 삭제 순간 로컬 SQLite 직접 수정 / 업로드 시작마다 전수 폴링 API를 **기본 경로**로.
- 출처별 캡션 전용 테이블 (최소는 `sns_appended`).
- 일상 업로드마다 서버 전수 재검증.
- 로컬 파일을 지워 중복을 푸는 운영.
- Similar 검사 끄기.
- 멱등키를 SHA만으로 고정.
- 전역 UNIQUE(source_sha256).

대안 A(pre-flight SHA check)는 기본 경로 아님.  
대안 B(응답의 `ocr_idx`를 로컬 장부에 저장)는 **1차에 포함**.

---

## 5. 확정 계층 (4층 + 거부 목록)

새 계층을 잔뜩 만들지 않는다. 역할만 나눈다.

```text
로컬 ledger          같은 미디어+같은 캡션이면 파일 생략 (caption-only)
                     서버 식별자·거부 캐시 보관
ingest request       동일 논리 요청 재실행 방지 (멱등키, photo_id 없음)
asset / signature    동일 파일·최적화본 식별. similar 전에 저장
활성 OCR 가드        같은 미디어의 미결정/활성 OCR 재사용 (전역 SHA당 1행 아님)
similar review       진짜 같은 콘텐츠인지는 사람
sns_appended         캡션 변경·출처 보존 (덮어쓰기 금지)
거부 목록(서버 SSOT)  삭제 시 선택. 넣은 SHA는 재ingest 시 OCR 생성 금지
```

로컬 거부장부는 서버가 SQLite를 **직접 수정하지 않는다.** 서버 거부 목록이 원본이고, 로컬은 다음 업로드(caption-only 응답)로 **따라가는 캐시**.

---

## 6. 로컬 장부

### 6.1 현행

- 테이블 `uploaded_sha_ledger`: `source_sha256` PK, `request_idx`, `next`, `final_sha_prefix`, `uploaded_at`.
- 장부에 있고 묶음이 아니면 **caption-only** (HTTP는 함, 파일 생략).
- 테이블 없으면 경고 후 파일 포함 업로드.
- prune은 이 테이블을 안 지움.

### 6.2 1차 변경

스키마 보정(`init_schema`로 테이블 보장) + 필드 확장:

```text
media_fingerprint     단일 = source_sha256
                      앨범 = 1차는 현행처럼 정렬 멤버 SHA (2차: main+순서)
caption_fingerprint   SHA256(정규화 캡션) — 1차는 원문 해시, 2차 정규화
request_idx / ocr_idx / asset_ids / result_type
uploaded_at
rejected              서버 거부 목록 캐시 (선택)
rejected_at
```

동작:

```text
동일 미디어 + 동일 캡션 + 거부 아님
  → 파일 없이 caption-only (완전 HTTP 스킵 아님)

동일 미디어 + 캡션 변경
  → 캡션 변경 요청 (파일 생략 가능, exact/컨텐츠 있을 때)

새 미디어
  → 파일 포함

서버: 없음 / 거부 / exact / hold 재사용
  → 장부 갱신. caption-only가 “파일 다시”면 장부 forget 후 파일 재전송
     단, 거부 목록에 있으면 파일도 OCR도 안 함
```

성공 기록 금지: 타임아웃, 파싱 실패, 5xx, 처리 중, 롤백, 일부만 저장, 응답 fingerprint 불일치.

---

## 7. 서버 ingest

### 7.1 멱등키

현행 단일: `sha256(client_id|sha256|photo:{photo_id}:{sha[:16]})`  
현행 묶음: `sha256(client_id|bundle|sorted shas)` — `client_id` 의존.

목표 (단일):

```text
kakao:upload:v1:{media_fingerprint}:{caption_fingerprint}
```

`photo_id`·로컬 경로·(가능하면) `client_instance_id`를 키에서 뺀다. 같은 그림+같은 캡션은 PC/DB가 바뀌어도 같은 요청.

캡션이 바뀌면 **다른 멱등키** → 캡션 반영 요청. OCR INSERT는 이 키로 막지 않고 **asset/활성 OCR 가드**로 막는다.

### 7.2 asset-first (similar_hold 구멍 수정)

순서:

```text
파일 수신
  → source SHA 검증
  → asset 생성 또는 재사용 (source SHA + optimized SHA signature alias)
  → OCR-media 연결
  → exact / similar 판정
  → 신규 OCR 또는 기존 활성 OCR/컨텐츠 재사용 또는 similar_hold
```

핵심: **similar 판정 전에 asset 영속.**  
트랜잭션으로 asset + signature + OCR(또는 재사용) + media 링크 + ingest request를 묶고, 실패 시 함께 롤백.  
`similar_hold` OCR도 `tbl_ocrcontent_media`로 asset을 가리켜 삭제 워커가 참조 0으로 보지 않게 한다.

### 7.3 활성 OCR 재사용 (SHA당 1행 아님)

```text
같은 미디어 그룹에 활성 dup_review / 미이관 OCR이 있으면
  → INSERT 금지, 기존 행·hold 유지, 캡션만 정책대로 추가

컨텐츠에 같은 SHA가 있으면
  → exact SNS 병합 (현행 sns_appended)

활성 OCR도 컨텐츠도 없고 거부 목록에도 없으면
  → 신규 OCR (similar이면 hold)

거부 목록에 있으면
  → OCR 생성 금지, already_rejected
```

같은 그림이 **다른 강의**에 쓰이는 것은 전역 UNIQUE로 막지 않는다.

### 7.4 캡션

- 동일 캡션 지문: 아무 작업 없음.
- 다른 캡션: `sns_appended` / 이력 추가.
- 기존 설명이 있을 때 **덮어쓰기 금지**.
- 기존이 비어 있으면 대표로 채움.

---

## 8. 거부 목록 (서버 SSOT)

사용자가 원한 것: **삭제가 곧 영구 거부가 아니다.** 지울 때 목록에 넣을지 고르고, 나중에 목록에서 빼면 다시 올릴 수 있다.

### 8.1 누가 원본인가

- **서버 테이블**이 원본 (가칭 `tbl_ingest_reject_sha` 또는 media-asset-dedupe `018`).
- 로컬은 캐시. 서버가 운영 PC SQLite를 직접 수정하지 않음.
- OCR 삭제 직후 로컬이 꺼져 있어도 서버가 막는다. 다음 caption-only/업로드 응답으로 로컬 캐시 갱신.

### 8.2 삭제 UI (이번 개발 · 확정)

OCR 삭제와 **컨텐츠 삭제에 같은 선택**을 둔다.

```text
[✓] 이 이미지 재업로드를 거부 목록에 넣기
    (같은 파일은 다시 와도 OCR/컨텐츠로 안 돌아옴)
```

- **기본값: 체크(넣기).** 끄면 다음에 다시 등록 가능.
- 이관된 OCR만 지우고 **컨텐츠가 남는 경우(시나리오 2):** 체크를 **끄고 비활성**. 안내: “컨텐츠에 이미지가 남아 있어 거부 목록에 넣지 않습니다.” SHA는 목록에 안 들어감 → 이후 exact 연결.
- 일괄 삭제는 확인 한 번에 같은 기본값(넣기). 건마다 다른 선택은 이번 없음.

### 8.3 거부 목록 화면 위치 — 두 층 (자세히)

거부 “화면”은 하나가 아니다. **지울 때 넣는 곳**과 **나중에 빼는 곳**이 다르다.

#### (1) 이번 개발: 새 메뉴 없음. 기존 삭제 확인에 붙인다

지금 삭제는 `confirm()` / ConfirmModal 뿐이다.

| 화면 | 경로 | 지금 | 이번 추가 |
|------|------|------|-----------|
| OCR 목록 일괄 삭제 | `/admin_w/ocr_data` | `선택한 OCR N건을 삭제할까요?` | 체크박스 기본 켜짐 |
| OCR 상세 삭제 | `/admin_w/ocr_data/[id]` | `이 OCR 데이터를 정말 삭제…` | 동일 |
| 강의 목록 삭제 | `/admin_w/contents` | `정말 삭제하시겠습니까?` | 동일 (컨텐츠 삭제도 같은 선택) |
| 강의 상세 삭제 | `/admin_w/contents/[id]` | ConfirmModal 2단 | 동일 |

이유: 거부 여부를 고르는 순간은 **지울 때**다. 별도 페이지로 가면 깜빡하고 기본 넣기를 못 고른다. 매일 지우는 동선(OCR 목록)에 있어야 한다.

#### (2) 이번 개발 이후: 조회·해제 전용 화면 (서버 후속)

기본이 “넣기”이면, 나중에 그 포스터가 다시 필요할 때 **목록에서 빼는 화면**이 필요하다. 다만 목록 검색·이력·권한은 ingest 파이프라인과 별 작업이라 **이 개발이 끝난 뒤** 한다.

권장 위치: **수집 관리** 하위.

```text
수집 관리
  수집 소스(URL)
  …
  카카오 Import
  재업로드 거부 SHA     ← 후속. 예: /admin_w/ingest/reject-sha
  Similar 임계값
```

카카오 재수집으로 다시 들어오는 것을 막는 장부라, 강의 목록이나 OCR 목록 탭보다 **수집(ingest)** 에 두는 편이 맞다. OCR 하위에 두면 컨텐츠만 지운 SHA를 찾기 어렵고, 강의 하위에 두면 OCR discard SHA가 묻힌다.

후속 화면에서 할 일:

- SHA 앞자리·날짜·누가 넣었는지·출처(OCR idx / content idx) 검색
- **해제(비활성)** → 이후 같은 파일 업로드 허용
- 넣기만 있고 빼기가 없으면 “다시 필요할 수도”를 막음. 후속 전까지는 DB/임시 API로만 해제 가능(운영 메모).

후보로 두지 않는 곳:

- 사이드바 최상위 새 메뉴 — 사용 빈도가 삭제 확인보다 낮음
- `/admin_w/contents/shared-poster`에 섞기 — 그건 살아 있는 공유 포스터용

### 8.4 묶음이면 멤버 SHA를 전부 넣을지 (자세히)

“묶음”이 두 가지라 섞이면 안 된다.

| 종류 | 무엇인가 | 서버에 있는 SHA |
|------|----------|-----------------|
| **카카오 앨범** | `_01` 같은 시각 여러 장 → 한 OCR의 main+sub | 올린 **모든 장**의 source SHA (이번 C 이후 asset 연결) |
| **로컬 similar 그룹** | 사람/자동이 “같은 콘텐츠”로 본 여러 파일. 대표만 업로드 | **올린 대표(및 실제로 전송된 장)** 만. 업로드 안 한 멤버 SHA는 서버에 없음 |

거부 목록은 **서버가 아는 SHA만** 넣을 수 있다. 로컬 similar에서 스킵된 파일은 목록에 안 들어간다. 그게 다시 단독으로 올라오면 다른 SHA라 막히지 않는다. (막으려면 그 건도 한 번 올라온 뒤 지울 때 넣는다.)

#### 이번 정책 (확정): UI에서 장별 고르지 않는다. 첨부된 SHA를 전부 넣는다

체크 한 번 = 그 OCR/컨텐츠가 가진 **main + sub 전부**.

```text
앨범 A,B,C 세 장짜리 OCR을 거부 넣기로 삭제
  → A, B, C SHA 모두 거부
  → 다음에 앨범이든 한 장만 다시 와도 세 장 다 안 돌아옴
```

대표만 넣으면:

```text
main A만 거부, sub B·C는 거부 안 함
  → 재수집 때 B가 단독 업로드되면 새 OCR
  → 또 지워야 함  ← 지금 막으려는 반복
```

그래서 **전부 넣기가 맞다.** 장별 체크박스는 후속에서도 필수가 아니다.

#### 안전장치: 다른 살아 있는 컨텐츠가 쓰는 SHA

강사 프로필처럼 **같은 그림이 다른 강의에도** 있을 수 있다.

```text
거부 넣기 요청이 와도
  그 SHA를 아직 참조하는 다른 OCR/컨텐츠가 있으면
  → 그 SHA는 목록에 넣지 않음 (또는 ingest 시 거부보다 exact 우선)
```

안 그러면 남은 강의 포스터와 같은 파일을 카카오가 또 보내도 막혀 exact 캡션 병합이 안 된다. **삭제 대상에만 붙어 있던 SHA만** 거부한다.

#### 후속으로 미룰 것

- 삭제 모달에서 썸네일마다 SHA 선택
- 로컬 similar 미업로드 멤버까지 서버 거부에 미리 넣기  
둘 다 서버 UI/계약이 커져 **이번 파이프라인 이후**.

### 8.5 목록 해제

후속 관리 화면에서 SHA 비활성 → 이후 ingest 허용.  
이번 개발에는 테이블 + 삭제 시 insert + ingest 조회만. 해제 UI는 이후.

### 8.6 ingest 동작

```text
source SHA가 거부 목록에 활성
  그리고 다른 활성 컨텐츠/OCR이 그 SHA를 안 씀
  → OCR INSERT 하지 않음
  → already_rejected (로컬 캐시)

다른 컨텐츠가 그 SHA를 씀
  → 거부보다 exact 재사용
```

비슷한 **다른 SHA**는 목록에 없으면 막지 않음.

---

## 9. 목표 시나리오 (사용자 확정)

전제: 로컬 이미지 삭제 → prune은 `photo_file`만. 장부는 남음. 재수집 시 새 `photo_id`, 바이트 같으면 SHA 동일.

### 시나리오 1 — OCR 삭제, 컨텐츠 없음

의도: **필요 없어서 지움.**

| 삭제 시 거부 목록 | 동일 이미지 A 재업로드 | 비슷한 이미지 B |
|-------------------|------------------------|-----------------|
| **넣음** | 서버 `already_rejected`. 새 OCR 없음. 로컬 캐시 후 요청 감소 | B SHA가 목록에 없으면 신규 또는 다른 강의 similar |
| **안 넣음** | 신규 OCR (다시 등록 허용) | 동일 |

사용자가 강조한 기본 운영: 지운 것을 매일 다시 지울 수 없으므로, **보통은 넣기**. 나중에 필요하면 목록에서 제거.

### 시나리오 2 — OCR 삭제, 컨텐츠 있음

의도: **강의는 유지, OCR만 정리.**

| 동일 이미지 A | 비슷한 이미지 B |
|---------------|-----------------|
| exact → 기존 컨텐츠에 캡션만 (`sns_appended`). 새 OCR 없음 | similar_hold + **asset 저장**. 다음엔 같은 B SHA는 exact/hold 재사용 |

이 경우 A SHA를 거부 목록에 넣으면 컨텐츠에 캡션을 못 붙이므로 **넣지 않음**.

### 로컬 장부만 있고 서버 거부 목록이 없을 때

같은 PC·장부 정상이면 A 요청이 안 가서 “안 돌아옴”처럼 보인다.  
**장부 유실·다른 PC**면 다시 OCR이 생긴다. 그래서 시나리오 1의 보장은 **서버 거부 목록**이다.

---

## 10. 개발 단계 (착수 전 초안)

사용자 추가 사항 반영 후 순서만 확정. 아래는 합의된 우선순위.

### 1차 — 버그 수정 + 전송량 + 거부 선택

| 단계 | 어디 | 내용 |
|------|------|------|
| **A** | 로컬 | `uploaded_sha_ledger` 스키마 보장. caption 지문. 응답 `ocr_idx`/`request_idx`/`result` 저장. 동일 미디어+동일 캡션 → caption-only. 캡션 변경 → 캡션 요청 |
| **B** | 로컬+서버 | 멱등키에서 `photo_id` 제거. `media_fingerprint + caption_fingerprint + v1` |
| **C** | 서버 | similar/exact 전에 asset+signature 저장. similar_hold도 `ocrcontent_media` 연결. 삭제 큐가 hold asset을 못 지움 |
| **D** | 서버 | 활성 OCR/컨텐츠 SHA면 INSERT 금지, 재사용 + 캡션 정책. 전역 SHA UNIQUE 금지 |
| **E** | 서버 DDL+삭제 UI | 거부 목록 테이블. OCR·컨텐츠 삭제 확인에 체크 **기본 켜짐**. 첨부 SHA 전부 기록(다른 활 참조 제외). ingest 선두에서 `already_rejected` |
| **F** | 로컬 | `already_rejected`면 장부에 거부 캐시 |

A와 C+D는 **같이 1차**. E 없이 A~D만 하면 “지운 A가 장부 깨졌을 때 다시 옴”.

**이번 이후 (서버):** `/admin_w/ingest/reject-sha` 조회·해제 화면. 묶음 장별 선택 UI 없음(정책은 E에서 이미 전부 넣기).

### 2차 — 정교함

- 앨범 지문: `main` + 순서.
- 캡션 정규화(공백/줄바꿈만).
- ingest UNIQUE로 동시성 (D에 일부 포함 가능).
- `ledger-reconcile` (복원 후).
- 멀티PC pre-flight (선택).

### 명시적 비범위

- 웹훅으로 로컬 DB 푸시.
- 새 ingestion_receipt 테이블.
- 기본 경로 pre-flight SHA 배열 API.
- 출처별 캡션 테이블.
- 로컬 similar-detect 결정 리셋 변경 (별 이슈면 §14).

---

## 11. 검증 (개발 시)

1. 같은 파일 두 번째 업로드: 파일 생략 또는 exact. **OCR 행 수 증가 없음.**
2. similar_hold 후 같은 파일 재업로드: exact 또는 기존 hold 재사용. **새 hold OCR 없음.**
3. 캡션만 변경: 새 멱등키, OCR 추가 없음, sns_appended.
4. OCR 삭제 + 컨텐츠 없음 + **거부 넣기** + 재수집 업로드: `already_rejected`, OCR 없음.
5. OCR 삭제 + 컨텐츠 없음 + **거부 안 넣기** + 재업로드: 신규 OCR.
6. OCR 삭제 + 컨텐츠 있음: A는 exact 연결, 거부 목록 기본 비활성.
7. 거부 목록에서 SHA 제거 후 업로드: 다시 등록 가능.
8. `uploaded_sha_ledger` 없는 DB: 서버 C+D+E가 막아 중복 OCR 없음.
9. 로컬 사진 삭제 후 재수집: photo_id가 달라도 같은 SHA는 같은 논리 요청.

---

## 12. 구현 시 건드리는 곳 (예정)

### kakao-import-local

- `sql/006_uploaded_sha_ledger.sql` 및 후속 마이그
- `src/kakao_import/ledger.py`, `db.py`, `upload.py`, `payload.py`
- 테스트: `tests/test_uploaded_sha_ledger.py` 등

### frontend

- `lib/ingest/import/receiveKakaoImport.ts`, `createOcrFromKakaoImport.ts`, `exactLookup.ts`
- `lib/ocr/discardOcrContent.ts`, `deleteMigratedOcr.ts`, OCR/컨텐츠 삭제 API·UI
- `lib/media/*` (hold 경로 asset 링크)
- `types/content.ts` (거부 목록·응답 코드)
- `docs/sql/media-asset-dedupe/018_*.sql` (가칭) + README·허브 §3/§4는 **DDL 작성·적용 후** 갱신
- `signature-and-dup-review-policy.md`: similar hold 시 asset 영속 허용으로 문구 수정

로그: `@lib/logger` / kakao `logging_util`. 쿼리 로그 필수.

---

## 13. 외부 검토에서 가져온 문장 중 폐기

- “SHA당 OCR 1행을 UNIQUE로 건다”
- “멱등키 = source_sha256만”
- “성공 SHA면 HTTP 완전·영구 스킵” (서버 확인 없는 스킵)
- “업로드 전 hold 동기화 API를 1단계 앞에 필수”
- “ingestion_receipt를 새로 두는 것이 최종 대안”

유지하는 정신: **파일 중복 / 요청 멱등 / 캡션 변경 / 의도적 거부**를 한 키로 뭉치지 말 것.

---

## 14. 추가 사항

| # | 날짜 | 내용 | 반영 |
|---|------|------|------|
| 1 | 2026-08-19 | 삭제 시 거부 목록 **기본값 넣기** | §8.2 확정 |
| 2 | 2026-08-19 | 컨텐츠 삭제도 OCR과 **같은 선택** | §8.2 확정 |
| 3 | 2026-08-19 | 거부 목록 **관리 화면**은 서버 후속 (이번 파이프라인 이후). 이번은 삭제 confirm만 | §8.3 |
| 4 | 2026-08-19 | 묶음은 **첨부 SHA 전부** 자동. 장별 UI는 후속에서도 비범위 | §8.4 |

후속 서버 작업 메모: 수집 관리 > 재업로드 거부 SHA (`/admin_w/ingest/reject-sha`)에서 조회·해제.

---

## 15. 한 페이지 요약

```text
해야 함
  로컬 caption-only + 장부 스키마/ocr_idx
  멱등키에서 photo_id 제거, 미디어+캡션 지문
  similar 전 asset 저장, hold도 media 링크
  활성 OCR/컨텐츠 재사용 (전역 SHA당 OCR 금지)
  캡션 덮어쓰기 금지 (sns_appended)
  서버 거부 목록 + 삭제 시 선택 + 해제
  로컬은 서버 응답으로 거부 캐시

하지 않음
  웹훅, 새 receipt 테이블, 기본 pre-flight
  SHA UNIQUE OCR, SHA-only 멱등키
  파일 삭제로 중복 해결, similar 끄기

시나리오
  컨텐츠 없음 + 거부 넣기 → A 안 돌아옴
  컨텐츠 없음 + 거부 안 넣기 → A 다시 등록 가능
  컨텐츠 있음 → A exact 연결, 거부 목록 쓰지 않음
```

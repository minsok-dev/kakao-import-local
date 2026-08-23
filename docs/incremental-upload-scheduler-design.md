 # kakao-import 증분 업로드/스케줄러 설계

 <!-- [변경사유]: 2026-08-20 — upload 전체 재계산 병목 제거, 스케줄러 무인 실행, hold 정책 확정 -->
 <!-- [변경사유]: 2026-08-20 — similar-review/poster-review는 자동 실행에서 제외하고 보류 큐로 넘기는 운영 모델 정리 -->
 <!-- [변경사유]: 2026-08-23 — §20~22 리뷰 합의·classify/similar/parse 동작·시간·parse 증분 축 -->

 관련 문서: [phase3-import.md](./phase3-import.md) · [phase3-ops-stabilization.md](./phase3-ops-stabilization.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [poster-classifier-plan.md](./poster-classifier-plan.md) · [schedule-auto-upload-and-poster-retrain-design.md](./schedule-auto-upload-and-poster-retrain-design.md) (collect→upload 배선·운영 §11) · [room-scoped-auto-upload-dev.md](./room-scoped-auto-upload-dev.md)

 ---

 ## 1. 목표

 `kakao-import upload --no-dry-run`은 앞으로도 **사용자/스케줄러가 동일하게 한 번만 실행**한다.

 다만 내부 구조는 기존의:

 ```text
 실행 시마다 전체 후보 재계산
 → 전체 caption/similar/poster 재판정
 → 업로드/스킵 판단
 ```

 에서 아래 구조로 전환한다.

 ```text
 신규/변경 후보 탐지
 → 변경된 후보만 평가
 → ready만 업로드
 → 애매한 항목은 hold
 → 다음 실행에서는 상태 재사용
 ```

 핵심 목표는 다음과 같다.

 - 사용자 입장에서는 **명령 1회**
 - 스케줄러에서도 **같은 명령** 사용
 - 사람이 필요한 항목은 실패가 아니라 **보류**
 - 이미 처리한 대상을 매번 다시 계산하지 않음
 - 서버 중복 생성 없이 **멱등하게** 재시도 가능

 ---

 ## 2. 운영 원칙

 ### 2.1 업로드 명령은 항상 비대화형

 `upload`는 manual/scheduler에 따라 정책이 달라지면 안 된다.

 ```bash
 kakao-import upload --dry-run
 kakao-import upload --no-dry-run
 ```

 위 두 명령은 언제나 같은 판정 정책을 사용한다.

 사람 개입이 필요한 작업은 별도 명령으로 분리한다.

 ```bash
 kakao-import similar-review
 kakao-import poster-review
 kakao-import hold-report
 ```

 ### 2.2 보류는 실패가 아니다

 아래 항목은 자동 업로드하지 않고 `hold_*` 상태로 남긴다.

 - `poster uncertain`
 - `similar deferred`
 - 파일 없음
 - 사람이 별도 검토해야 하는 항목

 즉, 스케줄러에서는 review UI를 열지 않는다.  
 대신 **확정 가능한 항목만 업로드**하고, 애매한 항목은 나중에 사람이 처리한다.

 ### 2.3 이미 업로드된 항목은 파일이 없어도 재업로드하지 않는다

 로컬 폴더 파일 존재 여부가 아니라 **후보 상태 DB + 서버 멱등성**이 기준이다.

 ---

 ## 3. 현재 병목의 원인

 현재 `upload`는 실질적으로 업로드 명령이 아니라 전체 평가 배치에 가깝다.

 주요 병목:

 1. `upload.py`의 `cmd_upload()`가 매번 `build_batch_manifest()`를 호출
 2. `payload.py`의 `build_upload_items()`가 전체 exact 대표 후보를 다시 계산
 3. `caption_build.py`에서 각 photo마다 exact/group_text/similar union SQL 반복
 4. 실제 업로드 전 단계에서 이미 스킵될 항목도 모두 재평가
 5. 업로드 사이 sleep까지 더해져 총 수행 시간이 커짐

 이 구조는 수동 실행에도 느리고, 스케줄러에는 더 부적합하다.

 ---

 ## 4. 상태 모델

 최종 상태는 아래처럼 정리한다.

 ### 처리 상태

 - `new`
 - `evaluating`
 - `ready`
 - `uploading`
 - `uploaded`

 ### 제외/보류 상태

 - `excluded_non_poster`
 - `excluded_group_member`
 - `hold_poster_uncertain`
 - `hold_similar_deferred`
 - `hold_missing_file`
 - `hold_manual_review`

 ### 실패/재시도 상태

 - `retry_wait`
 - `failed_terminal`

 설명:

 - `excluded_non_poster`: 자동 분류상 업로드 대상이 아님
 - `excluded_group_member`: same_content/partial에서 대표가 아닌 멤버
 - `hold_*`: 사람 개입 또는 외부 조건이 충족되어야 재개 가능
 - `retry_wait`: 자동 재시도 대상
 - `failed_terminal`: 자동으로는 더 진행하지 않음

 ---

 ## 5. 후보 식별 방식

 `candidate_key`는 대표 photo나 대표 SHA 하나로 만들지 않는다.

 이유:

 - 대표 선정 기준이 바뀌면 같은 논리 후보가 새 후보처럼 보일 수 있음
 - 나중에 번들 구성/대표 이미지가 바뀌어도 동일 업로드 단위로 인식되어야 함

 ### 5.1 단일 이미지

 - `media_fingerprint = sha256`

 ### 5.2 번들(main + sub)

 업로드 단위 전체를 대표하는 fingerprint를 사용한다.

 권장 입력:

 - 버전 문자열
 - main 이미지 SHA
 - 정렬된 sub 멤버 SHA 집합
 - 멤버 역할(main/sub)

 ### 5.3 정렬 규칙은 반드시 결정적이어야 함

 `ordered_sub_shas`는 매 실행마다 같은 순서가 보장되어야 한다.

 권장 우선순위:

 1. 번들 `sort_order`
 2. 파일명 앨범 seq (`_01`, `_02`, `_03`)
 3. fallback으로 SHA 정렬

 즉, **도메인 순서를 우선하고** 최종 fallback만 SHA 정렬로 한다.

 ---

 ## 6. 후보/멤버 저장 구조

 1차 구현 기준으로 **후보 테이블 1개 + 멤버 테이블 1개**를 권장한다.

 ### 6.1 `upload_candidate`

 업로드 단위의 상태를 저장한다.

 권장 필드:

 - `id`
 - `candidate_key`
 - `media_fingerprint`
 - `caption_fingerprint`
 - `evaluation_fingerprint`
 - `state`
 - `state_reason`
 - `caption_text_cached`
 - `poster_status`
 - `poster_confidence`
 - `poster_decision_source`
 - `poster_classifier_version`
 - `similar_group_id`
 - `similar_decision`
 - `attempt_count`
 - `next_retry_at`
 - `last_error_code`
 - `last_error`
 - `server_receipt_id`
 - `server_ocr_id`
 - `lease_owner`
 - `lease_expires_at`
 - `needs_rebuild`
 - `created_at`
 - `updated_at`
 - `uploaded_at`

 ### 6.2 `upload_candidate_member`

 후보를 구성하는 이미지 목록을 저장한다.

 권장 필드:

 - `candidate_id`
 - `photo_id`
 - `sha256`
 - `member_role` (`main`, `sub`)
 - `sort_order`
 - `rel_path`

 ---

 ## 7. caption 캐시 정책

 `caption_fingerprint`만 저장하고 본문은 매번 재조립하는 방식은 1차 구현에 부적합하다.

 이유:

 - exact SHA 확장
 - group_text 조회
 - similar union
 - 방 구분선 조립

 비용이 모두 크다.

 따라서 아래 둘 다 유지한다.

 - `caption_fingerprint`: 변경 감지용
 - `caption_text_cached`: 재사용용

 ---

 ## 8. 재평가 기준

 `needs_rebuild`만으로 재평가를 결정하면 안 된다.

 ### 8.1 `needs_rebuild`

 - 사람이 강제로 다시 계산시키고 싶을 때 사용

 ### 8.2 `evaluation_fingerprint`

 자동 변경 감지용.

 권장 입력:

 - `media_fingerprint`
 - `caption_fingerprint`
 - `poster_status`
 - `poster_classifier_version`
 - `similar_decision`
 - `bundle_policy_version`
 - `caption_builder_version`

 재평가 조건:

 - `needs_rebuild = 1`
 - 또는 `evaluation_fingerprint` 변경

 ---

 ## 9. similar / poster 정책

 ### 9.1 poster 자동 분류

 - `poster` → 계속 진행
 - `non_poster` → `excluded_non_poster`
 - `uncertain` → `hold_poster_uncertain`

 ### 9.2 similar decision

 - `different_content` → 각각 `ready`
 - `same_content` → 대표만 `ready`, 나머지는 `excluded_group_member`
 - `partial` → subgroup 대표만 `ready`, 나머지는 `excluded_group_member`
 - `deferred` → 관련 후보 전체 `hold_similar_deferred`

 주의:

 - unresolved similar connected component가 있으면 영향 범위 전체를 보류하는 것이 안전하다.

 ---

 ## 10. 파일 삭제 정책

 ### 10.1 이미 업로드 완료된 항목

 파일이 삭제되어도 재업로드하지 않는다.

 ### 10.2 아직 업로드되지 않은 항목

 파일이 없으면 `hold_missing_file`.

 ### 10.3 caption-only 가능 항목

 서버 exact/ledger 재사용이 가능하면 파일 없이 처리할 수 있다.  
 그렇지 않으면 `hold_missing_file` 또는 파일 복구 후 재시도.

 ---

 ## 11. 락 / lease / 중복 실행 방지

 스케줄러 환경에서는 두 층이 모두 필요하다.

 ### 11.1 전체 실행 lock

 - 중복 실행 방지

 ### 11.2 candidate lease

 상태:

 - `evaluating`
 - `uploading`

 에서 프로세스가 죽을 수 있으므로:

 - `lease_owner`
 - `lease_expires_at`

 를 두고 다음 실행에서 복구한다.

 ---

 ## 12. 서버 멱등성

 로컬 큐가 생겨도 서버 멱등성은 유지해야 한다.

 이유:

 - 서버 성공 후 응답 유실
 - 업로드 도중 클라이언트 종료
 - 재시도 시 중복 생성 위험

 권장 멱등키 입력:

 - `media_fingerprint`
 - `caption_fingerprint`

 서버 응답 저장:

 - `server_receipt_id`
 - `server_ocr_id`
 - 결과 상태

 ---

 ## 13. 자동 재시도 정책

 `retry_wait`에는 backoff와 상한을 둔다.

 예시:

 - 1회 실패 → 5분 뒤
 - 2회 실패 → 30분 뒤
 - 3회 실패 → 2시간 뒤
 - 5회 초과 → `failed_terminal`

 필요 필드:

 - `attempt_count`
 - `next_retry_at`
 - `last_error_code`

 ---

 ## 14. review 후 재평가

 사람이 아래 명령에서 결정을 바꾸면:

 - `similar-review`
 - `poster-review`

 반드시 관련 후보/그룹에:

 - `needs_rebuild = 1`

 을 기록해야 한다.

 이 트리거가 빠지면 다음 `upload` 실행에서 변경사항이 반영되지 않는다.

 ---

 ## 15. hold 해제 조건

 | 상태 | 해제 조건 |
 |------|-----------|
 | `hold_poster_uncertain` | 분류기 버전 변경 또는 수동 poster 결정 |
 | `hold_similar_deferred` | similar decision 저장/변경 |
 | `hold_missing_file` | 파일 재발견 |
 | `hold_manual_review` | 관리자 명시 결정 |
 | `retry_wait` | `next_retry_at` 도달 |
 | `failed_terminal` | 수동 재개 |

 ---

 ## 16. dry-run 정책

 `upload --dry-run`은:

 - 서버를 변경하지 않음
 - 로컬 후보 상태/평가 캐시는 갱신함

 즉:

 ```text
 discover/evaluate/cache 저장
 + HTTP 업로드는 하지 않음
 ```

 ---

 ## 17. 결과 리포트

 결과 리포트는 보류가 있어도 정상 종료 가능한 구조여야 한다.

 예시:

 ```json
 {
   "runId": "20260820-050000",
   "discovered": 120,
   "evaluated": 43,
   "reused": 77,
   "ready": 28,
   "uploaded": 27,
   "retryWait": 1,
   "holds": {
     "similarDeferred": 7,
     "posterUncertain": 5,
     "missingFile": 3
   }
 }
 ```

 종료 코드 원칙:

 - 정상 업로드 + 보류 존재 → `0`
 - ready 0건 + 보류만 존재 → `0`
 - 설정/인증/DB 오류 → 비정상 종료

 ---

 ## 18. 최종 명령 구조

 ```bash
 kakao-import upload --dry-run
 kakao-import upload --no-dry-run
 kakao-import similar-review
 kakao-import poster-review
 kakao-import hold-report
 ```

 `upload`는 항상 비대화형이며, review는 별도 명령으로만 수행한다.

 ---

 ## 19. 최종 결론

 이 설계의 핵심은:

 - 명령은 1개 유지
 - 내부는 증분 평가
 - 리뷰가 필요한 것은 보류
 - ready만 업로드
 - 상태/캡션/평가 결과를 재사용
 - 서버 멱등성은 유지

 즉, **“전체 재계산형 upload”를 “증분 상태 기반 업로드 파이프라인”으로 바꾸는 것**이다.

 ---

 <!-- [변경사유]: 2026-08-23 — 외부 리뷰 합의·단계별 실제 동작·시간 절감·parse 증분 축 분리 -->

 ## 20. 외부 리뷰 합의 (운영 전 게이트 vs P1)

 ### 20.1 맞는 결론

 1. `similar-detect` 실패는 collect 체인의 `check=True`로 이미 upload 호출이 막힌다.  
    단, 내부에서 예외를 삼키고 **exit 0**으로 끝나면 막지 못하므로 그 경로는 테스트로 확인한다.
 2. poster는 **두 겹**이 안전하다.  
    - 명령 실패 + upload ON → 체인 중단  
    - 개별 후보에 분류 행 없음 → hold (서버 payload 미포함)
 3. collect→upload **배선** ≠ **증분 최종형**. 라벨을 섞지 않는다.

 ### 20.2 자동 업로드 ON 전 필수 (단순 P1로만 미루지 않음)

 - classify fail-closed + 미분류 hold + payload 미포함 테스트  
 - 동일 입력 실업로드 2회 → **새 OCR 0** (가능하면 불필요 HTTP 0)  
 - similar rebuild 중간 실패 → 기존 decision 보존 (단일 트랜잭션 확인·테스트)  
 - 작업 스케줄러 중복 실행 금지 + 로그인된 Windows 세션  

 ### 20.3 P1로 넘겨도 되는 것

 - similar 그룹 fingerprint를 photo_id → **멤버 SHA**  
 - `decided_by` / `decided_at` / `decision_version`  
 - candidate lease · backoff · 완전 변경분 평가 큐  
 - dataset sync 이력 · 주간 retrain 자동화  

 ### 20.4 decision 표현

 - 현재(P0): **동일 멤버 집합의 non-deferred decision 보존** (human 전용이 아님)  
 - P1: `decided_by` 추가 후 “사람 판정 우선”으로 문서·코드 정렬  

 ### 20.5 현재 증분 상태 (정직한 라벨)

 | 라벨 | 의미 |
 |------|------|
 | **중간 단계 (이미 일부 있음)** | `upload_candidate` / `uploaded_sha_ledger` / caption·uploaded skip |
 | **최종형 (미완)** | 변경분만 평가, process lock, candidate lease, receipt 중심 재시도 |

 표현: *“재전송·재평가를 줄이는 중간 단계”* / *“변경분 큐 최종형은 미완”*.

 ---

 ## 21. 단계별 “지금 어떻게 도나” — classify / similar / parse

 `upload` 증분 설계가 직접 바꾸는 범위와, **체인 앞단(run/classify/similar)** 을 구분한다.

 ### 21.1 전체 체인 한눈에

 ```text
 collect (PC)          → 워터마크 기준 증분 수집 (이미 증분)
 kakao-import run
   scan                → photos/ 파일 목록 upsert (사실상 매번 전 파일 walk)
   parse               → 방마다 최신 채팅 TXT **전체 재파싱** + 메시지 replace
   match               → 매칭 테이블 clear 후 **전체(또는 --room) 재매칭**
   hash                → 서명/해시 (기존 있으면 일부 skip 가능)
 poster-classify        → SHA 있는 photo **전부 순회** (임베딩은 캐시, 판정은 다시)
 similar-detect        → signature로 그룹 **재클러스터** (decision만 복원)
 upload                → 후보 평가 + 전송  ← 본 문서의 “증분” 주 대상
 ```

 ### 21.2 `poster-classify` — 왜 “신규만이면 조금, 전방 전체면 그대로”인가

 **지금 코드 동작**

 1. `photo_file`에서 `sha256` 있는 행을 **전부** SELECT  
 2. (`--room` 있으면 해당 방만 남김)  
 3. 파일마다:  
    - human 분류 행이 있으면 **덮지 않음** (`skipped_human`)  
    - 그 외는 CLIP 임베딩(`embed_cached` — **같은 SHA면 디스크 캐시 재사용**)  
      + 로지스틱 `predict` → `poster_classify` upsert  
 4. **“이미 model이 같은 version으로 분류했으니 predict 스킵” 은 없음**

 따라서:

 | 상황 | 체감 |
 |------|------|
 | 방 한정 `--room` + 사진 수십 장 | 순회 범위가 작아 **시간↓** |
 | 스케줄 전방 · 사진 수천 장 | 루프는 전부. 임베딩은 캐시로 빠르지만 **predict·DB upsert는 반복** → 체감은 “조금~중간” |
 | 모델 버전 바뀜 | 전원 재판정이 의도된 동작에 가깝다 |

 **upload 증분 최종형이 이 단계를 바꾸지 않는다.**  
 classify를 “미분류·needs_rebuild만”으로 줄이는 것은 **별도 개선**(원하면 P1 run 트랙).

 ### 21.3 `similar-detect` — 재클러스터 vs decision 보존

 **지금 코드 동작**

 1. non-deferred decision을 멤버 fingerprint(`sorted(photo_id)`)로 스냅샷  
 2. (방 필터 시) 그 방 photo가 속한 그룹만 DELETE, 아니면 workspace 재구성  
 3. dhash/phash 거리로 **다시 클러스터** → 그룹 INSERT (기본 `deferred`)  
 4. fingerprint 일치 시 스냅샷 decision **복원**  
 5. upload 큐는 건드리지 않음 (`needs_rebuild` 마킹은 리뷰/결정 시)

 따라서:

 | 상황 | 체감 |
 |------|------|
 | `--room` 한 방 | 클러스터 입력이 그 방 photo만 → **시간↓**, 타 방 그룹 유지 |
 | 스케줄 전방 | 서명 있는 (non_poster 제외) **전 photo 재클러스터** → 규모에 비례해 시간 큼 |
 | 이미 사람이 확정한 그룹 | 멤버 집합이 같으면 decision은 남음. **계산 자체는 다시 함** |

 **upload 증분 최종형이 similar 재클러스터를 “신규만”으로 바꾸지 않는다.**  
 (장기: 서명/멤버 변경분만 영향 범위 재클러스터 — 별도 과제.)

 ### 21.4 `parse` / `match` — 시간이 많이 드는 이유 (사용자 질문)

 **맞다. 파싱·매칭을 증분하면 전체 스케줄 시간이 크게 줄 수 있다.**  
 다만 그것은 **본 문서의 “upload 후보 증분”과 다른 축**이다.

 **지금 parse**

 - collect는 워터마크로 **새 그리드만** 가져온다.  
 - 그런데 `kakao-import parse`는 방마다 **최신 export TXT 전체를 다시 읽고**  
   `parse_chat_file` → `replace_messages` 한다.  
 - `content_sha256`은 저장하지만, **“SHA 같으면 파싱 스킵” 분기는 없다.**  
 - 채팅 export가 수개월·수만 줄이면 **매번 전체 파싱 비용**이 반복된다.

 **지금 match**

 - 방(또는 전체) 매칭 테이블을 지운 뒤 **사진·메시지 전부로 재매칭**한다.  
 - 어제와 같은 채팅이라도 match는 다시 돈다.

 **그래서**

 ```text
 upload 증분 최종형만 완료
 → upload 평가/재전송은 크게 ↓
 → parse/match/similar가 길면 전체 wall-clock은 여전히 김

 parse(+match) 증분까지 하면
 → “매일 전체 TXT 재파싱”이 사라져
 → 체감 절감이 upload 증분만보다 더 클 수 있음
 ```

 **parse 증분 후보 (별도 P1/P2 — 본 upload 설계와 분리 표기)**

 | 아이디어 | 효과 | 주의 |
 |----------|------|------|
 | chat `content_sha256` 동일 → parse·replace 스킵 | 파싱 CPU/IO 대폭↓ | export가 매일 “전체 덤프로 덮어쓰기”면 SHA가 바뀌어 스킵 안 됨 |

<!-- [변경사유]: 2026-08-24 — I7 1차(SHA 스킵) 구현. 날짜창·match 증분은 후속 -->
**구현 상태:** `cmd_parse` 가 동일 `rel_path` + `content_sha256` 이고 메시지가 있으면 `replace_messages` 를 스킵한다 (`skipped_unchanged`).
날짜 창 파싱·match 행 보존은 **아직 미구현** (부분 파싱+전역 wipe 금지).
 | mtime/size만 보고 스킵 | 구현 단순 | 내용 변경 누락 위험 → SHA가 더 안전 |
 | 신규 photo만 match | match 시간↓ | 캡션·같은 분 그룹이 과거 메시지에 의존 → **방 단위 재매칭**이 더 안전할 수 있음 |
 | scan: mtime 불변 파일 skip | walk는 남음, upsert↓ | 상대적 이득 작을 수 있음 |

 문서 상태 표현 권장:

 ```text
 upload 증분 P1     = 후보 평가·전송 큐
 run/parse 증분     = 별도 트랙 (시간 체감에 더 중요할 수 있음)
 room-scoped E2E    = 한 방 스모크용 범위 축소 (이미 계약: room-scoped-auto-upload-dev.md)
 ```

 ---

 ## 22. 시간 절감 예상 (축을 나눠서)

 ### 22.1 무엇이 줄고 / 안 주는가

 | 단계 | upload 증분 최종형 | parse/match 증분(별도) | `--room` 한정 |
 |------|-------------------|------------------------|---------------|
 | collect | ❌ | ❌ | ✅ 그 방만 |
 | parse / match | ❌ | ✅ **여기가 클 수 있음** | ✅ 그 방만 |
 | poster-classify | ❌ (루프 정책) | △ 미분류만 돌리면 ✅ | ✅ |
 | similar-detect | ❌ (재클러스터 정책) | △ 영향분만이면 ✅ | ✅ |
 | upload 평가(캡션 SQL) | ✅ **주 대상** | — | ✅ 후보 수↓ |
 | HTTP + sleep(기본 5초/건) | ✅ ready 신규만 | — | ✅ |

 ### 22.2 upload 증분만 완료했을 때 (일상·신규 ≪ 누적)

 - 평가: 누적 N건 × t초 → 신규 n건 × t초 (예: N=500, n=15 → 평가 **~90%+↓**)  
 - 전송+sleep: 이미 ledger/candidate로 줄어든 부분과 합쳐, **올릴 것만**  
 - **전체 파이프라인**: parse·similar가 길면 **10~40%** 체감일 수도,  
   upload 평가가 병목이면 **절반 이상**도 가능  

 ### 22.3 parse(+match) 증분까지 했을 때

 - 채팅 TXT가 크고 매일 내용이 거의 같다면, **parse가 wall-clock의 상당 부분**인 경우가 많다.  
 - 그때는 upload 증분보다 **parse 스킵이 체감이 더 큼**이 정상이다.  
 - 정량: “동일 export SHA로 2회 `run`” 전후 분(分)을 재면 확정.  
   문서에 고정 분을 박지 말고, 운영에서 한 번 측정한다.

 ### 22.4 시나리오 요약

 | 시나리오 | 기대 |
 |----------|------|
 | 어제까지 업로드 완료, 오늘 신규 소수, **upload만** 증분 최종형 | upload 구간 대폭↓, 앞단은 비슷 |
 | 위 + **parse SHA 스킵** | 전체 스케줄이 짧게 느껴질 가능성 큼 |
 | 첫 백필 / 신규 ≈ 전체 | 증분 이득 거의 없음 |
 | 한 방 `--room` 스모크 | 방 한정만으로도 앞단·upload 모두↓ (증분 최종형 없이도) |

 ### 22.5 한 줄

 **upload 증분 = “올릴지 말지·캡션 다시 짤지” 비용 제거.**  
 **parse 증분 = “어제와 같은 대화 파일 다시 읽기” 비용 제거 — 체감이 더 클 수 있고, 별도 개발 축이다.**

 # kakao-import 증분 업로드/스케줄러 설계

 <!-- [변경사유]: 2026-08-20 — upload 전체 재계산 병목 제거, 스케줄러 무인 실행, hold 정책 확정 -->
 <!-- [변경사유]: 2026-08-20 — similar-review/poster-review는 자동 실행에서 제외하고 보류 큐로 넘기는 운영 모델 정리 -->

 관련 문서: [phase3-import.md](./phase3-import.md) · [phase3-ops-stabilization.md](./phase3-ops-stabilization.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [poster-classifier-plan.md](./poster-classifier-plan.md)

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

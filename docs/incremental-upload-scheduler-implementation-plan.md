 # kakao-import 증분 업로드/스케줄러 개발 계획서

 <!-- [변경사유]: 2026-08-20 — 증분 업로드/hold 큐/비대화형 스케줄러 실행을 위한 구현 순서와 체크리스트 정리 -->

 관련 문서: [incremental-upload-scheduler-design.md](./incremental-upload-scheduler-design.md) · [phase3-import.md](./phase3-import.md) · [phase3-ops-stabilization.md](./phase3-ops-stabilization.md)

 ---

 ## 1. 개발 목표

 현재 `kakao-import upload --no-dry-run`의 전체 재계산 병목을 제거하고, 아래 조건을 만족하는 구조로 전환한다.

 - 명령어는 1회만 실행
 - 스케줄러도 동일 명령 실행
 - 사람 개입 없이 자동 처리
 - `similar deferred`, `poster uncertain`은 자동 업로드하지 않고 보류
 - 이미 처리한 항목은 상태 재사용
 - 서버 멱등성 유지

 ---

 ## 2. 구현 범위

 이번 작업의 1차 범위는 다음과 같다.

 1. 로컬 상태 DB 스키마 추가
 2. 업로드 후보/멤버 상태 관리 로직 추가
 3. `upload`를 상태 기반 증분 파이프라인으로 변경
 4. `similar-review`, `poster-review` 이후 재평가 트리거 연결
 5. 결과/보류 리포트 추가

 이번 1차 범위에서 하지 않는 것:

 - review UI 자체 대개편
 - 포스터 분류 모델 재학습 자동화
 - 업로드 병렬 처리
 - 서버 API 계약 대규모 변경

 ---

 ## 3. 구현 단계

 ### Phase A. 로컬 상태 스키마 추가

 목표:

 - 후보 상태를 DB에 저장할 기반 마련

 작업:

 - SQLite DDL 추가
 - `upload_candidate`
 - `upload_candidate_member`
 - 필요 시 run lock/lease 보조 테이블 추가

 검토 포인트:

 - `candidate_key` unique
 - `media_fingerprint` / `evaluation_fingerprint` 인덱스
 - 상태값 enum 제약 또는 상수 정의

 산출물:

 - 신규 migration/DDL
 - 상태 모델 상수

 ---

 ### Phase B. ledger / 상태 저장 계층 추가

 목표:

 - 업로드 이력과 후보 상태를 함께 관리할 수 있게 함

 작업:

 - `ledger.py` 확장 또는 신규 상태 모듈 추가
 - 후보 생성/조회/업데이트 함수
 - member upsert 함수
 - lease 획득/해제 함수
 - retry/backoff 계산 함수
 - hold 해제 함수

 검토 포인트:

 - 기존 uploaded SHA ledger와 충돌 없이 공존
 - `uploaded`와 `caption_only` 재사용 흐름 유지

 산출물:

 - 상태 저장 API
 - retry/lease 유틸

 ---

 ### Phase C. payload 구성 로직 리팩터링

 목표:

 - `payload.py`의 전체 재계산 구조를 해체

 작업:

 - `build_upload_items()`를 상태 기반 선별 구조로 전환
 - 전체 exact 대표를 매번 다시 처리하지 않도록 변경
 - `candidate_key`, `media_fingerprint`, `caption_fingerprint`, `evaluation_fingerprint` 계산 추가
 - `caption_text_cached` 재사용
 - `similar`/`bundle` 평가 결과를 후보 상태에 반영

 검토 포인트:

 - 번들 정렬 기준이 결정적이어야 함
 - same_content/partial에서 대표 외 멤버는 `excluded_group_member`
 - deferred는 관련 영향 범위 전체 `hold_similar_deferred`

 산출물:

 - 상태 기반 item builder
 - 평가 결과 snapshot 구조

 ---

 ### Phase D. caption 계산 최적화

 목표:

 - per-photo 반복 SQL 병목 완화

 작업:

 - `caption_build.py`에서 반복 조회 구조 정리
 - 변경 없는 후보는 `caption_text_cached` 재사용
 - 변경된 후보만 caption 재조립
 - `caption_builder_version` 상수 도입

 검토 포인트:

 - exact/group_text/similar union 결과 동일성 유지
 - dry-run과 실업로드 시 같은 caption 생성 보장

 산출물:

 - caption cache 정책
 - version 기반 재평가 기준

 ---

 ### Phase E. upload 실행 로직 전환

 목표:

 - `upload.py`를 큐 소비형으로 전환

 작업:

 - `cmd_upload()` 내부를 아래 단계로 분리

 ```text
 discover
 -> evaluate changed candidates
 -> select ready candidates
 -> acquire lease
 -> upload
 -> update result
 ```

 - `ready`만 업로드
 - `hold_*`는 정상 보류
 - `retry_wait`는 시간 도달 시만 재시도
 - 기존 sleep/ocr_extra_sleep 유지

 검토 포인트:

 - 응답 유실 시 서버 멱등성 재사용
 - 업로드 성공 후 상태 저장 실패 시 다음 실행에서 중복 생성 없어야 함

 산출물:

 - 증분 업로드 파이프라인
 - 결과 요약 JSON

 ---

 ### Phase F. review 이후 재평가 트리거 연결

 목표:

 - 사람이 결정한 결과가 다음 upload에 반영되도록 보장

 작업:

 - `similar-review` 결정 저장 시 관련 후보/그룹에 `needs_rebuild = 1`
 - `poster-review` 결정 저장 시 관련 후보에 `needs_rebuild = 1`

 검토 포인트:

 - 단일 후보만 다시 열지, 영향 그룹 전체를 다시 열지 정책 일치

 산출물:

 - review -> rebuild 트리거

 ---

 ### Phase G. hold/report 명령 추가

 목표:

 - 스케줄러 결과와 수동 후처리를 쉽게 함

 작업:

 - `hold-report` CLI 추가
 - 상태별 보류 건수/목록 출력
 - 결과 JSON 파일 저장 형식 정리

 검토 포인트:

 - `hold_poster_uncertain`
 - `hold_similar_deferred`
 - `hold_missing_file`
 - `failed_terminal`

 산출물:

 - 보류 목록 조회 명령
 - 운영용 결과 리포트

 ---

 ## 4. 파일별 수정 계획

 ### 반드시 수정

 - `src/kakao_import/payload.py`
 - `src/kakao_import/upload.py`
 - `src/kakao_import/caption_build.py`
 - `src/kakao_import/ledger.py`
 - `src/kakao_import/cli.py`

 ### 필요 시 추가

 - `src/kakao_import/pipeline.py`
 - `src/kakao_import/*state*.py` 또는 별도 상태 모듈
 - SQLite DDL / migration 파일

 ### review 연동 확인 대상

 - `src/kakao_import/similar_review.py`
 - `src/kakao_import/poster_review.py`
 - `src/kakao_import/poster_label.py`
 - `src/kakao_import/similar_policy.py`

 ---

 ## 5. 상태 전이 규칙

 ### 기본 흐름

 ```text
 new
 -> evaluating
 -> ready
 -> uploading
 -> uploaded
 ```

 ### 분기

 ```text
 evaluating
 -> excluded_non_poster
 -> excluded_group_member
 -> hold_poster_uncertain
 -> hold_similar_deferred
 -> hold_missing_file
 -> retry_wait
 -> failed_terminal
 ```

 ### 재개

 - `hold_poster_uncertain` -> 수동 poster 결정 또는 classifier version 변경
 - `hold_similar_deferred` -> similar 결정 저장/변경
 - `hold_missing_file` -> 파일 재발견
 - `retry_wait` -> `next_retry_at` 도달
 - `failed_terminal` -> 관리자 수동 재개

 ---

 ## 6. retry / backoff 정책

 권장 기본값:

 - 1회 실패 -> 5분
 - 2회 실패 -> 30분
 - 3회 실패 -> 2시간
 - 최대 5회
 - 5회 초과 -> `failed_terminal`

 별도 저장 필드:

 - `attempt_count`
 - `next_retry_at`
 - `last_error_code`
 - `last_error`

 ---

 ## 7. dry-run 정책

 `upload --dry-run`은:

 - 서버 업로드는 하지 않음
 - 로컬 후보 상태와 평가 캐시는 갱신함

 목적:

 - 다음 실업로드 가속
 - hold/ready 상태 사전 확인
 - 동일 계산 반복 방지

 ---

 ## 8. 성공 기준

 아래 조건을 만족하면 1차 구현 성공으로 본다.

 1. 연속 2회 실행 시, 변경이 없으면 2번째 실행의 재평가/업로드 건수가 거의 0
 2. `poster uncertain`은 자동 업로드되지 않고 hold 상태로 남음
 3. `similar deferred`는 자동 업로드되지 않고 hold 상태로 남음
 4. 이미 업로드 완료된 항목은 로컬 파일이 없어도 재업로드되지 않음
 5. 미업로드 항목에서 파일이 없으면 `hold_missing_file`
 6. 서버 응답 유실 후 재실행해도 OCR 중복 생성이 없음
 7. review 후 `needs_rebuild = 1`이 찍혀 다음 upload에서 반영됨
 8. 스케줄러 중복 실행 시 lock/lease로 중복 업로드가 방지됨

 ---

 ## 9. 테스트 계획

 최소 테스트 시나리오:

 - 동일 명령 2회 실행 -> 두 번째 업로드 0건
 - `uploaded` 항목 파일 삭제 -> 재업로드 안 됨
 - 미업로드 항목 파일 삭제 -> `hold_missing_file`
 - `poster uncertain` -> `hold_poster_uncertain`
 - `similar deferred` -> `hold_similar_deferred`
 - review 결정 후 `needs_rebuild = 1` 확인
 - 서버 성공 후 응답 유실 -> 멱등 재시도 시 중복 없음
 - `uploading` 상태 중단 -> lease 만료 후 복구
 - retry limit 초과 -> `failed_terminal`

 ---

 ## 10. 구현 순서 권장

 실제 개발은 아래 순서로 진행하는 것이 가장 안전하다.

 1. 상태 테이블 DDL 추가
 2. `ledger.py` 상태 API 추가
 3. `payload.py` fingerprint/evaluation 로직 추가
 4. `caption_build.py` 캐시 재사용 구조 추가
 5. `upload.py` 증분 큐 처리 전환
 6. `similar-review` / `poster-review`의 `needs_rebuild` 연결
 7. `hold-report` 추가
 8. 통합 테스트

 ---

 ## 11. 오픈 이슈

 구현 전에 아래는 최종 확인이 필요하다.

 - caption-only를 상태 모델에 별도 `pending_action`으로 둘지 여부
 - 결과 JSON 저장 경로/보존 정책
 - hold 항목 알림 채널(파일만 / 슬랙 등)

 ---

 ## 12. 결론

 이 계획의 목적은 기존 `upload`를 없애는 것이 아니라,  
 **같은 명령을 유지한 채 내부를 증분 상태 기반 파이프라인으로 바꾸는 것**이다.

 최종적으로 기대하는 운영 모습은 다음과 같다.

 ```text
 스케줄러가 kakao-import upload --no-dry-run 실행
 -> 신규/변경분만 평가
 -> 확정 가능한 것만 업로드
 -> 애매한 것은 hold
 -> 사람이 나중에 review
 -> 다음 실행에서는 바뀐 것만 다시 계산
 ```

 이 구조가 지금 요구사항에 가장 잘 맞는다.

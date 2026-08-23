# 개발 문서 — 방 한정 E2E 자동 업로드 + 체인 fail 정책

<!-- [변경사유]: 2026-08-23 — `--room`이 collect만 적용되던 버그 수정·classify fail-closed·미분류 hold·증분 P1 범위 분리 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-08-23 |
| 상태 | **구현 대상 (본 문서 = 작업 계약)** |
| 레포 | `kakao-pc-collect` · `kakao-import-local` |
| 관련 | [schedule-auto-upload-and-poster-retrain-design.md](./schedule-auto-upload-and-poster-retrain-design.md) · [incremental-upload-scheduler-design.md](./incremental-upload-scheduler-design.md) (§20~22: 리뷰 합의·classify/similar/parse 동작·시간 축) |

---

## 0. 사용자 기대 (버그 정의)

```text
kakao-pc-collect run --room hongdae_bonita --with-upload
```

기대:

1. **그 방만** 카톡 수집  
2. **그 방만** scan / parse / match / hash / merge 반영  
3. **그 방만** poster-classify  
4. **그 방만** similar-detect (다른 방 similar 그룹 파괴 금지)  
5. **그 방만** upload (--no-dry-run)  
6. 스케줄/자동 체인에서 **poster-review UI 대기 금지** (`--no-review`)  
7. upload 켠 상태에서 **poster-classify 실패 → upload 금지**  
8. **미분류(classify 행 없음) → 업로드 금지(hold)** (활성 모델이 있을 때)

`--room`이 collect에만 먹고 import가 전체 raw를 돌리면 **버그**다.  
(플래그 의미 = E2E 방 한정.)

---

## 1. “시간 줄어드나?” — 정직한 답

| 조치 | 한 방 `--room` 실행 | 스케줄(방 전부, `--room` 없음) |
|------|---------------------|--------------------------------|
| **본 문서 방 한정 E2E** | ✅ parse 등 그 방만 → **시간 크게 감소** | 변화 없음(전체 유지) |
| **증분 candidate 최종형** (P1, 별도) | 추가 절감 가능 | ✅ upload **평가·전송** 위주 감소 (앞단 parse는 별도) |
| **parse/match 증분** (별도 트랙) | 추가 절감 | ✅ 채팅 TXT 재파싱이 길면 **여기가 체감 핵심**일 수 있음 |
| fail-closed / 미분류 hold | 시간↓ 아님 | **잘못된 업로드 방지** |

이번 구현은 **방 한정 + 안전 fail 정책**이다.  
`upload_candidate` / lease / fingerprint 기반 **완전 증분 재설계는 P1**로 남긴다.  
지금 자동 upload를 “증분 최종형 완료”라고 부르지 않는다.  
단계별 실제 동작·parse 증분 축: [incremental-upload-scheduler-design.md](./incremental-upload-scheduler-design.md) §21~22.

---

## 2. 구현 범위 (이번 / 나중)

### 2.1 이번 (필수)

| ID | 내용 |
|----|------|
| R1 | collect `--room` → `kakao-import … --room` 전달 |
| R2 | `run`/`scan`/`parse`/`match`/`hash` room 필터 (`match`는 **해당 방만** 지우고 재매칭) |
| R3 | `poster-classify --room` + 체인 `--no-review` |
| R4 | `similar-detect --room` — **해당 방 멤버가 속한 그룹만** 재구성 (타 방 그룹 유지) |
| R5 | `upload --room` — 후보 rel의 방 필터 |
| S1 | `run_upload=1` 이면 poster-classify **fail-closed** (실패 시 upload 미호출) |
| S2 | 활성 포스터 모델이 있을 때 **classify 행 없음 → upload 제외/hold** |
| S3 | similar-detect 실패 → upload 미호출 (이미 check=True, **테스트·문서 확정**) |
| D1 | 본 문서 + schedule 설계 § 교차 링크 |

### 2.2 나중 (P1 — 이번 PR에서 구현하지 않음)

| ID | 내용 |
|----|------|
| I1 | 증분 evaluation_fingerprint / lease / receipt |
| I2 | similar fingerprint를 photo_id → media SHA |
| I3 | decided_by / decided_at DDL |
| I4 | rebuild 단일 트랜잭션 강화 · 프로세스 실행 lock |
| I5 | 쿠키 만료 시 실행 리포트 JSON + 알림 |
| I6 | poster dataset sync manifest (B2) |
| I7 | **parse/match 증분** (chat `content_sha256` 동일 시 재파싱 스킵 등) — upload 증분과 **별도 축**, 체감 시간↓에 중요할 수 있음 ([incremental §21.4](./incremental-upload-scheduler-design.md)) |

---

## 3. 동작 계약

### 3.1 CLI

**collect**

```text
kakao-pc-collect run --room A --room B --with-upload
→ 수집 A,B 만
→ kakao-import run --room A --room B
→ kakao-import poster-classify --no-review --room A --room B
→ kakao-import similar-detect --room A --room B
→ kakao-import upload --no-dry-run --room A --room B
```

`--room` 생략 = enabled 방 전부 수집 + import **전체**(기존 스케줄).

**import 단독**

```text
kakao-import run --room hongdae_bonita
kakao-import upload --no-dry-run --room hongdae_bonita
```

### 3.2 match (함정)

`cmd_match`는 기존에 `image_group` 등을 **전역 DELETE** 한다.  
방 필터 시 전역 DELETE 하면 **다른 방 매칭이 날아가는 버그**가 된다.

계약: `room_ids`가 있으면

1. 그 방 photo에 연결된 assignment / group_text / image_group 만 삭제  
2. 그 방만 재매칭·INSERT  
3. 다른 방 매칭 유지  

### 3.3 similar (함정)

전역 `DELETE WHERE workspace_key=current` 후 방 사진만 넣으면 **타 방 그룹·decision 소실**.

계약: `room_ids`가 있으면

1. 해당 방 photo_id가 멤버인 그룹만 snapshot → DELETE  
2. 해당 방 사진만 재클러스터  
3. decision 복원(동일 멤버 fingerprint)  
4. 타 방만 있는 그룹은 그대로  

### 3.4 fail 정책

| 단계 | upload OFF | upload ON (`--with-upload` / `RUN_UPLOAD=1`) |
|------|------------|-----------------------------------------------|
| poster-classify 실패 | fail-open (계속) | **fail-closed** — 체인 중단, upload 안 함 |
| similar-detect 실패 | 체인 중단 | 체인 중단 (**upload 안 함**) |
| 활성 모델 + classify 행 없음 | (해당 없음) | **업로드 후보에서 제외 / hold** |
| uncertain | hold | hold |
| deferred similar | hold | hold |

활성 모델이 **없으면** classify는 no-op(기존). 이 경우 미분류 hold는 적용하지 않음(모델 없이 운영 가능 유지).

---

## 4. 수정 파일 맵

### kakao-pc-collect

| 파일 | 변경 |
|------|------|
| `pipeline.py` | `_call_kakao_import(room_ids=…)`, classify fail-closed when upload |
| `cli.py` / README | `--room` = E2E 방 한정 명시 |

### kakao-import-local

| 파일 | 변경 |
|------|------|
| `pipeline.py` | scan/parse/match/hash/run `room_ids` |
| `poster_classify.py` + cli | `--room` |
| `similar_detect.py` + cli | `--room` 부분 rebuild |
| `payload.py` / `upload.py` + cli | `--room` 필터 + 미분류 skip |
| `tests/…` | 방 필터 · match 타방 보존 · upload 필터 · fail-closed |

---

## 5. 테스트 계획

1. `iter_room_layouts`/`scan` 방 필터 — 타 방 로그·upsert 없음  
2. match 방 필터 — 타 방 `image_group` 유지  
3. similar 방 필터 — 타 방 그룹·decision 유지  
4. upload `--room` — 타 방 item 매니페스트 제외  
5. collect `_call_kakao_import` 명령에 `--room` / `--no-review` 포함  
6. `run_upload=True` 이고 classify 실패 시 upload 커맨드 미실행 (mock)  

---

## 6. 수락 기준

- [ ] `--room X --with-upload` 로그에 다른 방 parse/scan이 **나오지 않음**  
- [ ] 같은 실행의 upload에 다른 방 후보가 **포함되지 않음**  
- [ ] 다른 방 similar decision / match 데이터가 **유지**  
- [ ] classify UI로 체인 **정지하지 않음**  
- [ ] upload ON + classify 실패 → upload **미실행**  
- [ ] 활성 모델 + 미분류 sha → **미전송**  
- [ ] `--room` 없는 스케줄 경로 = 기존 전체 동작  

---

## 7. 한 줄 요약

**`--room`은 수집만이 아니라 import·classify·similar·upload까지 같은 방 집합으로 한정한다.**  
자동 업로드를 켠 경우 classify 실패·미분류는 서버로 보내지 않는다.  
완전 증분 큐는 다음 단계(P1)이며, 이번 작업만으로 스케줄 전체 재계산이 사라지지는 않는다.  
방 하나 스모크/부분 실행의 시간과 안전은 이번으로 맞춘다.

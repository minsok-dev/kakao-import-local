# 설계안 — 스케줄 자동 업로드 + 포스터 리뷰 재학습

<!-- [변경사유]: 2026-08-22 — 카카오 스케줄 E2E(upload 연결)·similar hold 유지·포스터 human→dataset→retrain 자동화 설계 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-08-22 |
| 상태 | **P0 구현 완료** (A1–A3·A5–A6, B1·B3–B6). P1 선택(A4 DDL·B2 sync_log·주간 retrain 스케줄)만 미착수 |
| 레포 | `kakao-import-local` (주) · `kakao-pc-collect` (체인 배선) |
| 관련 | [incremental-upload-scheduler-design.md](./incremental-upload-scheduler-design.md) (§20~22 리뷰·classify/similar/parse·시간) · [room-scoped-auto-upload-dev.md](./room-scoped-auto-upload-dev.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [poster-classifier-plan.md](./poster-classifier-plan.md) · [poster-classifier-dev.md](./poster-classifier-dev.md) |
| 운영 절차 | 본 문서 **§11** (로컬 테스트 · 스케줄 · similar 수동) |

---

## 0. 목표 요약

### 작업 A — similar 개입 없이 스케줄이 업로드까지 완료

```text
[스케줄]
collect → run → poster-classify → similar-detect → upload --no-dry-run
                                                      │
                         ┌────────────────────────────┴────────────────────────────┐
                         ▼                                                         ▼
              similar 미소속 / 확정 decision                             similar deferred
              (+ poster 확정분)                                         → hold (업로드 안 함)
              → 서버 전송                                               → 사람이 similar-review 후
                                                                        → upload --no-dry-run 재실행
```

- **자동:** 깨끗·확정 가능한 후보만 업로드  
- **보류:** `similar deferred`(및 기존 `poster uncertain` 등 hold)  
- **금지:** 스케줄에서 `similar-review` / `poster-review` UI 오픈  

### 작업 B — 포스터 리뷰 수정 → AI 재학습 반영

```text
poster-review / poster-label (source=human)
  → (즉시) 업로드 판정 반영          ← 이미 있음
  → (배치) dataset/ 동기화
  → poster-train → (게이트) poster-activate
  → 이후 poster-classify 는 새 모델
```

---

## 1. 현황 (구현 반영)

### 1.1 수집 후 import 체인

`kakao-pc-collect` `_call_kakao_import` (2026-08 이후):

| 단계 | 호출 |
|------|------|
| `kakao-import run` | ✅ (`KAKAO_COLLECT_RUN_IMPORT=1`) |
| `kakao-import poster-classify --no-review` | ✅ (실패 fail-open). **UI 미오픈** — 자동 체인 |
| `kakao-import similar-detect` | ✅ |
| `kakao-import upload --no-dry-run` | ✅ **opt-in** (`KAKAO_COLLECT_RUN_UPLOAD=1` 또는 `--with-upload`). 기본 `0`이면 미호출 |

### 1.2 upload 정책 (이미 존재)

`build_upload_items` → `_apply_similar_policy`:

| similar decision | upload |
|------------------|--------|
| 그룹 미소속 | 후보 유지 → 전송 가능 |
| `deferred` | 전원 skip → `hold_similar_deferred` |
| `same_content` / `partial` | 대표만 (정책 표 따름) |
| `different_content` | 전원 유지 |

→ **“similar는 막고 나머지만 올린다”는 upload 쪽 로직은 이미 있음.**  
collect 쪽 upload 호출은 **`KAKAO_COLLECT_RUN_UPLOAD` opt-in으로 구현 완료** (기본 off).

### 1.3 similar-detect 리스크 (완화됨)

`rebuild_similar_groups`는 여전히 DELETE 후 재생성하지만:

1. DELETE 전 non-`deferred` decision(+partial) **스냅샷**  
2. 멤버 fingerprint 일치 시 **decision 복원**  
3. 불일치·신규 그룹만 `deferred`  

→ 매일 스케줄 detect 후에도 **동일 멤버 집합의 사람 decision은 유지**.

### 1.4 포스터 재학습

| 저장소 | 학습 사용 |
|--------|-----------|
| `poster_classify` (`source=human`) | ❌ (런타임·업로드만) |
| `dataset/poster` · `dataset/non_poster` | ✅ `poster-train` 입력 |

리뷰 → dataset: `poster-dataset-sync` / `poster-retrain-from-review` **구현 완료** (스케줄 의무 아님, 수동/배치).
---

## 2. 작업 A — 상세 설계

### 2.1 목표 운영 모델

| 역할 | 명령 | 대화형 |
|------|------|--------|
| 야간/주간 스케줄 | collect(+upload) 또는 collect 후 upload | 아니오 |
| 사람 | `similar-review` → `upload --no-dry-run` | 예 |
| 점검 | `hold-report` · `upload --dry-run` | 선택 |

[incremental-upload-scheduler-design.md](./incremental-upload-scheduler-design.md) §2와 정합:  
**upload 정책은 manual/scheduler 동일**, 리뷰 UI는 스케줄에서 열지 않음.

### 2.2 체인 배선 (필수) — **완료 후 순차 호출**

시각을 어긋낸 이중 Task Scheduler는 **쓰지 않는다** (수집 종료 시각을 알 수 없음).

#### 기본안: collect 프로세스 안에서 import 완료 직후 upload

`kakao-pc-collect`:

- env: `KAKAO_COLLECT_RUN_UPLOAD=1` (기본 `0` — 기존 수동 흐름 보존)
- CLI: `--with-upload`
- `_call_kakao_import` 순서:

```text
kakao-import run
kakao-import poster-classify --no-review
kakao-import similar-detect
kakao-import upload --no-dry-run    # RUN_UPLOAD=1 일 때만, 위 완료 직후
```

- upload 실패 시: 로그 + non-zero (hold만 있고 전송 성공인 경우는 upload 쪽 exit 0)

스케줄은 **작업 하나만** 등록:

```text
kakao-pc-collect run
(+ .env 에 KAKAO_COLLECT_RUN_UPLOAD=1)
```

#### (비권장) 시각 분리

수집 시간이 가변이라 upload를 “N시간 뒤”로 두면 겹치거나 너무 늦음 → **채택하지 않음**.

#### (대안) .cmd 순차

```bat
kakao-pc-collect run
kakao-import upload --no-dry-run
```

코드 opt-in과 동일 효과. 스케줄은 이 `.cmd` 하나만 실행.

### 2.3 전제 조건 (운영)

| 항목 | 내용 |
|------|------|
| endpoint | `KAKAO_IMPORT_ENDPOINT` 또는 기본 import URL |
| cookie | `KAKAO_IMPORT_SESSION_COOKIE` (만료 시 upload 실패 → 알림/로그) |
| DB | `kakao_local.db` · watermarks · data 경로 |
| poster 모델 | 활성 모델 없으면 classify skip (현행) |

### 2.4 similar decision 보존 (필수 보완)

#### 문제

스케줄마다 `similar-detect` → 전량 DELETE → 전부 `deferred`  
→ 리뷰한 그룹이 다시 hold → 자동 업로드 대상이 흔들림.

#### 보존 키 (제안)

그룹 멤버 `photo_id` 집합을 정규화한 fingerprint:

```text
group_fingerprint = sha1( sorted(photo_id).join(",") )
```

또는 기존 `group_key`가 멤버 안정적이면 그 키 + workspace.

#### 알고리즘 (rebuild 시)

```text
1. 기존 그룹 SELECT (workspace_key, decision ≠ deferred 이거나 사람이 둔 것)
   → map[fingerprint] = { decision, representative_photo_id, partial subgroups… }

2. DELETE members/groups (현행)

3. 새 클러스터 INSERT
   - fingerprint가 map에 있고 멤버 집합이 동일(또는 허용 오차 내)이면
     → 이전 decision / representative 복원
   - 신규·멤버 변경 그룹만 deferred

4. 로그: restored=N, new_deferred=M
```

#### “사람이 둔 것” 판별

| 방안 | 내용 |
|------|------|
| B1 (단순) | `decision != 'deferred'` 만 보존 |
| B2 (권장) | `decided_at` / `decided_by='human'` 컬럼 추가 후 human만 보존 |

스키마 변경 시:

```sql
-- 예: sql/0xx_similar_decision_audit.sql
ALTER TABLE similar_image_group ADD COLUMN decided_at TEXT;
ALTER TABLE similar_image_group ADD COLUMN decided_by TEXT; -- 'human' | 'system'
```

`set_similar_group_decision` 시 `decided_by='human'`, `decided_at=now`.  
rebuild 복원 시 human만 우선.

#### 멤버가 늘어난 경우

- 이전 멤버 ⊂ 새 멤버: **deferred로 리셋** (사람 재확인) — 안전 기본  
- 또는 partial 유지 정책은 Phase 후속 (1차에서는 리셋)

### 2.5 upload 동작 (변경 최소)

`upload --no-dry-run` **정책 코드는 수정하지 않는 것을 1차 원칙**으로 함.

스케줄이 호출만 하면:

1. 미소속·확정분 → HTTP 업로드  
2. `deferred` → hold 기록, 전송 안 함  
3. hold-only면 실패로 치지 않음 (기존 설계 §2.2)

### 2.6 사람 후속 루프 (변경 없음, 문서화)

```text
kakao-import hold-report
kakao-import similar-review          # 또는 similar-decide
kakao-import upload --no-dry-run    # hold 해제분 전송
```

decide만으로는 서버 반영 안 됨 — **반드시 upload 재실행**.

### 2.7 스케줄 예시 (Windows) — **단일 작업**

```text
프로그램: kakao-pc-collect (venv Scripts)
인수: run
환경 / .env:
  KAKAO_COLLECT_RUN_IMPORT=1
  KAKAO_COLLECT_RUN_UPLOAD=1
  (+ kakao-import-local .env 쿠키·endpoint)
시작 위치: kakao-pc-collect 루트
```

수집이 몇 시간이 걸려도, import 체인 끝난 **직후** upload가 이어진다.

### 2.8 작업 A — 구현 체크리스트

| ID | 항목 | 레포 | 우선 | 상태 |
|----|------|------|------|------|
| A1 | `KAKAO_COLLECT_RUN_UPLOAD` / `--with-upload` + 완료 직후 upload | collect | P0 | ✅ |
| A2 | README / 설계: 완료 후 순차 (시각 분리 비채택) | 양쪽 | P0 | ✅ |
| A3 | similar rebuild decision 보존 | import | P0 | ✅ |
| A4 | (선택) `decided_by` / `decided_at` DDL | import | P1 | 미착수 (B1으로 충분) |
| A5 | 테스트: deferred hold + 미소속 upload dry-run | import | P0 | 기존 upload 정책 + 신규 preserve 테스트 ✅ |
| A6 | 테스트: rebuild 후 human decision 유지 | import | P0 | ✅ |

### 2.9 작업 A — 비목표 (1차)

- similar-review UI 자동화 / 헤드리스 “자동 same_content”  
- `--force`로 간격·커서 무시 (SNS와 무관, 카카오 upload에 해당 없음)  
- 서버 SNS merge와 로컬 similar decision 동일시  

---

## 3. 작업 B — 포스터 리뷰 → 재학습 상세 설계

### 3.1 목표

사용자가 `poster-review` / `poster-label`로 고친 결과가:

1. **즉시** 업로드·classify 보호에 반영 (현행 유지)  
2. **이후** 모델 학습 데이터에 들어가, 다음 활성 모델이 같은 실수를 줄임  

### 3.2 원칙

| 원칙 | 내용 |
|------|------|
| 원본 photos 불변 | dataset에는 **복사**만 (`dataset/…`) |
| human 우선 | `poster-classify`는 `source=human` 덮지 않음 (현행) |
| train ≠ activate | train만 하고 활성은 게이트 후 (현행 `poster-activate`) |
| classify와 분리 | 야간 collect 직후 무거운 train 금지 — **별도 배치** |
| uncertain | 학습 2클래스에 넣지 않음 (스킵) |

### 3.3 데이터 흐름

```text
[즉시 — 이미 구현]
  poster-label
    → poster_classify (source=human, status=poster|non_poster|uncertain)
    → mark needs_rebuild (upload 후보)

[배치 — 신규]
  poster-dataset-sync
    → SELECT source=human AND status IN ('poster','non_poster')
    → 원본 경로 resolve (room photos)
    → copy → dataset/poster/ 또는 dataset/non_poster/
    → (선택) sync_log 테이블에 sha, status, synced_at

  poster-train
    → dataset 스캔 (현행)
    → models/poster-clip-vN.{joblib,json}

  poster-activate --version N
    → false_exclude 게이트
    → active-model.json
```

### 3.4 `poster-dataset-sync` 설계

#### 입력

- SQLite `poster_classify`  
- 조건: `source = 'human'` AND `status IN ('poster','non_poster')`  
- (선택) `synced_at IS NULL` 또는 mtime/sha 변경분만  

#### 출력 파일명 규칙 (제안)

```text
dataset/{poster|non_poster}/{room_id}__{sha256_12}__{orig_basename}
```

- 동일 sha가 반대 폴더에 있으면 **이동/삭제 후** 올바른 폴더에 복사 (라벨 번복 대응)  
- 원본 없으면 skip + 로그  

#### CLI

```bash
kakao-import poster-dataset-sync
kakao-import poster-dataset-sync --since=2026-08-01
kakao-import poster-retrain-from-review   # sync + train (activate는 기본 수동)
kakao-import poster-retrain-from-review --activate  # 게이트 통과 시에만 activate
```

### 3.5 트리거 정책

| 모드 | 동작 | 권장 |
|------|------|------|
| T0 수동 | 리뷰 후 운영자가 sync + train | 1차 |
| T1 배치 스케줄 | 주 1회 `poster-retrain-from-review` | 2차 |
| T2 라벨 직후 | label마다 sync만 (train은 배치) | 선택 |
| T3 자동 activate | train 후 게이트 OK면 activate | 신중 (기본 off) |

**1차 권장:** T0 + CLI 완비 → 안정화 후 T1.  
T3는 `false_exclude` 게이트 실패 시 activate 금지·리포트만.

### 3.6 uncertain / 번복

- `uncertain` human: dataset에 넣지 않음. 런타임 hold만.  
- poster ↔ non_poster 번복: sync가 반대 폴더 정리 후 재복사.  
- 최소 학습 장수: 현행 `poster-train` 규칙 유지 (미달 시 train 거부).

### 3.7 작업 B — 구현 체크리스트

| ID | 항목 | 우선 |
|----|------|------|
| B1 | `poster_dataset_sync.py` + CLI | P0 | ✅ |
| B2 | (선택) `poster_sync_log` 또는 classify에 `dataset_synced_at` | P1 | 미착수 |
| B3 | `poster-retrain-from-review` = sync + train | P0 | ✅ |
| B4 | `--activate` 옵션 + 게이트 | P1 | ✅ (`--activate` / `--activate-force`) |
| B5 | 테스트: human label → dataset 파일 존재 | P0 | ✅ |
| B6 | 문서: poster-classifier-dev C3 자동화 절 | P0 | 설계 §3·§10 |
| B7 | 주간 Task Scheduler 예시 | P2 | 운영 선택 |

### 3.8 작업 B — 비목표 (1차)

- OpenCLIP 자체 fine-tune (로지스틱 헤드만 재학습 — 현행과 동일)  
- backend/서버 연동  
- uncertain을 학습 클래스로 추가  
- collect 체인에 train 삽입  

---

## 4. 전체 목표 파이프라인 (수정 후)

### 4.1 매일/정기 스케줄 (무인)

```text
kakao-pc-collect run
  ├─ (수집)
  ├─ kakao-import run
  ├─ poster-classify          # 활성 모델
  ├─ similar-detect          # decision 보존 rebuild
  └─ upload --no-dry-run     # NEW (opt-in)
        ├─ ready → 서버
        └─ similar deferred / poster uncertain → hold
```

### 4.2 사람 (수시)

```text
similar-review  → upload --no-dry-run
poster-review   → (즉시 human) → (주간) poster-retrain-from-review [--activate]
hold-report
```

### 4.3 주간 (선택)

```text
kakao-import poster-retrain-from-review
# 또는 --activate
```

---

## 5. 리스크와 완화

| 리스크 | 완화 |
|--------|------|
| 쿠키 만료로 upload 실패 | 로그·exit≠0, hold와 구분; 쿠키 갱신 절차 문서화 |
| similar rebuild가 리뷰 삭제 | A3 decision 보존 |
| force성 전체 재업로드 | upload 정책 유지, collect에 `--force` 개념 없음 |
| train이 collect를 막음 | train은 별도 스케줄 |
| 잘못된 human이 모델 오염 | activate 게이트; human 런타임 덮어쓰기 금지 유지 |
| 이중 스케줄(서버+로컬) | 카카오는 로컬 DB; SNS due와 무관 |

---

## 6. 구현 단계 (권장 순서)

### Phase S1 — 자동 업로드 배선 (작업 A P0) ✅

1. collect: import **완료 직후** upload opt-in (`KAKAO_COLLECT_RUN_UPLOAD` / `--with-upload`)  
2. 문서: 시각 분리 스케줄 비채택  
3. 스모크: 운영에서 `RUN_UPLOAD=1` 후 hold/업로드 확인 (권장)  

### Phase S2 — similar decision 보존 (작업 A P0) ✅

1. rebuild 시 human/`non-deferred` 복원  
2. 테스트  

### Phase S3 — 포스터 sync + retrain CLI (작업 B P0) ✅

1. dataset-sync  
2. retrain-from-review  
3. 문서 §3·§10  

### Phase S4 — 운영 자동화 (P1~P2) — 선택

1. decided_by DDL  
2. 주간 retrain 스케줄  
3. `--activate`는 CLI에 이미 있음 (게이트 운영 정책만)  

---

## 7. 수락 기준 (Acceptance)

### 작업 A

- [x] `KAKAO_COLLECT_RUN_UPLOAD=1` (또는 동등)일 때 collect 한 번으로 **upload까지** 실행  
- [x] similar `deferred` 멤버는 서버에 안 올라가고 hold에 남음 (기존 upload 정책)  
- [x] 미소속·확정분은 `--no-dry-run`으로 업로드됨 (기존 upload 정책)  
- [x] similar-detect 재실행 후에도 **사람 decision이 유지**됨 (동일 멤버 집합)  
- [x] 기본값(upload off)에서는 **기존과 동일**하게 upload 미호출  

### 작업 B

- [x] human `poster`/`non_poster` label 후 `poster-dataset-sync` 시 dataset 해당 폴더에 파일 생김  
- [x] `poster-retrain-from-review`가 그 dataset으로 새 `poster-clip-vN` 생성 (CLI; 실제 train은 `.[poster]`)  
- [x] activate 전후로 human 행은 model classify에 덮이지 않음 (기존 정책)  
- [x] uncertain은 dataset에 들어가지 않음  

---

## 8. 수정 파일 맵 (예상)

### kakao-pc-collect

| 파일 | 변경 |
|------|------|
| `src/kakao_pc_collect/pipeline.py` | `_call_kakao_import`에 upload 단계 |
| `src/kakao_pc_collect/config.py` / `.env.example` | `KAKAO_COLLECT_RUN_UPLOAD` |
| `src/kakao_pc_collect/cli.py` | `--with-upload` (선택) |
| `README.md` | 스케줄·체인 설명 |

### kakao-import-local

| 파일 | 변경 |
|------|------|
| `src/kakao_import/similar_detect.py` | rebuild 시 decision 보존 |
| `sql/0xx_similar_decision_audit.sql` | (P1) decided_at/by |
| `src/kakao_import/poster_dataset_sync.py` | **신규** |
| `src/kakao_import/poster_label.py` | (선택) sync 훅 |
| `src/kakao_import/cli.py` | sync / retrain-from-review |
| `tests/…` | A·B 스모크 |
| `docs/poster-classifier-dev.md` | C3 자동화 |
| 본 문서 | 설계 원본 |

---

## 10. 운영 서버 git pull 시 설정 변경점

코드만 pull 하면 되고, **필수 신규 시크릿/서버 설정은 없음**.  
동작이 바뀌는 것은 **opt-in env** 뿐이다.

### kakao-pc-collect

| 항목 | 기본 | 운영에서 할 일 |
|------|------|----------------|
| `KAKAO_COLLECT_RUN_UPLOAD` | `0` (기존과 동일, upload 안 함) | 스케줄 자동 업로드 쓰려면 `.env`에 `=1` |
| `--with-upload` | 없음 | 수동 1회 시험용 |
| `KAKAO_COLLECT_RUN_IMPORT` | 기존과 동일 | 변경 없음 |

### kakao-import-local

| 항목 | 기본 | 운영에서 할 일 |
|------|------|----------------|
| similar decision 보존 | **항상 on** (코드) | 설정 불필요. detect 재실행 시 non-deferred 유지 |
| `poster-dataset-sync` / `poster-retrain-from-review` | 수동 CLI | 원할 때 실행. 스케줄 의무 아님 |
| `KAKAO_IMPORT_SESSION_COOKIE` / endpoint | 기존 | upload 켤 때 만료만 점검 |
| DB 마이그 | 없음 (decision 보존에 DDL 불필요) | SQL 추가 실행 없음 |

### 스케줄러

- 작업 **1개**: `kakao-pc-collect run` + `.env`에 `KAKAO_COLLECT_RUN_UPLOAD=1`
- upload 전용 시각 분리 작업 **추가하지 않음**
- 사람: `similar-review` → `upload --no-dry-run` (기존)

### pull 후 체크리스트

1. `git pull` (collect + import-local)
2. (선택) `pip install -e .` / `.[poster]` — 새 모듈만 있으면 보통 불필요
3. 스케줄 PC `.env`에 `KAKAO_COLLECT_RUN_UPLOAD=1` **명시적으로 추가**
4. 쿠키 유효성 확인
5. 한 번 `kakao-pc-collect run --with-upload` 스모크

**주의:** env를 안 건드리면 upload는 예전처럼 안 돈다 (안전 기본값).

---

## 11. 운영 절차 — 로컬 테스트 · 스케줄 · similar 수동

### 11.1 사전 조건

| 항목 | 내용 |
|------|------|
| 카톡 PC | 로그인된 상태 (collect 실수집 시) |
| collect `.env` | `KAKAO_IMPORT_ROOT`, `KAKAO_DOWNLOAD_DIR`, `KAKAO_COLLECT_RUN_IMPORT=1` |
| import `.env` | `KAKAO_EXPORT_ROOT`, `KAKAO_LOCAL_DB` |
| 실전송 시만 | import `.env`에 `KAKAO_IMPORT_ENDPOINT` + `KAKAO_IMPORT_SESSION_COOKIE` |
| similar signature | `pip install -e ../backend/packages/danceinfo_image_signature` (FastAPI 서버 불필요) |

경로 예 (로컬):

```text
kakao-pc-collect/.env
  KAKAO_IMPORT_ROOT=F:/site_kdance/TEST_web/kakao-import-local
  KAKAO_DOWNLOAD_DIR=<실제 카톡 "받은 파일" 폴더>
  KAKAO_COLLECT_RUN_IMPORT=1
  KAKAO_COLLECT_RUN_UPLOAD=0          # 로컬 기본: upload 끔

kakao-import-local/.env
  KAKAO_EXPORT_ROOT=./input/raw
  KAKAO_LOCAL_DB=./data/kakao_local.db
  # 실업로드 테스트할 때만:
  # KAKAO_IMPORT_ENDPOINT=https://danceinfo.net/api/admin/ingest/import/kakao
  # KAKAO_IMPORT_SESSION_COOKIE=...
```

### 11.2 로컬 테스트 명령과 실제 동작

venv 활성화 후 각 레포에서 실행.

#### A) 수집만 (import/upload 없음)

```powershell
cd F:\site_kdance\TEST_web\kakao-pc-collect
.\.venv\Scripts\Activate.ps1
kakao-pc-collect run --room <room_id> --no-import
```

- 카톡 UI로 txt/사진만 `input/raw/<room>/`에 쌓음.
- `run` / `poster-classify` / `similar-detect` / `upload` **전부 안 함**.

#### B) 수집 + import 체인 (upload 없음) — 기본

```powershell
# .env: KAKAO_COLLECT_RUN_UPLOAD=0 (또는 미설정)
kakao-pc-collect run --room <room_id>
```

실제 순서:

1. 수집  
2. `kakao-import run` (scan→…→match 등)  
3. `kakao-import poster-classify --no-review` (UI 없음)  
4. `kakao-import similar-detect` (decision 보존 rebuild)  
5. **upload 안 함**

#### C) 수집 + import + 실업로드 (로컬 1회 스모크)

```powershell
# 방법 1: CLI 플래그 (env 안 바꿔도 됨)
kakao-pc-collect run --room <room_id> --with-upload

# 방법 2: .env 에 KAKAO_COLLECT_RUN_UPLOAD=1 후
kakao-pc-collect run --room <room_id>
```

실제 순서: B의 1–4 후

5. `kakao-import upload --no-dry-run`  
   - 미소속·확정 decision → 서버 전송  
   - `deferred` → `hold_similar_deferred` (전송 안 함)  
   - poster uncertain 등 기존 hold도 그대로 hold  

**쿠키/endpoint 없으면 이 단계에서 실패.** dry-run만 보려면 import 쪽에서 직접:

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
.\.venv\Scripts\Activate.ps1
kakao-import upload              # 기본 --dry-run (전송 없음, 후보/hold 요약)
kakao-import hold-report        # hold_* 현황
```

#### D) import만 (이미 파일이 있을 때)

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
.\.venv\Scripts\Activate.ps1
kakao-import run
kakao-import poster-classify --no-review
kakao-import similar-detect
kakao-import upload                # dry-run
# kakao-import upload --no-dry-run  # 실전송
```

#### E) 단위 테스트 (UI/카톡 불필요)

```powershell
cd kakao-import-local
.\.venv\Scripts\python.exe -m pytest tests/test_phase4_similar.py tests/test_poster_dataset_sync.py -q

cd kakao-pc-collect
.\.venv\Scripts\python.exe -m pytest tests/test_run_upload_setting.py -q
```

### 11.3 Windows 작업 스케줄러 등록

**원칙:** 작업 **1개**. upload 전용 시각 분리 금지.  
프로그램은 `.py`/스크립트 직접이 아니라 **venv의 python.exe**.

| 항목 | 값 예 |
|------|--------|
| 프로그램 | `F:\site_kdance\TEST_web\kakao-pc-collect\.venv\Scripts\python.exe` |
| 인수 | `-m kakao_pc_collect run` 또는 `F:\...\Scripts\kakao-pc-collect.exe run` |
| 시작 위치 | `F:\site_kdance\TEST_web\kakao-pc-collect` |
| 환경 | 해당 폴더 `.env`에 `KAKAO_COLLECT_RUN_IMPORT=1`, **`KAKAO_COLLECT_RUN_UPLOAD=1`** |
| 조건 | 카톡 PC 로그인·화면 잠금 정책은 기존 collect와 동일 |

스케줄 1회 실행 시 동작 = §11.2 C (수집→run→poster→similar-detect→upload --no-dry-run).  
**스케줄은 `similar-review` / `poster-review`를 열지 않음.**

### 11.4 스케줄 밖 — similar 사람 처리

similar는 **탐지만 자동**, **관계 확정은 사람**. decision만 저장하고, 전송은 이후 `upload`가 한다.

```text
[자동/스케줄]                    [사람]                         [사람 또는 다음 스케줄]
similar-detect                   similar-review                 upload --no-dry-run
(그룹 만들고 deferred 기본)      (decision 저장만)               (확정분만 전송, deferred hold)
```

#### 1) 보류 현황 보기

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
.\.venv\Scripts\Activate.ps1
kakao-import hold-report
kakao-import similar-list
```

#### 2) 리뷰 UI (권장)

```powershell
kakao-import similar-review
# → http://127.0.0.1:8765/
```

UI에서 그룹마다:

| 선택 | 의미 | 이후 upload |
|------|------|-------------|
| 같은 콘텐츠 (`same_content`) | 한 후보 | 대표 1장만 |
| 다른 콘텐츠 (`different_content`) | 각자 | 멤버 전부 |
| 부분 (`partial`) | 서브그룹 혼합 | 묶음 대표 + 단독 |
| 보류 (`deferred`) | 미결정 | **계속 hold** |

저장만 되고 **이 명령은 서버에 올리지 않음.**

#### 3) CLI로 decision만 (UI 없이)

```powershell
kakao-import similar-decide --group-id 14 --decision same_content
kakao-import similar-decide --group-id 14 --decision different_content
kakao-import similar-decide --group-id 14 --decision deferred
```

#### 4) 리뷰 후 업로드

```powershell
kakao-import upload                 # dry-run으로 건수·hold 확인
kakao-import upload --no-dry-run   # 확정분 실전송
```

다음 스케줄(`RUN_UPLOAD=1`)이 돌아도, 아직 `deferred`인 것은 계속 hold.  
멤버 집합이 같으면 `similar-detect` 재실행해도 **non-deferred decision은 유지**된다.


# 로컬 포스터 분류기 — 개발 계획서

<!-- [변경사유]: 2026-08-18 — 계약(poster-classifier-plan)을 CLI·스키마·단계·완료 조건으로 쪼갬. 코드 미착수 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-08-18 |
| 상태 | **C0~C2 구현 · v1 학습 완료** (계약 [poster-classifier-plan.md](./poster-classifier-plan.md)) |
| 레포 | `kakao-import-local` |
| 계약 | [poster-classifier-plan.md](./poster-classifier-plan.md) (정책 원본. 이 문서와 충돌하면 **계약이 우선**) |
| 전제 | 운영자가 `dataset/poster` · `dataset/non_poster` 를 모으는 동안, 구현은 C0→C1부터 진행 가능 |

similar-review(같은 그림인지)와 **다른 축**이다. Phase 3.5·4 본선을 막지 않는 **병렬 트랙**.

---

## 0. 역할 분담

| 누가 | 한다 | 하지 않는다 |
|------|------|-------------|
| 운영자 | `dataset/poster` · `dataset/non_poster` 복사본 정리 (원본 `photos/` 불변) | CLIP·torch 단독 설치 |
| 개발 | 패키지 extra, CLI, SQLite, 캐시, upload 제외, 테스트 | 원본 이동·삭제, 유료 API |

torch / OpenCLIP은 **코드가 extra로 넣을 때** 같이 받는다. 운영자가 다른 CLIP을 미리 받으면 체크포인트가 어긋날 수 있다.

데이터 두는 법: [dataset/README.md](../dataset/README.md)

---

## 1. 한 줄 목표

`kakao-import run` 이후, SHA가 있는 사진에 대해 **확실한 비포스터만** 업로드 큐에서 빼되  
`input/raw/<room_id>/photos` 파일은 손대지 않는다.

```text
kakao-pc-collect
  → kakao-import run              # 기존 (scan→parse→match→hash→merge)
  → kakao-import poster-classify  # 신규 (활성 모델 있을 때만)
  → kakao-import similar-detect   # 기존
  → similar-review → upload
```

C2 전에는 `poster-classify`가 없어도 오늘과 동일하게 동작해야 한다.

---

## 2. 하지 말 것 (전 단계 공통)

- `photos/` 이동·삭제·리네임
- `exact_sha_member.excluded_from_upload` 에 포스터 판정을 덮어쓰기  
  (그 컬럼은 **동일 SHA 중복 장** 전용. hash가 매번 재작성함)
- 파일명만의 `ignore_list.json`
- 임계값 `0.9` 등을 코드에 박아 두기
- 키워드(“살사” 있으면 포스터) 즉시 확정
- CLIP fine-tune, OCR 본선, similar-review를 포스터 UI로 쓰기
- 기본 `pip install -e .` 에 torch를 넣기 (용량). **optional extra만**

---

## 3. 단계 개요

| ID | 이름 | 데이터셋 필수 | 산출 |
|----|------|:------------:|------|
| **C0** | 골격 | 아니오 | extra · 빈 폴더 · SQL · CLI stub · gitignore |
| **C1** | train / classify CLI | **예** (시험 각 80장+) | embedding 캐시 · 로지스틱 · 모델 파일 · 고정 test 리포트 |
| **C2** | import 연동 | C1 활성 모델 | hash 이후 판정 → upload·similar에서 `non_poster` 제외 |
| **C3** | 사람 확정 · 재학습 활성화 | 오분류 추가분 | `poster-label` · human > model · test 통과 시에만 activate |
| **C4** | OCR 보조 | C3 이후, uncertain이 계속 많을 때만 | 비범위. 착수 전 계약 갱신 |

권장 순서: **C0 (데이터와 병렬) → 데이터 도착 → C1 → C2 → C3**.  
C2를 C1보다 먼저 넣지 않는다. 모델 없이 연동하면 전부 fail-open이라 의미가 없다.

---

## 4. 의존성 · 모델

`pyproject.toml` extra `poster` (이름 고정):

```text
torch
open_clip_torch
scikit-learn
pillow
joblib
numpy
```

설치:

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
.\.venv\Scripts\Activate.ps1
pip install -e ".[poster]"
```

| 항목 | v1 고정값 (C0에서 상수로 명시) |
|------|-------------------------------|
| OpenCLIP 모델 | `ViT-B-32` |
| pretrained | `laion2b_s34b_b79k` |
| 장치 | CPU 기본. CUDA 있으면 사용해도 되나 필수는 아님 |
| 분류기 | 로지스틱 회귀 (sklearn). CLIP 가중치는 **동결** |

체크포인트는 첫 `poster-train` / `poster-classify` 때 `open_clip`이 받는다. 별도 수동 다운로드 절차 없음.

pytest **기본 스위트는 torch 없이** 통과. CLIP 호출은 stub/픽스처. `pytest -m poster` 만 extra 필요 (C1).

---

## 5. 경로 · git

```text
kakao-import-local/
  dataset/
    README.md
    poster/           # 운영자 복사본 (gitignore)
    non_poster/
  data/
    poster_embed/     # sha256 → vector 캐시 (이미 data/ gitignore)
  models/
    poster-clip-vN.joblib
    poster-clip-vN.json
    active-model.json
  sql/007_poster_classify.sql
  src/kakao_import/
    poster_schema.py
    poster_embed.py
    poster_train.py
    poster_classify.py
    poster_label.py
  tests/test_poster_classify.py
```

`.gitignore`에 `dataset/poster/**`, `dataset/non_poster/**`, `models/*.joblib` 추가.  
README · `.gitkeep` · `*.json` 메타는 커밋 가능.

원본 `input/raw/` 는 기존처럼 gitignore. 학습은 **dataset 복사본만**.

---

## 6. 스키마 (C0)

신규 `sql/007_poster_classify.sql`. `init_schema`에 기존 004~006과 같이 연결.  
`exact_sha_member`는 변경하지 않는다.

```text
poster_embedding
  sha256 + clip_model   PK
  dim, vector(BLOB float32), created_at

poster_classify
  UNIQUE(room_id, sha256)
  photo_id, rel_path, file_name
  model_version
  poster_score          -- 포스터일 점수 0~1 (모델일 때)
  status                -- poster | uncertain | non_poster
  source                -- model | human | rule
  excluded_from_upload  -- non_poster 이면 1 (이 테이블 전용)
  classified_at

poster_model_meta
  version PK
  clip_model, exclude_threshold, metrics_json, created_at
```

판정 재사용 키: **`room_id` + sha256**.  
`room_id`는 기존 `room_id_from_rel(rel_path)`와 동일.

**사람 행:** `source='human'` 이면 모델이 다시 돌아도 덮지 않음.  
모델 갱신은 `source='model'|'rule'` 만.

---

## 7. CLI (C0 stub → C1~C3에서 채움)

기존 `kakao-import` 그룹에 추가. 신규 엔트리포인트 없음.

| 명령 | 단계 | 역할 |
|------|------|------|
| `poster-train` | C1 | dataset 스캔 → embedding 캐시 → 그룹 단위 split → 로지스틱 학습 → `models/poster-clip-vN.*` (활성은 아직 아님) |
| `poster-classify` | C1 단독 / C2 연동 | photo_file(sha 있음) 판정 기록. 활성 모델 없으면 no-op + 로그 |
| `poster-activate --version vN` | C3 (C1은 리포트만) | 고정 test 게이트 통과 시에만 `active-model.json` |
| `poster-label --sha … --status poster\|non_poster` | C3 | human 확정 |
| `poster-dataset-sync` | C3 자동화 | human `poster`/`non_poster` → `dataset/` 복사 (uncertain 제외) |
| `poster-retrain-from-review` | C3 자동화 | sync + `poster-train` (+ 선택 `--activate` / `--activate-force`) |

로그: `kakao_import.logging_util`. 쿼리·학습 장수·오제외 건수는 **반드시 logger**.

`cmd_run`에 classify를 **넣지 않는다** (C2). collect/운영이 `run` 다음 명시 호출.  
`kakao-pc-collect`는 수집 성공 시 `poster-classify`를 체인에 포함함.  
human→dataset→retrain: [schedule-auto-upload-and-poster-retrain-design.md](./schedule-auto-upload-and-poster-retrain-design.md) §3·§11.

---

## 8. C0 — 골격 (데이터 없이 착수)

1. extra `poster`, `.gitignore`, `dataset/` README·gitkeep  
2. `007` + `init_schema`  
3. CLI 네 명령: extra 없으면 “`pip install -e .[poster]` 필요” 하고 종료 (exit 0 또는 2 — C0에서 하나로 고정)  
4. `tests/test_poster_classify.py`: 스키마 적용, **파일을 만들지 않음** 가드 (임시 디렉터리 복사본으로 확인)

완료: torch 없이 `pytest` 기존+신규 통과.

---

## 9. C1 — train / classify (데이터셋 필요)

운영자 완료 조건: 각 폴더 **80장 이상**, 종류가 섞일 것.  
식탁만 / 세로 포스터만이면 착수해도 v1 품질을 보장하지 않는다고 리포트에 적는다.

### 9.1 train

1. `dataset/poster` = 긍정, `dataset/non_poster` = 부정. 원본 `raw` 스캔 금지.  
2. 이미지 SHA-256. 캐시에 있으면 CLIP 생략.  
3. **그룹 단위 split** (파일 무작위 금지):  
   같은 SHA · 가까운 pHash · 같은 `KakaoTalk_` 앨범 스템 · 가능하면 날짜(과거 train / 최근 test)  
   한 그룹은 train 또는 test 전부.  
4. train만 로지스틱 적합.  
5. test에서 **포스터 → non_poster 건수(오제외)** 를 1순위 지표로 저장.  
6. `exclude_threshold`: test 오제외가 0(또는 합의한 상한)인 점수. 코드 상수 0.9 금지.  
7. 산출: `models/poster-clip-vN.joblib` + json (clip명, 장수, 오제외, threshold, 시각). **active는 수동/게이트 후.**

pHash는 기존 similar용 패키지가 있으면 재사용, 없으면 imagehash 또는 단순 근사. C1에서 한 가지로 고정.

### 9.2 classify (CLI만, upload 미연결)

- 입력: DB `photo_file` (sha 있음) 또는 `--root` 스캔.  
- 출력: `poster_classify` upsert.  
- 점수 ≥ 포스터 쪽 충분 → `poster`  
- 점수 ≤ exclude_threshold → `non_poster`  
- 사이 → `uncertain`  
- CLIP/디코드 실패 → 행을 안 넣거나 `poster` 취급 **제외하지 않음** (fail-open) + 로그  
- 작은 이미지 rule: 한 변 64px 미만만 `source=rule` 후보. 가로 500px 컷 금지.

완료: `poster-train`이 json 리포트를 남기고, 샘플 몇 장 `poster-classify`가 DB에 기록. upload 큐는 아직 그대로.

---

## 10. C2 — import 연동

전제: `active-model.json` 존재.

1. `poster-classify`가 hash 이후 사진에 대해 upsert (human 유지).  
2. `build_upload_items`: 대표 사진이 `poster_classify.excluded_from_upload=1` 이면 스킵.  
   exact SHA 제외와 **AND**. 한쪽만으로 포스터를 지우지 말 것.  
3. `similar-detect`: exact 제외와 같이, 이 플래그 1인 장은 그룹에 넣지 않음.  
4. 활성 모델 없음 / extra 없음 / 분류 예외 → 오늘과 동일 통과.  
5. `kakao-pc-collect` 성공 훅: `run`과 `similar-detect` **사이**에 `poster-classify` (실패해도 collect는 중단하지 않음).

완료: dry-run에서 확실한 잡사진 SHA가 항목에 없고, 포스터 SHA는 남아 있음.  
golden ESENCIA 매칭 테스트는 분류 extra 없이 통과.

---

## 11. C3 — 사람 확정 · 활성화

1. `poster-label`: `source=human`, status에 따라 excluded 플래그. 이후 모델 재실행이 덮지 않음.  
2. 오분류 장은 **dataset 해당 폴더에 복사**한 뒤 `poster-train` (전체 로지스틱 재학습, 점진 학습 없음).  
3. `poster-activate`: 고정 test에서 v_n 대비 포스터 오제외가 **늘지 않을 때만** 활성 교체. 실패 시 이전 활성 유지.  
4. 리포트: train/test 장수, 오제외, threshold.

완료: 잘못된 잡사진 제외를 사람이 되돌리면 다음 classify·upload에 즉시 반영.

---

## 12. C4 — 보류

OCR 글자 밀도는 embedding **옆 특징**으로만. 키워드 즉시 확정 금지.  
uncertain 비율이 운영상 부담일 때 계약 문단을 고친 뒤 착수.

---

## 13. 테스트 전략

| 종류 | 내용 | torch |
|------|------|:-----:|
| 기본 | 스키마, human 덮어쓰기 금지, upload 필터 AND, 파일 미이동, fail-open | 아니오 |
| 그룹 split | 같은 SHA/앨범이 train·test에 동시에 안 들어감 | 아니오 (가짜 벡터) |
| extra | 짧은 픽스처로 train→threshold json 필드 존재 | 예 (`-m poster`) |

임시 디렉터리만 사용. 운영 `photos/` · `dataset/` 실파일을 테스트가 지우지 않음.

---

## 14. 운영 흐름 (C2 이후)

```text
# 최초 1회
pip install -e ".[poster]"
kakao-import poster-train
kakao-import poster-activate --version poster-clip-v1   # 게이트 통과 시

# 수집 후 (collect 훅 또는 수동)
kakao-import run
kakao-import poster-classify
kakao-import similar-detect
kakao-import similar-review
kakao-import upload --dry-run
```

틀린 장 → dataset에 복사 → `poster-train` → test 확인 → `poster-activate`.

---

## 15. 다른 PC로 이전

<!-- [변경사유]: 추후 재문의 없이도 이전 절차를 바로 확인할 수 있도록 정리 -->

### 15.1 새 PC에서 다시 설치

```powershell
cd F:\site_kdance\TEST_web\kakao-import-local
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[poster]"
```

- `torch` + `open_clip_torch` 는 **가상환경에 로컬 설치**한다.
- CLIP은 API 호출이 아니라, 첫 실행 때 **모델 가중치 파일을 1회 다운로드**한 뒤 로컬에서 읽는다.
- `.venv/` 는 경로가 묶이므로 **다른 PC로 복사하지 않는다**.

### 15.2 직접 복사할 것

| 경로 | 이유 |
|------|------|
| `models/poster-clip-v*.joblib` | 학습된 로지스틱 분류기 |
| `models/poster-clip-v*.json` | threshold · false_exclude 등 학습 메타 |
| `models/active-model.json` | 현재 활성 모델 지정 |
| `dataset/poster/` · `dataset/non_poster/` | 추후 재학습용 정답 데이터 |

### 15.3 복사하지 않아도 되는 것

| 경로 | 이유 |
|------|------|
| `.venv/` | 새 PC에서 다시 설치 |
| `data/poster_embed/` | 임베딩 캐시. 없어도 재계산 가능 |
| Hugging Face 캐시 | 없어도 첫 `poster-train` / `poster-classify` 때 다시 받음 |

### 15.4 수집 업무까지 그대로 옮길 때

- `input/raw/`
- `data/kakao_local.db`
- `kakao-pc-collect/config/`
- `kakao-pc-collect/data/watermarks.json`
- 각 레포 `.env` (새 PC 경로에 맞게 수정)

### 15.5 오프라인만 써야 할 때

- 모델 가중치를 다시 받지 못하는 환경이면, 새 PC에서 최초 1회 인터넷 연결이 필요하다.
- 완전 오프라인 이전이 목적이면 `models/` 외에 **Hugging Face 로컬 캐시 폴더**도 함께 복사한다.

---

## 16. 완료 정의 (v1)

- [x] C0 pytest (torch 없이) 통과  
- [x] C1 활성 후보 모델 1개 + 오제외·threshold가 json에 있음 (`poster-clip-v1`, 2026-08-18)  
- [x] C2 dry-run 필터·similar skip 코드 반영. 원본 폴더 불변  
- [x] C3 human CLI (`poster-label`) · activate 게이트  
- [ ] 운영 DB에 `poster-classify` 적용 후 dry-run 확인 (운영자 실행)  
- [x] ESENCIA golden·기존 similar/upload 테스트 회귀 없음 (phase4 1건은 기존 실패)  

C4는 v1 완료에 포함하지 않는다.

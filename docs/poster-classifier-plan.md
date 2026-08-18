# 로컬 포스터 분류기 — 계약 (CLIP + 작은 분류기)

<!-- [변경사유]: 2026-08-18 — 수집 잡사진(일상 스냅)을 업로드에서 빼되 원본·매칭은 유지. 비용 없이 로컬 재학습 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-08-18 |
| 상태 | **계약 초안 · 미구현** (구현 단계: [poster-classifier-dev.md](./poster-classifier-dev.md)) |
| 위치 | `kakao-import-local` (수집기 `kakao-pc-collect`에 넣지 않음) |
| 비용 | 클라우드 API 없음. OpenCLIP 체크포인트 1회 다운로드 후 오프라인 |
| 관련 | [privacy-retention.md](./privacy-retention.md) · [kakao-pc-collect-plan.md](./kakao-pc-collect-plan.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [poster-classifier-dev.md](./poster-classifier-dev.md) |

similar-review(같은 그림인지)와 **다른 문제**다. 이 문서는 “포스터 vs 일상 사진”이다.

---

## 1. 목표

카카오 서랍에서 받은 이미지에는 강습·파티 포스터와 텀블러·셀카 같은 일상 사진이 섞인다.  
파일명(`KakaoTalk_시각[_NN]`)·확장자·앨범 번호만으로는 구분할 수 없다.

| 한다 | 하지 않는다 |
|------|-------------|
| 업로드 큐에서 **확실한 비포스터만** 제외 | 원본 삭제·이동·리네임 |
| 판정만 DB/JSON에 저장 | `input/raw/<room_id>/photos` 레이아웃 변경 |
| 오분류를 정답으로 넣어 **재학습** | 유료 OCR/비전 API |
| 애매하면 **통과** (포스터 누락이 더 손해) | 글자 수·키워드만으로 즉시 합격/탈락 |

원본 보존: [privacy-retention.md](./privacy-retention.md) §2.

---

## 2. 채택 방식 (본선)

```text
이미지
  → OpenCLIP (고정, 재학습 안 함) → embedding
  → 로지스틱 회귀 (우리 데이터만 학습)
  → poster | uncertain | non_poster
```

- CLIP 자체 fine-tune·GPU 필수 학습은 **초기 비범위**.
- Tesseract 글자 밀도·EXIF·OpenCV 에지/색만으로 본선을 두지 않는다.  
  데이터가 쌓여도 임계값만 만지게 되고, 캡처/메뉴판(글자 많음)·미니멀 포스터(글자 적음)에 약하다.
- OCR은 **uncertain이 너무 많을 때** embedding 옆 보조 특징으로만 추가한다. 키워드 발견 = 즉시 포스터 확정은 금지(대화 캡처에도 “바차타”가 있음).

나중에 비교만 해도 되는 것: Linear SVM, k-NN(설명용). 운영 판정은 v1에서 로지스틱 회귀.

---

## 3. 파이프라인 위치

수집기는 오늘처럼 방 폴더에 **전부 복사**한다. 분류는 import 쪽이다.

```text
kakao-pc-collect
  → input/raw/<room_id>/chats + photos   (원본 유지)

kakao-import run          # scan · parse · match · hash
  → (본 분류)             # SHA 있으면 캐시, 판정 기록, 확실한 non_poster만 업로드 제외
kakao-import similar-detect
kakao-import similar-review
kakao-import upload
```

파일을 `raw/photos` 평면 폴더로 옮기는 별도 `kakao-image-filter`는 **채택하지 않음**. 방별 경로·파일명 매칭이 깨진다.

---

## 4. 식별자 · 저장

이미지는 이동하지 않는다.

```text
input/raw/<room_id>/photos/KakaoTalk_....jpg
```

### 4.1 키

파일명 단독 금지. 같은 `KakaoTalk_….jpg`가 **다른 방**에 있을 수 있다.

| 용도 | 키 |
|------|-----|
| 본키 (판정 재사용) | `room_id` + SHA-256 |
| 매칭·탐색 | `relativePath` + 원본 파일명 |

`ignore_list.json`을 파일명만으로 키우는 안은 폐기.

### 4.2 판정 레코드 (개념)

```json
{
  "room_id": "gangnam_latin",
  "rel_path": "gangnam_latin/photos/KakaoTalk_20260817_061323742_15.jpg",
  "file_name": "KakaoTalk_20260817_061323742_15.jpg",
  "sha256": "…",
  "model_version": "poster-clip-v1",
  "poster_score": 0.0124,
  "status": "non_poster",
  "source": "model",
  "excluded_from_upload": true,
  "classified_at": "2026-08-18T02:30:00+09:00"
}
```

구현은 로컬 SQLite가 우선이다 (import DB와 같은 연결). sidecar JSON만으로 운영해도 키 규칙은 동일.

### 4.3 상태 · 주체

`excluded_from_upload` 플래그만 두면 재학습·사람 수정이 섞인다.

| status | 업로드 |
|--------|--------|
| `poster` | 통과 |
| `uncertain` | 통과 |
| `non_poster` | 제외 |
| (사람) `poster` | **항상** 통과 |
| (사람) `non_poster` | **항상** 제외 |

`source`: `model` | `human` | `rule`

**사람 확정 > 모델.** 모델을 바꿔도 human 행은 덮어쓰지 않는다.

---

## 5. 판정 정책

### 5.1 보수적 제외

목표는 전체 정확도가 아니라 **포스터를 비포스터로 잘못 제외하는 비율을 거의 0**에 가깝게 유지하는 것이다.  
잡사진을 일부 올려 보내는 편이, 포스터를 빠뜨리는 것보다 낫다.

임계값은 `0.2` / `0.02`를 임의로 박지 않는다. **고정 검증셋**에서 포스터 오제외가 허용 범위일 때의 점수를 고른다.  
로지스틱 회귀의 `0.99`를 실제 오판 1%로 믿지 않는다. (데이터 적을 때 Platt 보정은 선택, isotonic은 과적합 위험)

### 5.2 실패 시 통과 (fail-open)

손상 파일, 미지원 포맷, CLIP 오류 → **제외하지 않음**, 로그만.

### 5.3 크기 rule

스티커·아이콘만 아주 보수적으로 `rule` 후보.

허용 예: 한 변이 64px **미만**이고 용량이 매우 작음.  
금지: 가로 500px 미만 전부 제외, 가로형 전부 제외 (재압축 포스터·가로 일정표).

---

## 6. 학습 데이터

운영 원본 `photos/`는 그대로 둔다. 학습은 **복사본 인덱스**만 쓴다.

```text
dataset/
  poster/       # 올릴 장 (사이트 등록 포스터 복사 가능)
  non_poster/   # 올리면 안 되는 장
```

| 단계 | 포스터 | 비포스터 |
|------|------:|--------:|
| 시험 (v1 착수) | 80~100 | 80~100 |
| 기본 | 300+ | 300+ |

수량보다 **다양성**. 식탁 사진만 100장이면 안 된다.

**포스터에 넣을 경계 사례:** 글자 적은 파티 이미지, 단체사진형 홍보, 콜라주, 인스타 캡처형 포스터, 가로·흑백·손글씨, 날짜만 바꾼 재개강.

**비포스터(hard negative):** 카톡/인스타 캡처, 메뉴판, 영수증, 제품·음식점 광고, 밈, 유튜브 썸네일, 댄스 아닌 일정표, 공연 장면만, 글자 많은 상품 소개.

오분류 한 장만 넣기보다 **같은 유형 여러 장**. 같은 파일 재압축 20장은 넣지 않는다.

학습 인덱스에서만: SHA 중복 제거, perceptual hash로 같은 포스터 변형 대표 제한. **원본 수집 폴더에는 적용하지 않음.**

---

## 7. train / test 분리

파일 단위 무작위 분할 금지. 아래는 **한 그룹 전부 train 또는 전부 test**.

- SHA-256 동일
- perceptual hash가 가까운 재압축·재전송
- 같은 `KakaoTalk_` 스템 앨범 (`_01` …)
- 여러 방에 올라온 같은 포스터

가능하면 **날짜**: 과거 → train, 최근 1~2개월 → validation/test.  
안 그러면 원본 train / 재압축 test가 되어 점수가 실제보다 높아진다.

---

## 8. 재학습 · 모델 활성화

점진 학습(이전 가중치에 이어 붙이기)은 하지 않는다.

```text
새 정답 추가
  → 새 장만 CLIP embedding (sha256 캐시)
  → 누적 embedding 전체로 로지스틱 회귀 재학습
  → 고정 test에서 v_n vs v_n+1
  → 포스터 오제외가 늘지 않으면 활성화
```

```text
models/
  poster-clip-v1.joblib
  poster-clip-v1.json
  poster-clip-v2.joblib
  poster-clip-v2.json
  active-model.json
```

메타 JSON 예: CLIP 모델명, 학습 시각, train/test 장수, 포스터 오제외 건수, `exclude_threshold`.

임베딩 캐시: `sha256 → vector`. CLIP은 이미지마다 한 번.

---

## 9. 구현 단계 (코드)

1. **분류기만** — train / classify CLI. OCR 없음.  
2. **import 연동** — hash 이후, upload 전에 판정. `non_poster`만 제외. 방 폴더 불변.  
3. **사람 확정·재학습** — 오분류를 dataset에 넣고 train. test 통과 시 활성화.  
4. **OCR 보조** — uncertain이 계속 많을 때만. 분류기 특징으로만.

설치: `torch` + `open_clip_torch` + scikit-learn. 체크포인트 수백 MB~1GB, CPU로 하루 수십~수백 장 가능.

---

## 10. 운영자가 준비할 것

1. `dataset/poster`, `dataset/non_poster` (원본 `raw`는 복사만).  
2. 사이트에 올린 포스터 + 지금 `photos`에서 골라낸 잡사진.  
3. 시험은 각 80~100장, 종류를 섞을 것.  
4. 틀린 장이 나오면 해당 유형을 폴더에 추가하고 재학습한다는 운영 약속.

유료 API·서버 GPU·원본 삭제 도구는 준비하지 않는다.

---

## 11. 비범위 (이 문서)

- 수집 단계에서 서랍 칸을 “포스터만” 고르기  
- AllDup식 파일 삭제 (exact SHA는 기존 `excluded_from_upload` / similar-detect)  
- similar-review를 포스터 판정 UI로 쓰기  
- CLIP 전체 fine-tune (한계가 확인된 뒤 검토)

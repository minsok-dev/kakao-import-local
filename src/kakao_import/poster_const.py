# [변경사유]: 포스터 분류 v1 상수 — 임계값 숫자는 여기 박지 않음(학습 리포트)
"""포스터 분류기 고정값."""

from __future__ import annotations

from kakao_import.config import PROJECT_ROOT

# OpenCLIP — 계약 poster-classifier-dev.md §4
CLIP_ARCH = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"
CLIP_MODEL_KEY = f"{CLIP_ARCH}/{CLIP_PRETRAINED}"

# 스티커·아이콘만. 가로 500px 컷 금지
TINY_MIN_SIDE = 64
TINY_MAX_BYTES = 20_000

# 학습 그룹: aHash Hamming (64bit). similar 기본 10과 비슷한 근사
AHASH_MAX_DISTANCE = 8

TEST_GROUP_FRACTION = 0.20
RANDOM_SEED = 42

DATASET_DIR = PROJECT_ROOT / "dataset"
POSTER_DIR = DATASET_DIR / "poster"
NON_POSTER_DIR = DATASET_DIR / "non_poster"
MODELS_DIR = PROJECT_ROOT / "models"
EMBED_CACHE_DIR = PROJECT_ROOT / "data" / "poster_embed"
ACTIVE_MODEL_PATH = MODELS_DIR / "active-model.json"

STATUS_POSTER = "poster"
STATUS_UNCERTAIN = "uncertain"
STATUS_NON_POSTER = "non_poster"
SOURCE_MODEL = "model"
SOURCE_HUMAN = "human"
SOURCE_RULE = "rule"

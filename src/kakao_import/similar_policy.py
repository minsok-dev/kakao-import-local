# [변경사유]: Phase 4.0 — decision→upload policy 매핑 (실행은 4.2+; 탐지 단계에서는 저장만)
"""similar content decision vs upload policy.

decision = 콘텐츠 관계 판단 (자동 병합·삭제 아님)
upload_policy = 업로드 큐 적용 규칙 (4.2+에서 사용)
"""

from __future__ import annotations

from typing import Literal

ContentDecision = Literal[
    "same_content", "different_content", "partial", "deferred"
]
UploadPolicy = Literal[
    "upload_representative", "upload_all_members", "upload_none", "upload_partial"
]

VALID_DECISIONS: frozenset[str] = frozenset(
    {"same_content", "different_content", "partial", "deferred"}
)

# 서버 MEDIA_SIMILAR_MAX_DISTANCE 기본과 동일
DEFAULT_SIMILAR_MAX_DISTANCE = 10


def upload_policy_for_decision(decision: str) -> UploadPolicy:
    """decision → upload policy (파일 삭제·자동 병합을 의미하지 않음)."""
    d = (decision or "").strip()
    if d == "same_content":
        return "upload_representative"
    if d == "different_content":
        return "upload_all_members"
    if d == "partial":
        return "upload_partial"
    return "upload_none"

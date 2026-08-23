# [변경사유]: 개발 PC에 models/active-model.json 이 있어도 단위 테스트가 미분류 hold 로 깨지지 않게 격리
"""공통 pytest fixture."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_active_poster_model(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """
    기본: load_active_model → None.
    실제 활성 모델이 필요한 테스트만 @pytest.mark.with_active_poster_model.
    """
    if request.node.get_closest_marker("with_active_poster_model"):
        return

    monkeypatch.setattr(
        "kakao_import.poster_classify.load_active_model",
        lambda *args, **kwargs: None,
    )

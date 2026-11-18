"""쓰기 API는 사람 토큰이 필요하다 (#295). 테스트 클라이언트는 기본으로 설정된 사람 토큰을 보낸다.

토큰 확인 자체를 보는 테스트(`test_human_token.py`)는 헤더를 직접 지운다.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def default_human_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "tester:test-human-token")
    original = TestClient.__init__

    def init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        original(self, *args, **kwargs)
        self.headers.setdefault("authorization", "Bearer test-human-token")

    monkeypatch.setattr(TestClient, "__init__", init)


@pytest.fixture(autouse=True)
def fresh_orders_cache() -> None:
    """주문 관리 목록은 잠깐 기억해 쓴다. 테스트끼리 기억이 이어지지 않게 비운다."""
    from ax_dashboard_api import live_views

    live_views.clear_orders_cache()

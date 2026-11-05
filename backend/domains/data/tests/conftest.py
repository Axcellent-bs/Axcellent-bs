"""data 테스트 공통 설정."""

import pytest

from data_agent import demo_anchor


@pytest.fixture(autouse=True)
def _reset_demo_anchor():
    """목 데모 anchor는 프로세스 안 상태라 테스트마다 비운다."""
    demo_anchor.reset_demo_anchor()
    yield
    demo_anchor.reset_demo_anchor()


@pytest.fixture(autouse=True)
def _reset_youtube_ledger():
    """YouTube 할당량 장부(메모리)도 테스트마다 비운다 (앞 테스트의 호출이 상한에 쌓이지 않게)."""
    from data_agent.youtube_quota import LEDGER

    LEDGER.clear()
    yield
    LEDGER.clear()

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


@pytest.fixture(autouse=True)
def _reset_line_store():
    """한 줄 문장 재사용 저장(메모리)도 테스트마다 비운다 (앞 테스트의 문장을 다시 쓰지 않게)."""
    from data_agent.line_store import LINES

    LINES.clear()
    yield
    LINES.clear()


@pytest.fixture(autouse=True)
def _reset_llm_halt(monkeypatch: pytest.MonkeyPatch):
    """LLM 중단 상태(키 오류·공급자 한도)도 테스트마다 새로 (앞 테스트의 가짜 429·401이 안 남게)."""
    from data_agent import llm_halt

    monkeypatch.setattr(llm_halt, "HALT", llm_halt.HaltStore(None))
    yield

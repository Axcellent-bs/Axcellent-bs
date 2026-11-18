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


@pytest.fixture(autouse=True)
def _reset_llm_inflight(monkeypatch: pytest.MonkeyPatch):
    """LLM "계산 중" 표시도 테스트마다 메모리로 새로 (실제 장부 파일에 쓰지 않게)."""
    from data_agent import llm_inflight

    monkeypatch.setattr(llm_inflight, "INFLIGHT", llm_inflight.Inflight(None))
    yield


@pytest.fixture(autouse=True)
def _no_connected_company(monkeypatch: pytest.MonkeyPatch) -> None:
    """회사 설정(`ax_agent_template.tenant`)이 개발자 PC의 회사 연결·AX_TENANT를 읽지 않게 한다.

    시험이 넘긴 env가 비어 있으면 "연결 전"이어야 한다. 회사가 필요한 시험은 env에 AX_TENANT를 준다.
    """
    for name in ("AX_TENANT", "AX_COMPANY_CONNECTION_FILE", "AX_TENANTS_DIR"):
        monkeypatch.delenv(name, raising=False)

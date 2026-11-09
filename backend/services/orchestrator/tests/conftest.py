import pytest

from ax_orchestrator.mock.harness import Harness


# 쓰기 API 토큰 명부 (auth.py). 테스트 파일은 같은 값을 Authorization 헤더로 보낸다
@pytest.fixture(autouse=True)
def _write_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token,shop=shop-test-token")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")


@pytest.fixture
def harness() -> Harness:
    return Harness()

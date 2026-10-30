"""planning 테스트 공용 설정: 목 모드 고정, 새 상태 저장소, A2A 요청 만들기, 목 쓰기 토큰."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import config
from ax_agent_template.testing import reset_state  # noqa: F401  autouse, 템플릿 상태 초기화
from planning_agent import coder
from planning_agent.api import ADMIN_ACTIONS
from planning_agent.domain.service import PlanningService, action_name
from planning_agent.runtime import set_service


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch, reset_state: None) -> PlanningService:  # noqa: F811
    """모든 테스트는 목 모드(`AGENT_TEMPLATE_MOCK=1`)로 돌고, 초기 데이터에서 시작한다.

    템플릿 상태를 비우는 reset_state 뒤에 돌아야 에이전트 이름이 planning으로 남는다.
    """
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("PLANNING_ROLE", raising=False)  # 서비스 역할 없이 합친 모드로 본다
    config.set_agent_name("planning")  # 다른 부서 테스트가 먼저 돌아도 execute 요청자는 planning
    # CI 기다리기: 테스트에선 기다리지 않는다 (검사가 없으면 바로 CI 없는 레포로 본다)
    monkeypatch.setattr(coder, "CI_POLL_SECONDS", 0.0)
    monkeypatch.setattr(coder, "NO_CI_GRACE_SECONDS", 0.0)
    return set_service(PlanningService())


def _a2a_body(
    skill: str,
    payload: dict[str, Any],
    *,
    token: str = "mock:infra",
    path: list[str] | None = None,
    context_id: str = "ctx_inc_0001",
    idempotency_key: str | None = None,
    untrusted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "meta": {
            "context_id": context_id,
            "token": token,
            "path": path or [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": idempotency_key,
        },
        "skill": skill,
        "input": payload,
        "untrusted": untrusted or {},
    }


MOCK_HUMAN = "Bearer human:tester"
MOCK_ADMIN = "Bearer mock:admin"
ADMIN_PATHS = ("/api/planning/reset", "/api/planning/vcs")


def _mock_token(url: Any, json: Any) -> str:
    path = str(url)
    if any(p in path for p in ADMIN_PATHS):
        return MOCK_ADMIN
    kind = json.get("type") if isinstance(json, dict) else None
    if isinstance(kind, str) and action_name(kind) in ADMIN_ACTIONS:
        return MOCK_ADMIN
    return MOCK_HUMAN


@pytest.fixture(autouse=True)
def mock_write_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """쓰기 API 토큰(auth.py). Authorization을 안 넣은 요청에 목 토큰을 붙인다.

    admin 자리(초기화·형상관리·자동 범위)는 `mock:admin`, 나머지는 사람 `human:tester`.
    토큰 규칙 자체는 test_write_auth.py가 Authorization을 직접 넣어 본다.
    """
    original = TestClient.request

    def request(self: TestClient, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        headers = dict(kwargs.get("headers") or {})
        if method.upper() == "POST" and not any(k.lower() == "authorization" for k in headers):
            headers["Authorization"] = _mock_token(url, kwargs.get("json"))
            kwargs["headers"] = headers
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "request", request)


@pytest.fixture
def a2a_body():
    """A2A 요청 본문을 만드는 함수 (기본 요청자 infra, 사건 ctx_inc_0001)."""
    return _a2a_body

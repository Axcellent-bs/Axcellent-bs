"""고객 아이디어 중계(shop-api → 고객지원 submit_idea): 입력 검증, 문의와 분리, 오류 변환."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_shop_api import support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

META = {"context_id": "ctx_shop_x", "token": "t", "deadline": "2030-01-01T00:00:00Z"}
URL = "/api/shop/ideas"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


class FakeSupport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.result: AgentResult | None = None

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        if self.result is not None:
            return self.result
        output = {"idea_id": "IDEA-1", "received": True, "execution_id": "exe_1"}
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=output)


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> FakeSupport:
    fake = FakeSupport()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _body(**extra) -> dict:  # noqa: ANN003
    return {
        "buyer_id": "buy_0001",
        "kind": "event",
        "text": "생일 쿠폰 이벤트가 있으면 좋겠어요",
    } | extra


def _result(outcome: str) -> AgentResult:
    return AgentResult(
        meta=RequestMeta(**META),
        status=TaskStatus.COMPLETED,
        outcome=outcome,
        output={"message": "안내"},
    )


def test_정상_아이디어는_정수_user_id와_글을_untrusted로_넘기고_접수_사실만_돌려준다(
    client: TestClient, support: FakeSupport
) -> None:
    response = client.post(URL, json=_body())
    assert response.status_code == 201
    assert response.json()["data"] == {"idea_id": "IDEA-1", "received": True}  # 내부 값은 없다
    target, request = support.calls[0]
    assert (target, request.skill) == ("support", "submit_idea")
    assert request.input == {"kind": "event", "user_id": 1}
    assert request.untrusted == {"text": "생일 쿠폰 이벤트가 있으면 좋겠어요"}
    assert request.meta.token == "mock:shop-api"


@pytest.mark.parametrize(
    "changes",
    [{"kind": "complaint"}, {"text": ""}, {"text": "가" * 501}, {"buyer_id": "x"}, {"extra": 1}],
)
def test_잘못된_입력은_고객지원을_부르지_않는다(
    client: TestClient, support: FakeSupport, changes: dict
) -> None:
    assert client.post(URL, json=_body(**changes)).status_code in (400, 404, 422)
    assert support.calls == []


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("use_inquiry", 422, "USE_INQUIRY"),
        ("spam", 422, "INVALID_IDEA"),
        ("too_short", 422, "INVALID_IDEA"),
        ("rate_limited", 429, "RATE_LIMITED"),
    ],
)
def test_고객지원의_거절은_오류로_바뀐다(
    client: TestClient, support: FakeSupport, outcome: str, status: int, code: str
) -> None:
    support.result = _result(outcome)
    response = client.post(URL, json=_body())
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_아이디어가_아닌_응답은_성공으로_내보내지_않는다(
    client: TestClient, support: FakeSupport
) -> None:
    support.result = AgentResult(
        meta=RequestMeta(**META), status=TaskStatus.COMPLETED, output={"id": 3}
    )
    assert client.post(URL, json=_body()).status_code == 502

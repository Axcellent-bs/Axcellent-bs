"""해결 결과 평가 중계(shop-api → 고객지원 submit_feedback): 입력 검증, 본인 문의, 오류 변환."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_shop_api import support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

META = {"context_id": "ctx_shop_x", "token": "t", "deadline": "2030-01-01T00:00:00Z"}
COMMENT = "연락은 010-1234-5678 로 주세요"
URL = "/api/shop/inquiries/11/feedback"


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
        output = {
            "inquiry_id": request.input.get("inquiry_id"),
            "rated": True,
            "rating": request.input.get("rating"),
            "execution_id": "exe_1",  # 내부 값은 고객에게 나가지 않아야 한다
            "status": "flagged",
        }
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
    return {"buyer_id": "buy_0001", "rating": 5, "resolved": True, "comment": COMMENT} | extra


def _result(status: TaskStatus, outcome: str | None = None, output: dict | None = None):  # noqa: ANN202
    return AgentResult(
        meta=RequestMeta(**META), status=status, outcome=outcome, output=output or {}
    )


def test_정상_평가는_정수_user_id와_후기를_untrusted로_넘기고_접수_사실만_돌려준다(
    client: TestClient, support: FakeSupport
) -> None:
    response = client.post(URL, json=_body(rating=4))
    assert response.status_code == 201
    body = response.json()
    assert body["data"] == {"inquiry_id": 11, "rated": True, "rating": 4}  # 내부 판단은 없다
    assert body["meta"]["context_id"].startswith("ctx_shop_")
    target, request = support.calls[0]
    assert (target, request.skill) == ("support", "submit_feedback")
    assert request.meta.token == "mock:shop-api"
    assert request.input == {"inquiry_id": 11, "rating": 4, "resolved": True, "user_id": 1}
    assert request.untrusted == {"comment": COMMENT}
    assert COMMENT not in str(request.input)
    assert request.meta.idempotency_key.endswith(":submit_feedback:inquiry:11")


def test_후기_글은_없어도_된다(client: TestClient, support: FakeSupport) -> None:
    payload = {"buyer_id": "buy_0001", "rating": 3, "resolved": False}
    assert client.post(URL, json=payload).status_code == 201
    assert support.calls[0][1].untrusted == {"comment": ""}


@pytest.mark.parametrize(
    "patch",
    [
        {"rating": 0},
        {"rating": 6},
        {"rating": "다섯"},
        {"comment": "가" * 301},
        {"source": "console"},  # 시험용 표시는 고객 요청으로 보낼 수 없다
        {"user_id": 2},  # 다른 사람 번호를 직접 정할 수 없다
    ],
)
def test_잘못된_본문은_422이고_고객지원을_부르지_않는다(
    client: TestClient, support: FakeSupport, patch: dict
) -> None:
    assert client.post(URL, json=_body(**patch)).status_code == 422
    assert support.calls == []


def test_필수_값이_없으면_422(client: TestClient, support: FakeSupport) -> None:
    assert client.post(URL, json={"buyer_id": "buy_0001", "rating": 5}).status_code == 422
    assert client.post(URL, json={"rating": 5, "resolved": True}).status_code == 422
    assert support.calls == []


def test_구매자_ID_형식이_틀리면_400이고_문의_번호는_1_이상이어야_한다(
    client: TestClient, support: FakeSupport
) -> None:
    bad_buyer = client.post(URL, json=_body(buyer_id="buy_1"))
    assert (bad_buyer.status_code, bad_buyer.json()["error"]["code"]) == (400, "INVALID_BUYER_ID")
    assert client.post("/api/shop/inquiries/0/feedback", json=_body()).status_code == 422
    assert client.post("/api/shop/inquiries/abc/feedback", json=_body()).status_code == 422
    assert support.calls == []


def test_남의_문의나_없는_문의는_같은_404(client: TestClient, support: FakeSupport) -> None:
    support.result = _result(
        TaskStatus.COMPLETED, "not_found", {"message": "문의 11를 찾을 수 없다"}
    )
    response = client.post(URL, json=_body(buyer_id="buy_0002"))
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert support.calls[0][1].input["user_id"] == 2  # 요청한 구매자의 번호로 확인시킨다


def test_해결되지_않은_문의와_이미_남긴_문의는_409(
    client: TestClient, support: FakeSupport
) -> None:
    support.result = _result(
        TaskStatus.COMPLETED, "invalid_state", {"message": "해결된 문의에만 평가를 남길 수 있다"}
    )
    first = client.post(URL, json=_body())
    assert (first.status_code, first.json()["error"]["code"]) == (409, "INVALID_STATE")
    # 먼저 들어온 요청이 남겨서 이번 요청은 저장되지 않은 경우도 같은 409다
    support.result = _result(TaskStatus.COMPLETED, None, {"inquiry_id": 11, "rated": False})
    second = client.post(URL, json=_body())
    assert (second.status_code, second.json()["error"]["code"]) == (409, "INVALID_STATE")


@pytest.mark.parametrize(
    ("status", "outcome", "http", "code"),
    [
        (TaskStatus.CANCELED, None, 504, "UPSTREAM_TIMEOUT"),
        (TaskStatus.REJECTED, "FORBIDDEN_SKILL", 403, "UPSTREAM_REJECTED"),
        (TaskStatus.FAILED, "invalid_input", 502, "UPSTREAM_ERROR"),
        (TaskStatus.COMPLETED, "not_executed_pending", 409, "NOT_EXECUTED"),
    ],
)
def test_고객지원_오류는_상태와_코드로_바뀐다(
    client: TestClient, support: FakeSupport, status: TaskStatus, outcome: str, http: int, code: str
) -> None:
    support.result = _result(status, outcome)
    response = client.post(URL, json=_body())
    assert (response.status_code, response.json()["error"]["code"]) == (http, code)


def test_속도_제한은_문의_접수와_따로_센다(
    client: TestClient, support: FakeSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INQUIRY_RATE_PER_BUYER", "2")
    codes = [client.post(URL, json=_body()).status_code for _ in range(3)]
    assert codes == [201, 201, 429]
    assert len(support.calls) == 2
    other = client.post(URL, json=_body(buyer_id="buy_0002"))  # 다른 구매자는 영향이 없다
    assert other.status_code == 201
    order = client.post(
        "/api/shop/orders",
        json={"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1}]},
    ).json()["data"]["order_id"]
    inquiry = client.post(
        "/api/shop/inquiries", json={"buyer_id": "buy_0001", "order_id": order, "body": "문의"}
    )
    assert inquiry.status_code != 429  # 평가가 문의 접수 한도를 쓰지 않는다


def test_후기_글은_오류_응답과_로그에_싣지_않는다(
    client: TestClient, support: FakeSupport, caplog: pytest.LogCaptureFixture
) -> None:
    support.result = _result(TaskStatus.FAILED, "invalid_input")
    with caplog.at_level("DEBUG"):
        response = client.post(URL, json=_body())
    assert COMMENT not in response.text
    assert COMMENT not in caplog.text

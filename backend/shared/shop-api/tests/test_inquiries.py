"""고객 문의 접수: 판매 서버가 문의함에 저장한다 (읽어 가는 쪽은 test_inquiry_inbox.py)."""

import logging

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_shop_api import inquiries, support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SECRET = "내 전화번호는 010-1234-5678 입니다"
META = {"context_id": "ctx_shop_x", "token": "t", "deadline": "2030-01-01T00:00:00Z"}
SUPPORT = {"Authorization": "Bearer mock:support"}


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
        meta: RequestMeta = request.meta
        output = {
            "id": 11,
            "subject": request.untrusted["subject"],
            "status": "open",
            "order_id": request.input["order_id"],
            "updated_at": "2026-10-27T00:00:00Z",
            "preview": "원문 미리보기",
            "messages": [{"role": "customer", "text": "원문"}],
        }
        return AgentResult(meta=meta, status=TaskStatus.COMPLETED, output=output)


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> FakeSupport:
    fake = FakeSupport()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _order(client: TestClient, buyer_id: str = "buy_0001") -> str:
    made = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer_id, "items": [{"product_id": "prd_0001", "qty": 1}]},
    )
    return made.json()["data"]["order_id"]


def _body(order_id: str, **extra) -> dict:  # noqa: ANN003
    return {"buyer_id": "buy_0001", "order_id": order_id, "body": SECRET} | extra


def _result(status: TaskStatus, outcome: str | None = None, output: dict | None = None):  # noqa: ANN202
    return AgentResult(
        meta=RequestMeta(**META), status=status, outcome=outcome, output=output or {}
    )


def test_없는_주문은_404(client: TestClient, support: FakeSupport) -> None:
    response = client.post("/api/shop/inquiries", json=_body("ord_9999"))
    assert (response.status_code, response.json()["error"]["code"]) == (404, "ORDER_NOT_FOUND")
    assert support.calls == []


def test_남의_주문도_없는_주문과_같은_404(client: TestClient, support: FakeSupport) -> None:
    other = _order(client, "buy_0002")
    mine = client.post("/api/shop/inquiries", json=_body(other))
    missing = client.post("/api/shop/inquiries", json=_body("ord_9999"))
    assert mine.status_code == 404 and mine.json() == missing.json()
    assert support.calls == []


def test_원문은_로그와_오류에_남지_않는다(
    client: TestClient, support: FakeSupport, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    order_id = _order(client)
    ok = client.post("/api/shop/inquiries", json=_body(order_id))
    bad = client.post("/api/shop/inquiries", json=_body(order_id, subject="가" * 101))
    assert SECRET not in ok.text and SECRET not in bad.text
    assert SECRET not in caplog.text


def test_멱등_키는_시간_구간이_같을_때만_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOP_INQUIRY_DEDUP_WINDOW_SEC", raising=False)
    args = ("buy_0001", "ord_0001", "제목", SECRET)
    same_a = inquiries.digest(*args, now=lambda: 1200.0)
    same_b = inquiries.digest(*args, now=lambda: 1799.0)
    later = inquiries.digest(*args, now=lambda: 1800.0)
    other = inquiries.digest("buy_0001", "ord_0001", "제목", "다른 내용", now=lambda: 1200.0)
    assert same_a == same_b
    assert later != same_a
    assert other != same_a


@pytest.mark.parametrize("value", ["0", "-5", "abc", ""])
def test_잘못된_창_값은_기본값(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SHOP_INQUIRY_DEDUP_WINDOW_SEC", value)
    assert inquiries.dedup_window() == 600
    monkeypatch.setenv("SHOP_INQUIRY_DEDUP_WINDOW_SEC", "30")
    assert inquiries.dedup_window() == 30


def test_접수는_고객지원을_부르지_않고_문의함에_저장한다(
    client: TestClient, support: FakeSupport
) -> None:
    order_id = _order(client)
    response = client.post("/api/shop/inquiries", json=_body(order_id, subject="결제 문의"))
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["id"] == 1 and body["data"]["status"] == "open"
    assert body["data"]["order_id"] == order_id and body["data"]["updated_at"]
    assert body["meta"]["context_id"].startswith("ctx_shop_")
    assert support.calls == []  # 판매 서버가 고객지원을 직접 부르지 않는다


def test_제목이_없으면_주문_번호로_채운다(client: TestClient, support: FakeSupport) -> None:
    order_id = _order(client)
    assert client.post("/api/shop/inquiries", json=_body(order_id)).status_code == 201
    item = client.get("/api/shop/admin/inquiries", headers=SUPPORT).json()["data"][0]
    assert item["subject"] == f"주문 {order_id} 문의"


def test_고객지원이_꺼져_있어도_접수된다(client: TestClient) -> None:
    order_id = _order(client)  # SEND를 바꾸지 않아 고객지원에는 닿지 못한다. 접수와 상관이 없다
    assert client.post("/api/shop/inquiries", json=_body(order_id)).status_code == 201


def test_같은_접수는_한_건이고_다른_내용은_새_건이다(client: TestClient) -> None:
    order_id = _order(client)
    first = client.post("/api/shop/inquiries", json=_body(order_id)).json()["data"]["id"]
    again = client.post("/api/shop/inquiries", json=_body(order_id)).json()["data"]["id"]
    other = client.post("/api/shop/inquiries", json=_body(order_id, body="다른 내용")).json()
    assert first == again != other["data"]["id"]
    items = client.get("/api/shop/admin/inquiries", headers=SUPPORT).json()["data"]
    assert [i["seq"] for i in items] == [first, other["data"]["id"]]
    assert items[0]["customer_name"] == "데모 구매자 0001"

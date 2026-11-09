"""내 문의 목록·상태 읽기(shop-api → 고객지원 list_inquiries·get_inquiry): 본인 범위·고객용 모양."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_shop_api import support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

INTERNAL = "내부 초안·분류·상담원 메모"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


def _item(id_: int, status: str, rated: bool, **extra) -> dict:  # noqa: ANN003
    return {
        "id": id_,
        "subject": f"문의 {id_}",
        "status": status,
        "order_id": "ord_0001",
        "updated_at": "2026-10-30T00:00:00Z",
        "rated": rated,
        "preview": "미리보기",
    } | extra


class FakeSupport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.output: dict = {}
        self.outcome: str | None = None
        self.status = TaskStatus.COMPLETED

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output=self.output
        )


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> FakeSupport:
    fake = FakeSupport()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def test_목록은_구매자_번호로_부르고_고객용_모양과_평가_버튼_여부를_돌려준다(
    client: TestClient, support: FakeSupport
) -> None:
    support.output = {
        "items": [
            _item(3, "resolved", False, analysis=INTERNAL, customer_name="김하늘"),
            _item(2, "resolved", True),
            _item(1, "open", False),
            _item(4, "escalated", False),
        ]
    }
    response = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0007"})
    assert response.status_code == 200
    body = response.json()
    assert [(i["id"], i["can_rate"]) for i in body["data"]] == [
        (3, True),  # 해결됐고 아직 평가하지 않음
        (2, False),  # 이미 평가함
        (1, False),  # 아직 해결되지 않음
        (4, False),
    ]
    assert body["meta"]["total"] == 4
    assert INTERNAL not in response.text and "김하늘" not in response.text  # 모르는 키는 버린다
    target, request = support.calls[0]
    assert (target, request.skill) == ("support", "list_inquiries")
    assert request.input == {"user_id": 7} and request.meta.token == "mock:shop-api"
    assert request.meta.idempotency_key is None  # 읽기다


def test_목록은_limit만큼만_돌려주고_전체_수는_meta에_둔다(
    client: TestClient, support: FakeSupport
) -> None:
    support.output = {"items": [_item(i, "open", False) for i in range(1, 6)]}
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001", "limit": 2}).json()
    assert [i["id"] for i in body["data"]] == [1, 2] and body["meta"]["total"] == 5
    assert (
        client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001", "limit": 0}).status_code
        == 422
    )


def test_문의가_없으면_빈_목록(client: TestClient, support: FakeSupport) -> None:
    support.output = {"items": []}
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert body["data"] == [] and body["meta"]["total"] == 0


def test_구매자_ID가_없거나_틀리면_거절하고_고객지원을_부르지_않는다(
    client: TestClient, support: FakeSupport
) -> None:
    assert client.get("/api/shop/inquiries").status_code == 422
    bad = client.get("/api/shop/inquiries", params={"buyer_id": "buy_1"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "INVALID_BUYER_ID")
    assert client.get("/api/shop/inquiries/5", params={"buyer_id": "7"}).status_code == 400
    assert support.calls == []


def test_상세는_메시지_글만_보여_주고_내부_값은_버린다(
    client: TestClient, support: FakeSupport
) -> None:
    support.output = _item(5, "resolved", False, analysis=INTERNAL) | {
        "messages": [
            {
                "id": 1,
                "role": "customer",
                "text": "환불해 주세요",
                "created_at": "t1",
                "analysis": INTERNAL,
            },
            {"id": 2, "role": "agent", "text": "처리했습니다", "created_at": "t2"},
        ]
    }
    response = client.get("/api/shop/inquiries/5", params={"buyer_id": "buy_0001"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["can_rate"] is True and data["id"] == 5
    assert [set(m) for m in data["messages"]] == [{"id", "role", "text", "created_at"}] * 2
    assert INTERNAL not in response.text
    target, request = support.calls[0]
    assert (target, request.skill) == ("support", "get_inquiry")
    assert request.input == {"inquiry_id": 5, "user_id": 1}


def test_남의_문의와_없는_문의는_같은_404(client: TestClient, support: FakeSupport) -> None:
    support.outcome = "not_found"
    support.output = {"message": "문의 5를 찾을 수 없다"}
    response = client.get("/api/shop/inquiries/5", params={"buyer_id": "buy_0002"})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert support.calls[0][1].input["user_id"] == 2


def test_문의_번호는_1_이상_정수(client: TestClient, support: FakeSupport) -> None:
    assert client.get("/api/shop/inquiries/0", params={"buyer_id": "buy_0001"}).status_code == 422
    assert client.get("/api/shop/inquiries/abc", params={"buyer_id": "buy_0001"}).status_code == 422
    assert support.calls == []


def test_문의가_아닌_응답은_성공으로_내보내지_않는다(
    client: TestClient, support: FakeSupport
) -> None:
    support.output = {"message": "이상한 응답"}
    response = client.get("/api/shop/inquiries/5", params={"buyer_id": "buy_0001"})
    assert (response.status_code, response.json()["error"]["code"]) == (502, "UPSTREAM_ERROR")


@pytest.mark.parametrize(
    ("status", "http", "code"),
    [
        (TaskStatus.CANCELED, 504, "UPSTREAM_TIMEOUT"),
        (TaskStatus.FAILED, 502, "UPSTREAM_ERROR"),
    ],
)
def test_고객지원_오류는_상태와_코드로_바뀐다(
    client: TestClient, support: FakeSupport, status: TaskStatus, http: int, code: str
) -> None:
    support.status = status
    response = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"})
    assert (response.status_code, response.json()["error"]["code"]) == (http, code)

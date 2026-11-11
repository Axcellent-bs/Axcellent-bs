"""해결 결과 평가: 평가는 이 서버에 저장하고(고객지원을 부르지 않음) AXcellent가 읽어 간다.

평가는 고객지원이 되돌려 쓴 문의 값이 해결(resolved)이고 아직 평가하지 않았을 때만 받는다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult
from ax_shop_api import inbox, support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

COMMENT = "연락은 010-1234-5678 로 주세요"
SUPPORT_ID = 11  # 고객지원이 읽어 만든 문의 번호 (접수 번호와 다르다)
URL = "/api/shop/inquiries/1/feedback"  # 고객이 받은 번호 = 접수 번호 1
READ = "/api/shop/admin/feedback"
SUPPORT = {"Authorization": "Bearer mock:support"}
MARKETING = {"Authorization": "Bearer mock:marketing"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


class NoCalls:
    """평가 접수가 고객지원을 부르면 이 대역이 기록한다 (부르면 안 된다)."""

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        raise AssertionError("판매 서버가 고객지원을 불렀다")


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> NoCalls:
    fake = NoCalls()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def seed(db: Db, buyer: str = "buy_0001", *, status: str | None = "resolved") -> int:
    """접수를 넣고 고객지원이 읽어 갔으면(SUPPORT_ID) 그 번호와 고객용 값(status)을 잇는다."""
    saved = inbox.add(
        db,
        digest=f"{buyer}-{status}",
        context_id="ctx_shop_aaaaaaaaaaaa",
        buyer_id=buyer,
        customer_name="김하늘",
        order_id="ord_0001",
        subject="문의",
        message="원문",
        images=None,
    )
    if status is not None:
        inbox.mark_synced(db, saved["seq"], SUPPORT_ID)
        view = {"id": SUPPORT_ID, "subject": "문의", "status": status, "rated": False}
        inbox.set_view(db, saved["seq"], view, f"d-{status}")
    return saved["seq"]


@pytest.fixture(autouse=True)
def _own_inquiry(db: Db) -> None:
    seed(db)  # 접수 번호 1 = 고객지원 문의 11, 구매자 buy_0001, 해결됨


def _body(**extra) -> dict:  # noqa: ANN003
    return {"buyer_id": "buy_0001", "rating": 5, "resolved": True, "comment": COMMENT} | extra


def test_a_rating_is_saved_and_only_the_receipt_is_returned(
    client: TestClient, support: NoCalls
) -> None:
    response = client.post(URL, json=_body(rating=4))
    assert response.status_code == 201
    # 고객이 받은 번호(1)로 돌려주고 내부 판단은 없다
    assert response.json()["data"] == {"inquiry_id": 1, "rated": True, "rating": 4}
    assert support.calls == []


def test_the_customer_sees_it_as_rated_right_away(client: TestClient) -> None:
    client.post(URL, json=_body())
    data = client.get("/api/shop/inquiries/1", params={"buyer_id": "buy_0001"}).json()["data"]
    assert data["rated"] is True and data["can_rate"] is False


def test_the_comment_is_optional(client: TestClient) -> None:
    payload = {"buyer_id": "buy_0001", "rating": 3, "resolved": False}
    assert client.post(URL, json=payload).status_code == 201
    (item,) = client.get(READ, headers=SUPPORT).json()["data"]
    assert item["comment"] == "" and item["rating"] == 3 and item["resolved"] is False


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
def test_a_bad_body_is_422_and_nothing_is_saved(client: TestClient, patch: dict) -> None:
    assert client.post(URL, json=_body(**patch)).status_code == 422
    assert client.get(READ, headers=SUPPORT).json()["data"] == []


def test_required_values_are_checked(client: TestClient) -> None:
    assert client.post(URL, json={"buyer_id": "buy_0001", "rating": 5}).status_code == 422
    assert client.post(URL, json={"rating": 5, "resolved": True}).status_code == 422


def test_the_buyer_id_format_and_the_inquiry_number_are_checked(client: TestClient) -> None:
    bad_buyer = client.post(URL, json=_body(buyer_id="buy_1"))
    assert (bad_buyer.status_code, bad_buyer.json()["error"]["code"]) == (400, "INVALID_BUYER_ID")
    assert client.post("/api/shop/inquiries/0/feedback", json=_body()).status_code == 422
    assert client.post("/api/shop/inquiries/abc/feedback", json=_body()).status_code == 422


def test_someone_elses_and_missing_inquiries_are_the_same_404(client: TestClient) -> None:
    other = client.post(URL, json=_body(buyer_id="buy_0002"))  # 접수 1은 buy_0001의 것
    missing = client.post("/api/shop/inquiries/99/feedback", json=_body(buyer_id="buy_0002"))
    assert (other.status_code, other.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert other.json() == missing.json()


def test_an_inquiry_support_has_not_read_or_not_resolved_cannot_be_rated(
    client: TestClient, db: Db
) -> None:
    unread = seed(db, "buy_0003", status=None)
    open_one = seed(db, "buy_0004", status="open")
    for seq, buyer in ((unread, "buy_0003"), (open_one, "buy_0004")):
        response = client.post(f"/api/shop/inquiries/{seq}/feedback", json=_body(buyer_id=buyer))
        assert (response.status_code, response.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert client.get(READ, headers=SUPPORT).json()["data"] == []


def test_a_second_rating_is_a_409_and_the_first_stays(client: TestClient) -> None:
    assert client.post(URL, json=_body(rating=5)).status_code == 201
    second = client.post(URL, json=_body(rating=1))
    assert (second.status_code, second.json()["error"]["code"]) == (409, "INVALID_STATE")
    (item,) = client.get(READ, headers=SUPPORT).json()["data"]
    assert item["rating"] == 5


def test_the_rate_limit_is_counted_apart_from_inquiries(
    client: TestClient, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INQUIRY_RATE_PER_BUYER", "2")
    codes = [client.post(URL, json=_body()).status_code for _ in range(3)]
    assert codes == [201, 409, 429]  # 처음만 저장되고 둘째는 이미 평가함, 셋째는 한도
    other_seq = seed(db, "buy_0002")  # 다른 구매자는 영향이 없다
    other = client.post(
        f"/api/shop/inquiries/{other_seq}/feedback", json=_body(buyer_id="buy_0002")
    )
    assert other.status_code == 201
    order = client.post(
        "/api/shop/orders",
        json={"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1}]},
    ).json()["data"]["order_id"]
    inquiry = client.post(
        "/api/shop/inquiries", json={"buyer_id": "buy_0001", "order_id": order, "body": "문의"}
    )
    assert inquiry.status_code != 429  # 평가가 문의 접수 한도를 쓰지 않는다


def test_support_reads_ratings_and_marks_them(client: TestClient) -> None:
    client.post(URL, json=_body(rating=4))
    res = client.get(READ, headers=SUPPORT)
    (item,) = res.json()["data"]
    assert res.json()["meta"]["total"] == 1
    assert set(item) == {
        "seq", "key", "buyer_id", "support_inquiry_id", "rating", "resolved", "comment",
        "created_at",
    }  # fmt: skip
    assert (item["seq"], item["support_inquiry_id"], item["comment"]) == (1, SUPPORT_ID, COMMENT)
    done = client.post(
        "/api/shop/admin/inquiries/1/feedback-synced", json={"outcome": "rated"}, headers=SUPPORT
    )
    assert done.status_code == 200 and done.json()["data"]["outcome"] == "rated"
    again = client.post(
        "/api/shop/admin/inquiries/1/feedback-synced", json={"outcome": "x"}, headers=SUPPORT
    )
    assert again.json()["data"]["outcome"] == "rated"  # 이미 표시한 건 바뀌지 않는다
    assert client.get(READ, headers=SUPPORT).json()["data"] == []


def test_only_support_and_admin_may_read_and_mark_ratings(client: TestClient) -> None:
    client.post(URL, json=_body())
    mark = "/api/shop/admin/inquiries/1/feedback-synced"
    assert client.get(READ).status_code == 401
    assert client.get(READ, headers=MARKETING).status_code == 403
    assert client.post(mark, json={"outcome": "rated"}).status_code == 401
    assert client.post(mark, json={"outcome": "rated"}, headers=MARKETING).status_code == 403
    assert (
        client.post(
            "/api/shop/admin/inquiries/99/feedback-synced",
            json={"outcome": "rated"},
            headers=SUPPORT,
        ).status_code
        == 404
    )
    assert client.post(mark, json={"outcome": ""}, headers=SUPPORT).status_code == 422


def test_the_comment_is_not_in_error_responses_or_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        bad = client.post(URL, json=_body(rating=9))
        ok = client.post(URL, json=_body())
        again = client.post(URL, json=_body())
    assert COMMENT not in bad.text and COMMENT not in again.text and ok.status_code == 201
    assert COMMENT not in caplog.text
    assert json.loads(again.text)["error"]["code"] == "INVALID_STATE"

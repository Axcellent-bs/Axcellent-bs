"""고객 아이디어함: 접수는 저장만 하고(고객지원을 부르지 않음), AXcellent가 읽어 가 처리한다."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult
from ax_shop_api import support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

URL = "/api/shop/ideas"
READ = "/api/shop/admin/ideas"
SUPPORT = {"Authorization": "Bearer mock:support"}
MARKETING = {"Authorization": "Bearer mock:marketing"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


class NoCalls:
    """접수가 고객지원을 부르면 이 대역이 기록한다 (부르면 안 된다)."""

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
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _body(**extra) -> dict:  # noqa: ANN003
    return {
        "buyer_id": "buy_0001",
        "kind": "event",
        "text": "생일 쿠폰 이벤트가 있으면 좋겠어요",
    } | extra


def test_an_idea_is_saved_and_only_the_receipt_is_returned(
    client: TestClient, support: NoCalls
) -> None:
    response = client.post(URL, json=_body())
    assert response.status_code == 201
    assert response.json()["data"] == {"idea_id": 1, "received": True}  # 내부 값은 없다
    assert response.json()["meta"]["context_id"].startswith("ctx_shop_")
    assert support.calls == []


@pytest.mark.parametrize(
    "changes",
    [{"kind": "complaint"}, {"text": ""}, {"text": "가" * 501}, {"buyer_id": "x"}, {"extra": 1}],
)
def test_bad_input_is_not_saved(client: TestClient, support: NoCalls, changes: dict) -> None:
    assert client.post(URL, json=_body(**changes)).status_code in (400, 404, 422)
    assert client.get(READ, headers=SUPPORT).json()["data"] == []
    assert support.calls == []


def test_the_same_idea_from_the_same_buyer_is_one(client: TestClient) -> None:
    first = client.post(URL, json=_body()).json()["data"]["idea_id"]
    again = client.post(URL, json=_body(text="  생일 쿠폰   이벤트가 있으면 좋겠어요 ")).json()
    assert again["data"]["idea_id"] == first  # 공백만 다르다
    other = client.post(URL, json=_body(buyer_id="buy_0002")).json()["data"]["idea_id"]
    assert other != first
    assert client.get(READ, headers=SUPPORT).json()["meta"]["total"] == 2


def test_the_buyer_is_limited_per_minute(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INQUIRY_RATE_PER_BUYER", "2")
    for n in range(2):
        assert client.post(URL, json=_body(text=f"의견 {n}번입니다")).status_code == 201
    over = client.post(URL, json=_body(text="의견 3번입니다"))
    assert (over.status_code, over.json()["error"]["code"]) == (429, "RATE_LIMITED")


def test_support_reads_ideas_oldest_first_and_marks_them(client: TestClient) -> None:
    client.post(URL, json=_body(text="첫 번째 의견입니다"))
    client.post(URL, json=_body(kind="feature", text="두 번째 의견입니다"))
    res = client.get(READ, params={"limit": 1}, headers=SUPPORT)
    (item,) = res.json()["data"]
    assert res.json()["meta"]["total"] == 2 and item["seq"] == 1
    assert item["text"] == "첫 번째 의견입니다" and item["kind"] == "event"
    assert set(item) == {"seq", "key", "context_id", "buyer_id", "kind", "text", "created_at"}
    first = {"outcome": "received", "ref": "IDEA-1"}
    done = client.post(f"{READ}/1/synced", json=first, headers=SUPPORT)
    assert done.status_code == 200 and done.json()["data"]["outcome"] == "received"
    again = client.post(f"{READ}/1/synced", json={"outcome": "other"}, headers=SUPPORT)
    assert again.json()["data"]["outcome"] == "received"  # 이미 표시한 건 바뀌지 않는다
    assert [i["seq"] for i in client.get(READ, headers=SUPPORT).json()["data"]] == [2]


def test_only_support_and_admin_may_read_and_mark_and_numbers_are_checked(
    client: TestClient,
) -> None:
    client.post(URL, json=_body())
    assert client.get(READ).status_code == 401
    assert client.get(READ, headers=MARKETING).status_code == 403
    assert client.get(READ, headers={"Authorization": "Bearer mock:admin"}).status_code == 200
    body = {"outcome": "received"}
    assert client.post(f"{READ}/1/synced", json=body).status_code == 401
    assert client.post(f"{READ}/1/synced", json=body, headers=MARKETING).status_code == 403
    assert client.post(f"{READ}/99/synced", json=body, headers=SUPPORT).status_code == 404
    for bad in ({}, {"outcome": ""}, {"outcome": "한글"}, {"outcome": "ok", "extra": 1}):
        assert client.post(f"{READ}/1/synced", json=bad, headers=SUPPORT).status_code == 422


MY = "/api/shop/ideas"


def test_the_customer_sees_the_idea_status_and_the_notice_support_wrote_back(
    client: TestClient,
) -> None:
    client.post(URL, json=_body(text="환불해 주세요 지금 바로"))
    client.post(URL, json=_body(text="생일 쿠폰 이벤트가 있으면 좋겠어요"))
    mine = client.get(MY, params={"buyer_id": "buy_0001"}).json()
    assert [(i["idea_id"], i["status"]) for i in mine["data"]] == [(2, "pending"), (1, "pending")]
    notice = "환불·취소·불량은 1:1 문의로 남겨 주세요"
    body = {"outcome": "use_inquiry", "notice": notice}
    assert client.post(f"{READ}/1/synced", json=body, headers=SUPPORT).status_code == 200
    ok = {"outcome": "received", "ref": "IDEA-2"}
    assert client.post(f"{READ}/2/synced", json=ok, headers=SUPPORT).status_code == 200
    rows = {i["idea_id"]: i for i in client.get(MY, params={"buyer_id": "buy_0001"}).json()["data"]}
    assert rows[1]["status"] == "redirected" and rows[1]["notice"] == notice
    assert rows[2]["status"] == "received" and rows[2]["notice"] is None
    assert set(rows[1]) == {"idea_id", "kind", "preview", "status", "notice", "created_at"}


def test_after_it_is_read_only_the_start_of_the_idea_text_is_kept(client: TestClient) -> None:
    text = "생일 쿠폰 이벤트가 있으면 좋겠어요 그리고 포인트도 쌓였으면 해요 정말로요"
    client.post(URL, json=_body(text=text))
    client.post(f"{READ}/1/synced", json={"outcome": "received"}, headers=SUPPORT)
    (row,) = client.get(MY, params={"buyer_id": "buy_0001"}).json()["data"]
    assert row["preview"] == text[:30] and text not in str(row)


def test_the_idea_list_shows_only_my_ideas_and_checks_the_buyer(client: TestClient) -> None:
    client.post(URL, json=_body())
    client.post(URL, json=_body(buyer_id="buy_0002"))
    assert [
        i["idea_id"] for i in client.get(MY, params={"buyer_id": "buy_0002"}).json()["data"]
    ] == [2]
    assert client.get(MY, params={"buyer_id": "x"}).status_code == 400
    assert client.get(MY).status_code == 422


def test_the_notice_is_short_and_text_only(client: TestClient) -> None:
    client.post(URL, json=_body())
    long = {"outcome": "use_inquiry", "notice": "가" * 121}
    assert client.post(f"{READ}/1/synced", json=long, headers=SUPPORT).status_code == 422


def test_the_synced_paths_of_returns_and_ratings_do_not_take_a_notice(client: TestClient) -> None:
    body = {"outcome": "received", "notice": "안내"}
    assert (
        client.post("/api/shop/admin/returns/7/synced", json=body, headers=SUPPORT).status_code
        == 422
    )


def test_an_idea_refused_for_the_daily_limit_can_be_sent_again_as_the_notice_says(
    client: TestClient,
) -> None:
    client.post(URL, json=_body())
    body = {"outcome": "rate_limited", "notice": "하루 한도를 넘었어요"}
    client.post(f"{READ}/1/synced", json=body, headers=SUPPORT)
    again = client.post(URL, json=_body())  # 같은 글을 다시 낸다
    assert again.json()["data"]["idea_id"] == 1
    (item,) = client.get(READ, headers=SUPPORT).json()["data"]  # 다시 읽을 차례에 올라온다
    assert item["seq"] == 1
    (mine,) = client.get(MY, params={"buyer_id": "buy_0001"}).json()["data"]
    assert mine["status"] == "pending" and mine["notice"] is None


def test_a_closed_idea_other_than_a_daily_limit_one_is_not_reopened(client: TestClient) -> None:
    client.post(URL, json=_body())
    client.post(f"{READ}/1/synced", json={"outcome": "use_inquiry"}, headers=SUPPORT)
    client.post(URL, json=_body())
    assert client.get(READ, headers=SUPPORT).json()["data"] == []

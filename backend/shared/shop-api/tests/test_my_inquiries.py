"""내 문의 목록·상태 읽기: 고객이 받은 번호는 판매 서버 접수 번호(seq)다.

고객은 이 서버가 저장한 값만 읽는다(고객지원을 부르지 않는다). 고객지원이 읽어 간 문의는 고객이 볼
상태·답변을 되돌려 쓰고(POST /admin/inquiries/{seq}/view), 아직 안 쓴 문의는 접수한 내용만 본다.
"""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult
from ax_shop_api import inbox, support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

INTERNAL = "내부 초안·분류·상담원 메모"
SUPPORT = {"Authorization": "Bearer mock:support"}
MARKETING = {"Authorization": "Bearer mock:marketing"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


def _view(status: str, rated: bool, **extra) -> dict:  # noqa: ANN003
    return {
        "id": 999,  # 고객지원 번호: 이 서버가 접수 번호로 바꾼다
        "subject": "고객지원이 본 제목",
        "status": status,
        "order_id": "ord_0001",
        "updated_at": "2026-10-30T00:00:00Z",
        "rated": rated,
        "preview": "미리보기",
    } | extra


class NoCalls:
    """고객 읽기가 고객지원을 부르면 이 대역이 기록한다 (부르면 안 된다)."""

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


def seed(
    db: Db, buyer: str = "buy_0001", *, support_id: int | None = None, text: str = "원문"
) -> int:
    """접수를 하나 넣고, 고객지원이 읽어 갔으면 그 번호를 잇는다 (고객 번호 = 접수 번호)."""
    saved = inbox.add(
        db,
        digest=f"{buyer}-{text}-{support_id}",
        context_id="ctx_shop_aaaaaaaaaaaa",
        buyer_id=buyer,
        customer_name="김하늘",
        order_id="ord_0001",
        subject=f"접수 {text}",
        message=text,
        images=None,
    )
    if support_id is not None:
        inbox.mark_synced(db, saved["seq"], support_id)
    return saved["seq"]


def write_view(client: TestClient, seq: int, view: dict, digest: str = "d1"):  # noqa: ANN201
    body = {"view": view, "view_digest": digest}
    return client.post(f"/api/shop/admin/inquiries/{seq}/view", json=body, headers=SUPPORT)


def test_the_list_shows_intake_numbers_and_the_view_support_wrote_narrowed(
    client: TestClient, db: Db, support: NoCalls
) -> None:
    seqs = [seed(db, support_id=s, text=f"글{s}") for s in (30, 20, 10, 40)]
    views = [
        _view("resolved", False, analysis=INTERNAL, customer_name="김하늘"),
        _view("resolved", True),
        _view("open", False),
        _view("escalated", False),
    ]
    for seq, view in zip(seqs, views, strict=True):
        assert write_view(client, seq, view).status_code == 200
    response = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"})
    assert response.status_code == 200
    body = response.json()
    # 고객이 보는 번호는 접수 번호(최근 순)이고 고객지원 번호(30·20·10·40·999)가 아니다
    assert [(i["id"], i["can_rate"]) for i in body["data"]] == [
        (seqs[3], False),  # 에스컬레이션
        (seqs[2], False),  # 아직 해결되지 않음
        (seqs[1], False),  # 이미 평가함
        (seqs[0], True),  # 해결됐고 아직 평가하지 않음
    ]
    assert body["meta"]["total"] == 4
    assert INTERNAL not in response.text and "김하늘" not in response.text  # 모르는 키는 버린다
    assert support.calls == []


def test_an_inquiry_support_has_not_read_yet_shows_what_was_filed(
    client: TestClient, db: Db, support: NoCalls
) -> None:
    seq = seed(db, text="아직 처리 전인 문의 내용입니다")
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    (row,) = body["data"]
    assert row["id"] == seq and row["status"] == "open" and row["can_rate"] is False
    assert row["rated"] is False and row["preview"].startswith("아직 처리 전인")
    assert support.calls == []


def test_another_buyers_inquiry_is_not_in_the_list(client: TestClient, db: Db) -> None:
    seed(db, "buy_0002", text="남의 문의")
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert body["data"] == [] and "남의 문의" not in str(body)


def test_the_list_is_cut_at_limit_and_total_is_in_meta(client: TestClient, db: Db) -> None:
    for n in range(5):
        seed(db, text=f"글{n}")
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001", "limit": 2}).json()
    assert [i["id"] for i in body["data"]] == [5, 4] and body["meta"]["total"] == 5
    assert (
        client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001", "limit": 0}).status_code
        == 422
    )


def test_no_inquiries_is_an_empty_list(client: TestClient) -> None:
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert body["data"] == [] and body["meta"]["total"] == 0


def test_a_missing_or_wrong_buyer_id_is_refused(client: TestClient, support: NoCalls) -> None:
    assert client.get("/api/shop/inquiries").status_code == 422
    bad = client.get("/api/shop/inquiries", params={"buyer_id": "buy_1"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "INVALID_BUYER_ID")
    assert client.get("/api/shop/inquiries/5", params={"buyer_id": "7"}).status_code == 400
    assert support.calls == []


def test_the_detail_uses_the_intake_number_and_shows_message_text_only(
    client: TestClient, db: Db, support: NoCalls
) -> None:
    for n in range(3):  # 접수 번호가 고객지원 번호와 겹치지 않게 앞에 몇 건을 둔다
        seed(db, "buy_0002", text=f"남{n}")
    seq = seed(db, support_id=5)
    messages = [
        {"id": 1, "role": "customer", "text": "환불해 주세요", "created_at": "t1", "x": INTERNAL},
        {"id": 2, "role": "agent", "text": "처리했습니다", "created_at": "t2"},
    ]
    view = _view("resolved", False) | {"messages": messages}
    assert write_view(client, seq, view).status_code == 200
    response = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["can_rate"] is True and data["id"] == seq != 5  # 고객이 받은 번호 그대로
    assert [set(m) for m in data["messages"]] == [{"id", "role", "text", "created_at"}] * 2
    assert INTERNAL not in response.text
    assert support.calls == []
    listed = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()["data"]
    assert "messages" not in listed[0]  # 목록에는 메시지를 싣지 않는다


def test_the_detail_of_an_unread_inquiry_shows_the_filed_text(
    client: TestClient, db: Db, support: NoCalls
) -> None:
    seq = seed(db, text="아직 처리 전")
    data = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"}).json()["data"]
    assert data["id"] == seq and data["status"] == "open" and data["can_rate"] is False
    assert [(m["role"], m["text"]) for m in data["messages"]] == [("customer", "아직 처리 전")]
    assert support.calls == []


def test_someone_elses_and_missing_inquiries_are_the_same_404(
    client: TestClient, db: Db, support: NoCalls
) -> None:
    seq = seed(db, support_id=5)
    other = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0002"})
    missing = client.get("/api/shop/inquiries/99", params={"buyer_id": "buy_0001"})
    assert (other.status_code, other.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert other.json() == missing.json()
    assert support.calls == []


def test_the_inquiry_number_is_an_integer_of_one_or_more(client: TestClient) -> None:
    assert client.get("/api/shop/inquiries/0", params={"buyer_id": "buy_0001"}).status_code == 422
    assert client.get("/api/shop/inquiries/abc", params={"buyer_id": "buy_0001"}).status_code == 422


# ---------------------------------------------------------------- 되돌려 쓰기·열린 문의 읽기


def test_open_views_are_listed_with_their_digest_until_resolved_and_rated(
    client: TestClient, db: Db
) -> None:
    first, second = seed(db, support_id=1, text="a"), seed(db, support_id=2, text="b")
    seed(db, text="아직 안 읽음")  # 읽어 가지 않은 문의는 목록에 없다
    read = "/api/shop/admin/inquiries/open"
    page = client.get(read, headers=SUPPORT).json()
    assert [(i["seq"], i["view_digest"]) for i in page["data"]] == [(first, None), (second, None)]
    assert page["meta"]["next_after"] is None
    write_view(client, first, _view("open", False), "d-open")
    write_view(client, second, _view("resolved", True), "d-final")  # 끝난 문의는 더 안 본다
    page = client.get(read, headers=SUPPORT).json()
    assert [(i["seq"], i["view_digest"]) for i in page["data"]] == [(first, "d-open")]


def test_open_views_come_in_pages_with_a_next_cursor(client: TestClient, db: Db) -> None:
    seqs = [seed(db, support_id=n, text=f"글{n}") for n in range(1, 4)]
    read = "/api/shop/admin/inquiries/open"
    one = client.get(read, params={"limit": 2}, headers=SUPPORT).json()
    assert [i["seq"] for i in one["data"]] == seqs[:2] and one["meta"]["next_after"] == seqs[1]
    two = client.get(read, params={"after": seqs[1], "limit": 2}, headers=SUPPORT).json()
    assert [i["seq"] for i in two["data"]] == seqs[2:] and two["meta"]["next_after"] is None


def test_writing_the_same_digest_again_changes_nothing_and_unknown_numbers_are_404(
    client: TestClient, db: Db
) -> None:
    seq = seed(db, support_id=5)
    assert write_view(client, seq, _view("open", False), "d1").status_code == 200
    assert write_view(client, seq, _view("resolved", False), "d1").status_code == 200
    (row,) = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()["data"]
    assert row["status"] == "open"  # 같은 지문이면 다시 쓰지 않는다
    assert write_view(client, 99, _view("open", False)).status_code == 404


def test_only_support_and_admin_may_read_or_write_views(client: TestClient, db: Db) -> None:
    seq = seed(db, support_id=5)
    read = "/api/shop/admin/inquiries/open"
    assert client.get(read).status_code == 401
    assert client.get(read, headers=MARKETING).status_code == 403
    body = {"view": _view("open", False), "view_digest": "d"}
    url = f"/api/shop/admin/inquiries/{seq}/view"
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=MARKETING).status_code == 403
    for bad in ({}, {"view": {}}, {"view": {}, "view_digest": ""}, body | {"extra": 1}):
        assert client.post(url, json=bad, headers=SUPPORT).status_code == 422


def test_a_synced_inquiry_no_longer_keeps_the_text_copy_and_shows_no_text_before_the_view(
    client: TestClient, db: Db
) -> None:
    seq = seed(db, support_id=5, text="읽어 간 뒤에는 지운다")
    with db.tx() as conn:
        row = conn.execute("SELECT message FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
    assert row["message"] == ""
    (row,) = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()["data"]
    assert row["preview"] == ""  # 본문은 보이지 않는다
    detail = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"}).json()
    assert detail["data"]["messages"] == []  # 고객지원이 값을 되돌려 쓰면 채워진다


def test_an_inquiry_support_cannot_find_any_more_is_taken_off_the_customers_screen(
    client: TestClient, db: Db
) -> None:
    """개인정보로 지웠거나 고객지원이 다시 시작해 없는 문의는 되돌려 쓴 값도 함께 지운다."""
    kept = seed(db, support_id=5, text="a")
    gone = seed(db, support_id=6, text="b")
    for seq in (kept, gone):
        assert (
            write_view(client, seq, _view("open", False, preview="원문 미리보기")).status_code
            == 200
        )
    done = client.post(f"/api/shop/admin/inquiries/{gone}/gone", headers=SUPPORT)
    assert done.status_code == 200
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert [i["id"] for i in body["data"]] == [kept] and "원문 미리보기" in str(body["data"][0])
    missing = client.get(f"/api/shop/inquiries/{gone}", params={"buyer_id": "buy_0001"})
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    opened = client.get("/api/shop/admin/inquiries/open", headers=SUPPORT).json()["data"]
    assert [i["seq"] for i in opened] == [kept]  # 더 읽지 않는다
    assert client.post("/api/shop/admin/inquiries/99/gone", headers=SUPPORT).status_code == 404
    assert client.post(f"/api/shop/admin/inquiries/{kept}/gone").status_code == 401
    other = client.post(f"/api/shop/admin/inquiries/{kept}/gone", headers=MARKETING)
    assert other.status_code == 403


def test_an_inquiry_set_aside_after_repeated_failures_tells_the_customer(
    client: TestClient, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INBOX_MAX_ATTEMPTS", "1")
    seq = seed(db, text="끝내 처리되지 않는 문의")
    inbox.report_failure(db, seq, "INGEST_FAILED")
    (row,) = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()["data"]
    assert row["intake_failed"] is True and "고객센터" in row["preview"]
    assert "끝내 처리되지 않는" not in row["preview"]
    detail = client.get(f"/api/shop/inquiries/{seq}", params={"buyer_id": "buy_0001"}).json()
    assert "고객센터" in detail["data"]["notice"]


def test_a_resolved_inquiry_is_not_refreshed_after_the_resolved_period(
    client: TestClient, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    seq = seed(db, support_id=5)
    write_view(client, seq, _view("resolved", False), "d-res")
    read = "/api/shop/admin/inquiries/open"
    assert [i["seq"] for i in client.get(read, headers=SUPPORT).json()["data"]] == [seq]
    with db.tx() as conn:  # 해결된 지 8일이 지났다
        conn.execute(
            "UPDATE inquiry_inbox SET view_at = '2026-01-01T00:00:00Z' WHERE seq = ?", (seq,)
        )
    assert client.get(read, headers=SUPPORT).json()["data"] == []
    open_one = seed(db, support_id=6, text="열린 문의")
    write_view(client, open_one, _view("open", False), "d-open")
    with db.tx() as conn:  # 열린 문의는 오래돼도 계속 본다
        conn.execute(
            "UPDATE inquiry_inbox SET view_at = '2026-01-01T00:00:00Z' WHERE seq = ?", (open_one,)
        )
    assert [i["seq"] for i in client.get(read, headers=SUPPORT).json()["data"]] == [open_one]


def test_support_can_take_an_inquiry_off_by_its_own_number_right_when_it_purges(
    client: TestClient, db: Db
) -> None:
    kept, gone = seed(db, support_id=5, text="a"), seed(db, support_id=6, text="b")
    for seq in (kept, gone):
        write_view(client, seq, _view("open", False, preview="원문 미리보기"))
    url = "/api/shop/admin/inquiries/by-support/6/gone"
    assert client.post(url, headers=SUPPORT).status_code == 200
    body = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"}).json()
    assert [i["id"] for i in body["data"]] == [kept]
    with db.tx() as conn:  # 되돌려 쓴 값(고객 원문)도 함께 지운다
        row = conn.execute(
            "SELECT view, message FROM inquiry_inbox WHERE seq = ?", (gone,)
        ).fetchone()
    assert row["view"] is None and row["message"] == ""
    assert client.post(url, headers=SUPPORT).status_code == 200  # 다시 불러도 같다
    assert (
        client.post("/api/shop/admin/inquiries/by-support/99/gone", headers=SUPPORT).status_code
        == 404
    )
    assert client.post(url).status_code == 401
    assert client.post(url, headers=MARKETING).status_code == 403


def test_a_sweep_lists_resolved_and_rated_inquiries_too_so_a_late_deletion_is_not_missed(
    client: TestClient, db: Db
) -> None:
    seq = seed(db, support_id=5)
    write_view(client, seq, _view("resolved", True), "d-final")  # 평가까지 끝나 보통은 안 읽는다
    read = "/api/shop/admin/inquiries/open"
    assert client.get(read, headers=SUPPORT).json()["data"] == []
    swept = client.get(read, params={"sweep": "true"}, headers=SUPPORT).json()["data"]
    assert [i["seq"] for i in swept] == [seq]
    client.post(f"/api/shop/admin/inquiries/{seq}/gone", headers=SUPPORT)
    assert client.get(read, params={"sweep": "true"}, headers=SUPPORT).json()["data"] == []

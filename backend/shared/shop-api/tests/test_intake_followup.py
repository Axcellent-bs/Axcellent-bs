"""문의·클레임 접수 후속(#964 리뷰): 실패 접수 격리, 안정된 멱등 키, 재신청, 반려 재확인."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import claim_inbox, inbox
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SUPPORT = {"Authorization": "Bearer mock:support"}
MARKETING = {"Authorization": "Bearer mock:marketing"}
READ_I = "/api/shop/admin/inquiries"
READ_C = "/api/shop/admin/claims"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_INQUIRY_RATE_PER_BUYER", "500")
    monkeypatch.setenv("SHOP_INQUIRY_RATE_GLOBAL", "5000")
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _order(client: TestClient, buyer: str = "buy_0001") -> str:
    made = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": "prd_0001", "qty": 1}]},
    ).json()["data"]
    return client.post(f"/api/shop/orders/{made['order_id']}/payments", json={}).json()["data"][
        "order_id"
    ]


def _inquire(client: TestClient, order: str, body: str) -> int:
    done = client.post(
        "/api/shop/inquiries", json={"buyer_id": "buy_0001", "order_id": order, "body": body}
    )
    assert done.status_code == 201
    return done.json()["data"]["id"]


def _claim(client: TestClient, order: str, **extra) -> int:  # noqa: ANN003
    body = {
        "buyer_id": "buy_0001",
        "customer_name": "홍",
        "order_id": order,
        "type": "return",
        "reason": "defect",
        "description": "찢어짐",
    } | extra
    done = client.post("/api/shop/claims", json=body)
    assert done.status_code == 201
    return done.json()["data"]["id"]


def _fail(client: TestClient, kind: str, seq: int, reason: str = "NOT_PROCESSED"):  # noqa: ANN202
    path = READ_I if kind == "inquiries" else READ_C
    return client.post(f"{path}/{seq}/failed", json={"reason": reason}, headers=SUPPORT)


def _ago(db: Db, table: str, seq: int, **columns: str) -> None:
    """다시 읽을 시각 같은 값을 직접 당겨 시간이 지난 것처럼 만든다."""
    sets = ", ".join(f"{k} = ?" for k in columns)
    with db.tx() as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE seq = ?", (*columns.values(), seq))  # noqa: S608


# --- 2. 실패한 접수는 앞을 막지 않는다


def test_a_failed_inquiry_waits_and_the_next_one_is_read_first(client: TestClient) -> None:
    order = _order(client)
    first = _inquire(client, order, "계속 실패하는 문의")
    second = _inquire(client, order, "뒤에 온 문의")
    done = _fail(client, "inquiries", first)
    assert done.status_code == 200
    data = done.json()["data"]
    assert data["attempts"] == 1 and data["failed"] is False and data["retry_after"]
    body = client.get(READ_I, params={"limit": 1}, headers=SUPPORT).json()
    assert [i["seq"] for i in body["data"]] == [second]  # 기다리는 문의는 이번 주기에 건너뛴다
    assert body["meta"]["total"] == 2 and body["meta"]["failed"] == 0


def test_a_retry_is_offered_again_after_the_wait_with_fewer_failures_first(
    client: TestClient, db: Db
) -> None:
    order = _order(client)
    first = _inquire(client, order, "실패했던 문의")
    second = _inquire(client, order, "새 문의")
    _fail(client, "inquiries", first)
    past = (datetime.now(UTC) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _ago(db, "inquiry_inbox", first, retry_after=past)
    order_read = [i["seq"] for i in client.get(READ_I, headers=SUPPORT).json()["data"]]
    assert order_read == [second, first]  # 실패가 적은 접수가 먼저, 실패했던 접수는 뒤에


def test_a_failing_inquiry_is_set_aside_after_the_limit_and_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INBOX_MAX_ATTEMPTS", "3")
    order = _order(client)
    seq = _inquire(client, order, "끝내 처리되지 않는 문의")
    results = [_fail(client, "inquiries", seq).json()["data"] for _ in range(3)]
    assert [r["failed"] for r in results] == [False, False, True]
    body = client.get(READ_I, headers=SUPPORT).json()
    assert body["data"] == [] and body["meta"]["total"] == 0 and body["meta"]["failed"] == 1
    again = _fail(client, "inquiries", seq).json()["data"]  # 이미 뺀 접수는 더 세지 않는다
    assert again["attempts"] == 3 and again["failed"] is True


def test_a_synced_inquiry_ignores_a_late_failure_report(client: TestClient) -> None:
    seq = _inquire(client, _order(client), "문의")
    client.post(f"{READ_I}/{seq}/synced", json={"support_inquiry_id": 4}, headers=SUPPORT)
    done = _fail(client, "inquiries", seq).json()["data"]
    assert done["attempts"] == 0 and done["failed"] is False


def test_a_claim_that_keeps_failing_is_closed_for_the_customer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_INBOX_MAX_ATTEMPTS", "2")
    order = _order(client)
    seq = _claim(client, order)
    assert _fail(client, "claims", seq).json()["data"]["failed"] is False
    assert _fail(client, "claims", seq).json()["data"]["failed"] is True
    body = client.get(READ_C, headers=SUPPORT).json()
    assert body["data"] == [] and body["meta"]["failed"] == 1
    (mine,) = client.get("/api/shop/claims", params={"buyer_id": "buy_0001"}).json()["data"]
    assert mine["status"] == "not_accepted"  # 고객에게는 "접수하지 못했다"로 닫힌다
    assert mine["result"]["outcome"] == "intake_failed"


def test_failure_reports_need_a_token_a_known_number_and_a_short_code(client: TestClient) -> None:
    seq = _inquire(client, _order(client), "문의")
    body = {"reason": "X"}
    assert client.post(f"{READ_I}/{seq}/failed", json=body).status_code == 401
    assert client.post(f"{READ_I}/{seq}/failed", json=body, headers=MARKETING).status_code == 403
    assert _fail(client, "inquiries", 99).status_code == 404
    assert _fail(client, "claims", 99).status_code == 404
    for bad in (
        {},
        {"reason": ""},
        {"reason": "가" * 5},
        {"reason": "x" * 41},
        {"reason": "a", "x": 1},
    ):
        res = client.post(f"{READ_I}/{seq}/failed", json=bad, headers=SUPPORT)
        assert res.status_code == 422


def test_backoff_doubles_up_to_the_cap() -> None:
    assert [inbox.backoff_seconds(n) for n in (1, 2, 3, 4, 5, 6, 7, 8)] == [
        60, 120, 240, 480, 960, 1800, 1800, 1800,
    ]  # fmt: skip


# --- 4. 멱등 키는 접수 번호가 아니라 접수 지문


def test_the_read_key_is_the_intake_digest_not_the_number(client: TestClient) -> None:
    order = _order(client)
    _inquire(client, order, "키 확인 문의")
    _claim(client, order)
    inquiry = client.get(READ_I, headers=SUPPORT).json()["data"][0]
    claim = client.get(READ_C, headers=SUPPORT).json()["data"][0]
    assert len(inquiry["key"]) >= 32 and len(claim["key"]) >= 32
    assert str(inquiry["seq"]) not in (inquiry["key"], "")  # 번호와 다른 값이다
    assert "키 확인 문의" not in inquiry["key"] and "찢어짐" not in claim["key"]


def test_a_fresh_database_gives_the_same_number_but_a_different_key() -> None:
    def first_key(body: str) -> tuple[int, str]:
        fresh = TestClient(create_app(Db(":memory:")))
        order = _order(fresh)
        _inquire(fresh, order, body)
        item = fresh.get(READ_I, headers=SUPPORT).json()["data"][0]
        return item["seq"], item["key"]

    a, b = first_key("첫 번째 데이터베이스의 문의"), first_key("다시 만든 데이터베이스의 다른 문의")
    assert a[0] == b[0] == 1 and a[1] != b[1]  # 번호는 겹쳐도 키는 겹치지 않는다


# --- 8. 반려 뒤 같은 내용으로 다시 신청


def _write(client: TestClient, seq: int, status: str, claim_id: int = 7, **result) -> object:  # noqa: ANN003
    body = {"support_claim_id": claim_id, "status": status, "result": {"id": claim_id, **result}}
    return client.post(f"{READ_C}/{seq}/result", json=body, headers=SUPPORT)


def test_a_claim_filed_again_after_a_rejection_is_a_new_request(client: TestClient) -> None:
    order = _order(client)
    first = _claim(client, order)
    assert _claim(client, order) == first  # 진행 중이면 같은 접수
    _write(client, first, "rejected")
    second = _claim(client, order)
    assert second != first
    keys = {c["seq"]: c["key"] for c in client.get(READ_C, headers=SUPPORT).json()["data"]}
    assert list(keys) == [second]  # 새 접수가 읽을 차례에 들어온다
    assert keys[second].startswith(
        claim_inbox.digest("buy_0001", order, "return", "defect", None, False, "찢어짐")
    )
    assert _claim(client, order) == second  # 새 접수도 진행 중이면 다시 새로 만들지 않는다


def test_a_request_support_did_not_accept_can_be_filed_again(client: TestClient) -> None:
    order = _order(client)
    first = _claim(client, order)
    client.post(
        f"{READ_C}/{first}/result",
        json={
            "status": "not_accepted",
            "result": {"message": "진행 중", "outcome": "duplicate_claim"},
        },
        headers=SUPPORT,
    )
    assert _claim(client, order) != first


def test_a_completed_claim_does_not_open_a_second_one(client: TestClient) -> None:
    order = _order(client)
    first = _claim(client, order)
    _write(client, first, "completed")
    assert _claim(client, order) == first  # 환불까지 끝난 같은 신청은 되살리지 않는다


# --- 5. 반려된 신청도 일정 기간은 다시 확인


def test_a_rejected_claim_stays_open_for_a_week_then_drops_out(client: TestClient, db: Db) -> None:
    seq = _claim(client, _order(client))
    _write(client, seq, "rejected")
    open_now = client.get(READ_C, params={"state": "open"}, headers=SUPPORT).json()["data"]
    assert [c["seq"] for c in open_now] == [seq]  # 검토 시간 초과 반려는 다시 진행될 수 있다
    old = (datetime.now(UTC) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _ago(db, "claim_inbox", seq, updated_at=old)
    assert client.get(READ_C, params={"state": "open"}, headers=SUPPORT).json()["data"] == []


def test_a_rejected_claim_that_restarts_is_updated_on_the_shop(client: TestClient) -> None:
    seq = _claim(client, _order(client))
    _write(client, seq, "rejected")
    _write(client, seq, "approved")  # 환불을 다시 진행해 승인됐다
    (mine,) = client.get("/api/shop/claims", params={"buyer_id": "buy_0001"}).json()["data"]
    assert mine["status"] == "approved"


# --- 6·7. 지문과 쪽 나누기


def test_the_result_digest_changes_when_a_shown_value_changes(client: TestClient) -> None:
    seq = _claim(client, _order(client))
    _write(client, seq, "approved", refund_paid=0)
    first = client.get(READ_C, params={"state": "open"}, headers=SUPPORT).json()["data"][0]
    _write(client, seq, "approved", refund_paid=21000)  # 상태는 그대로, 환불 완료액만 바뀜
    second = client.get(READ_C, params={"state": "open"}, headers=SUPPORT).json()["data"][0]
    assert first["status"] == second["status"] and first["result_digest"] != second["result_digest"]
    same = claim_inbox.result_digest({"id": 7, "refund_paid": 21000})
    assert second["result_digest"] == same  # 읽는 쪽이 같은 방식으로 셀 수 있다


def test_open_claims_come_in_pages_with_a_next_cursor(client: TestClient) -> None:
    orders = [_order(client) for _ in range(5)]
    seqs = [_claim(client, o) for o in orders]
    for n, seq in enumerate(seqs):
        _write(client, seq, "needs_review", claim_id=100 + n)
    first = client.get(READ_C, params={"state": "open", "limit": 2}, headers=SUPPORT).json()
    assert [c["seq"] for c in first["data"]] == seqs[:2] and first["meta"]["next_after"] == seqs[1]
    second = client.get(
        READ_C, params={"state": "open", "limit": 2, "after": seqs[1]}, headers=SUPPORT
    ).json()
    assert [c["seq"] for c in second["data"]] == seqs[2:4]
    last = client.get(
        READ_C, params={"state": "open", "limit": 2, "after": seqs[3]}, headers=SUPPORT
    ).json()
    assert [c["seq"] for c in last["data"]] == seqs[4:] and last["meta"]["next_after"] is None
    assert (
        client.get(READ_C, params={"state": "open", "after": -1}, headers=SUPPORT).status_code
        == 422
    )


# ---------------------------------------------------------------- 막힌 접수 목록·다시 시도

STUCK = "/api/shop/admin/intake/stuck"


def _stuck_both(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    monkeypatch.setenv("SHOP_INBOX_MAX_ATTEMPTS", "1")
    order = _order(client)
    i = _inquire(client, order, "처리되지 않는 문의 원문")
    c = _claim(client, order)
    _fail(client, "inquiries", i, "INGEST_FAILED")
    _fail(client, "claims", c, "INGEST_FAILED")
    return i, c


def test_stuck_intake_lists_numbers_and_reasons_without_customer_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    i, c = _stuck_both(client, monkeypatch)
    res = client.get(STUCK, headers=SUPPORT)
    body = res.json()
    assert res.status_code == 200 and body["meta"]["total"] == 2
    kinds = {(r["kind"], r["seq"]) for r in body["data"]}
    assert kinds == {("inquiry", i), ("claim", c)}
    assert all(r["reason"] == "INGEST_FAILED" and r["attempts"] == 1 for r in body["data"])
    assert "원문" not in res.text and "찢어짐" not in res.text


def test_retrying_a_stuck_inquiry_puts_it_back_in_the_read_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    i, _ = _stuck_both(client, monkeypatch)
    assert client.get(READ_I, headers=SUPPORT).json()["data"] == []
    done = client.post(f"{READ_I}/{i}/retry", headers=SUPPORT)
    assert done.status_code == 200 and done.json()["data"] == {"seq": i, "requeued": True}
    (item,) = client.get(READ_I, headers=SUPPORT).json()["data"]
    assert item["seq"] == i
    assert all(r["kind"] != "inquiry" for r in client.get(STUCK, headers=SUPPORT).json()["data"])


def test_retrying_a_stuck_claim_reopens_it_for_the_customer_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, c = _stuck_both(client, monkeypatch)
    mine = client.get("/api/shop/claims", params={"buyer_id": "buy_0001"}).json()["data"]
    assert mine[0]["status"] == "not_accepted"
    assert client.post(f"{READ_C}/{c}/retry", headers=SUPPORT).json()["data"]["requeued"] is True
    mine = client.get("/api/shop/claims", params={"buyer_id": "buy_0001"}).json()["data"]
    assert mine[0]["status"] == "received" and mine[0]["result"] is None
    assert [r["seq"] for r in client.get(READ_C, headers=SUPPORT).json()["data"]] == [c]


def test_retry_leaves_items_that_are_not_stuck_and_checks_numbers_and_tokens(
    client: TestClient,
) -> None:
    order = _order(client)
    i = _inquire(client, order, "아직 정상인 문의")
    again = client.post(f"{READ_I}/{i}/retry", headers=SUPPORT).json()["data"]
    assert again["requeued"] is False
    assert client.post(f"{READ_I}/999/retry", headers=SUPPORT).status_code == 404
    assert client.post(f"{READ_C}/999/retry", headers=SUPPORT).status_code == 404
    assert client.get(STUCK).status_code == 401
    assert client.get(STUCK, headers=MARKETING).status_code == 403
    assert client.post(f"{READ_I}/{i}/retry", headers=MARKETING).status_code == 403

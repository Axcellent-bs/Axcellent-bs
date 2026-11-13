"""재무 대시보드 API 테스트 (재무 재설계 11장 응답 형식). 목 모드·가짜 안전 장치, 네트워크 없음."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import ExecuteDecision, HoldReason
from finance_agent import finance_api, handlers
from finance_agent.main import app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}
META_KEYS = {"mock", "collected_at", "source", "unavailable"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _get(client: TestClient, url: str, **params: Any) -> dict:
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "meta"}
    assert set(body["meta"]) >= META_KEYS
    assert (body["meta"]["mock"], body["meta"]["source"]) == (True, "seed")
    datetime.fromisoformat(body["meta"]["collected_at"].replace("Z", "+00:00"))
    return body


def _fake_safety(monkeypatch: pytest.MonkeyPatch, decision: ExecuteDecision) -> list[dict]:
    calls: list[dict] = []

    async def fake(action: str, params: dict, context: Any, evidence: Any) -> ExecuteDecision:
        calls.append(
            {"action": action, "params": params, "key": context.idempotency_key, "ctx": context}
        )
        return decision

    monkeypatch.setattr(finance_api, "execute", fake)
    return calls


HELD = ExecuteDecision(
    execution_id="exe_77",
    grade="C",
    status="held",
    hold_reason=HoldReason.OVER_THRESHOLD,
    review_id="rev_77",
)


# ---------------------------------------------------------------- 조회 형식
def test_overview_shape(client: TestClient) -> None:
    data = _get(client, "/api/finance/overview")["data"]
    assert data["date"] == "2026-10-23"
    assert set(data["kpis"]) == {
        "gmv",
        "commission_revenue",
        "refund_total",
        "coupon_cost",
        "pg_fee",
        "net_result",
    }
    k = data["kpis"]
    assert k["net_result"] == k["commission_revenue"] - k["coupon_cost"] - k["pg_fee"]
    assert all(isinstance(v, int) for v in k.values())
    kinds = [t["kind"] for t in data["todo"]]
    # 판매자 정산은 고객지원이 한다. 재무 할 일에 지급 승인 요청·승인이 없다
    # (고객지원이 꺼져 있으면 기한 알림도 지어내지 않는다)
    # 팀 예산 확정은 인사 AI가 한다(11/13). 재무 할 일에는 올리지 않는다
    assert kinds == ["closing_confirm", "recon_exception"]
    for t in data["todo"]:
        assert {"kind", "title", "count", "amount", "link", "action"} <= set(t)
        assert t["count"] > 0
    # 할 일마다 사람이 바로 누를 결정이 붙는다
    actions = {t["kind"]: t["action"] for t in data["todo"]}
    assert actions["closing_confirm"] == {
        "type": "closing_confirm",
        "period": "day",
        "date": "2026-10-22",
    }
    assert actions["recon_exception"]["type"] == "recon_note"
    assert "team_budget" not in actions
    for t in data["todo"]:
        if t["kind"] == "recon_exception":
            assert t["items"] and all(
                {"ref", "title", "detail", "amount"} <= set(i) for i in t["items"]
            )
    gone = ("settlement_request", "review_decision")
    assert not any(t["action"]["type"] in gone for t in data["todo"])


def test_closing_shape_and_params(client: TestClient) -> None:
    day = _get(client, "/api/finance/closing")["data"]
    assert (day["period"], day["date"], day["status"]) == ("day", "2026-10-23", "draft")
    assert day["commentary_source"] == "template" and day["commentary"]
    assert [ln["code"] for ln in day["lines"]][-1] == "net_result"
    month = _get(client, "/api/finance/closing", period="month", date="2026-10-05")["data"]
    assert (month["date"], month["from"]) == ("2026-10-01", "2026-10-01T00:00:00Z")
    week = _get(client, "/api/finance/closing", period="week", date="2026-10-22")["data"]
    assert (week["date"], week["from"], week["to"]) == (
        "2026-10-19",
        "2026-10-19T00:00:00Z",
        "2026-10-26T00:00:00Z",
    )
    for bad in ({"period": "year"}, {"date": "어제"}):
        response = client.get("/api/finance/closing", params=bad)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


def test_reconciliation_shape(client: TestClient) -> None:
    body = _get(client, "/api/finance/reconciliation")
    data = body["data"]
    assert set(data) == {"from", "to", "pg_coverage", "counts", "totals", "items", "shown"}
    assert set(data["counts"]) == {
        "matched", "mismatch", "missing_in_pg", "pending", "fee_anomaly", "missing_in_ours",
    }  # fmt: skip
    assert set(data["totals"]) == {"order_amount", "pg_amount", "pg_fee", "pay_out"}
    assert body["meta"]["pg_source"] == "mock_toss_settlement"
    one = _get(client, "/api/finance/reconciliation", date="2026-10-20")["data"]
    assert (one["from"], one["to"]) == ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")
    assert client.get("/api/finance/reconciliation", params={"date": "x"}).status_code == 400


def test_reconciliation_sends_only_what_the_screen_shows(client: TestClient) -> None:
    """기본은 사람이 볼 불일치만. 일치·정산 전은 화면이 켤 때만 받는다. 건수는 늘 전체다."""
    data = _get(client, "/api/finance/reconciliation")["data"]
    statuses = {i["status"] for i in data["items"]}
    assert statuses and statuses <= {"mismatch", "missing_in_pg", "fee_anomaly", "missing_in_ours"}
    assert data["shown"]["statuses"] == [
        "mismatch", "missing_in_pg", "fee_anomaly", "missing_in_ours",
    ]  # fmt: skip
    assert data["shown"]["total"] == len(data["items"])
    every = _get(client, "/api/finance/reconciliation", status="all")["data"]
    assert len(every["items"]) == sum(every["counts"].values())
    assert every["counts"] == data["counts"] and every["totals"] == data["totals"]
    matched = _get(client, "/api/finance/reconciliation", status="matched,pending")["data"]
    assert {i["status"] for i in matched["items"]} <= {"matched", "pending"}
    assert matched["shown"]["total"] == data["counts"]["matched"] + data["counts"]["pending"]
    one = _get(client, "/api/finance/reconciliation", status="all", limit="1")["data"]
    assert len(one["items"]) == 1 and one["shown"]["total"] == len(every["items"])
    for bad in ({"status": "ok"}, {"status": ","}, {"limit": "0"}, {"limit": "5001"}):
        response = client.get("/api/finance/reconciliation", params=bad)
        assert response.status_code == 400, bad


# ---------------------------------------------------------- 판매자 정산 (고객지원 것을 받기만)
def test_settlements_pass_through_support(client: TestClient, support, kit) -> None:
    rows = [{"seller_id": "sel_0001", "payout": 13000}]
    batch = {
        "status": "requested",
        "review_id": "rev_1",
        "execution_id": "exe_1",
        "total_payout": 13000,
        "seller_ids": ["sel_0001"],
    }
    support.weeks["2026-W42"] = kit.statement("2026-W42", rows, batch=batch)
    body = client.get("/api/finance/settlements").json()
    sett, meta = body["data"], body["meta"]
    assert set(sett) == {"period", "from", "to", "pay_date", "statements", "batch", "applied_rules"}
    assert sett == support.weeks["2026-W42"]  # 고객지원이 준 그대로 (meta만 뺌)
    assert set(meta) >= META_KEYS and meta["provider"] == "support"
    assert meta["source"] == "shop-api"  # 고객지원이 어디서 읽었는지
    assert support.calls[-1] == ("seller_settlement", {"period": None})
    w41 = client.get("/api/finance/settlements", params={"period": "2026-W41"}).json()
    assert w41["data"]["period"] == "2026-W41"
    bad = client.get("/api/finance/settlements", params={"period": "41"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_request")


def test_deadlines_pass_through_support(client: TestClient, support) -> None:
    body = client.get("/api/finance/settlements/deadlines").json()
    assert body["data"] == support.deadlines and body["meta"]["provider"] == "support"
    assert support.calls == [("seller_settlement_deadlines", {})]


@pytest.mark.parametrize("url", ["/api/finance/settlements", "/api/finance/settlements/deadlines"])
def test_settlements_are_503_when_support_is_down(client: TestClient, url: str) -> None:
    """고객지원에 닿지 않으면 재무가 정산을 계산해 채우지 않는다."""
    response = client.get(url)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "support_unavailable"


def test_support_failure_is_503_too(client: TestClient, support) -> None:
    support.down = True
    response = client.get("/api/finance/settlements")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "support_unavailable")


def test_settlement_feed_remembers_answers_briefly(client: TestClient, support) -> None:
    for _ in range(3):
        assert client.get("/api/finance/settlements").status_code == 200
    assert len(support.calls) == 1  # 화면이 몇 초마다 불러도 고객지원에 다시 묻지 않는다


def test_finance_no_longer_requests_payouts(client: TestClient, support, monkeypatch) -> None:
    """정산 묶음 요청 경로가 없고, 어떤 화면도 `execute("confirm_settlement")`를 부르지 않는다."""
    calls = _fake_safety(monkeypatch, HELD)
    for body in ({}, {"period": "2026-W42"}):
        response = client.post("/api/finance/settlements/batch", json=body, headers=HUMAN)
        assert response.status_code in (404, 405)
    for url in (
        "/api/finance/overview",
        "/api/finance/settlements",
        "/api/finance/settlements/deadlines",
        "/api/finance/payables",
        "/api/finance/cashflow",
        "/api/finance/close-checklist",
        "/api/finance/bank/outflows",
    ):
        assert client.get(url).status_code == 200, url
    assert calls == []
    assert handlers.state().records.legacy_batches == {}  # 재무는 묶음을 기록하지 않는다


def test_finance_code_never_asks_for_settlement_execution() -> None:
    """재무 코드 어디에도 `execute("confirm_settlement", ...)` 호출이 없다 (설명 글은 빼고 본다)."""
    import ast
    from pathlib import Path

    src = Path(finance_api.__file__).parent
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            first = node.args[0]
            assert not (
                name == "execute"
                and isinstance(first, ast.Constant)
                and first.value == "confirm_settlement"
            ), path.name


def test_missing_sources_are_reported_in_meta(client: TestClient, kit) -> None:
    async def live_like():
        return kit.books(
            mock=False,
            source="shop-api",
            unavailable=["executions"],
            executions=None,
        )

    handlers.state().books_loader = live_like
    body = client.get("/api/finance/payables").json()
    assert body["meta"]["unavailable"] == ["executions"]
    assert (body["meta"]["mock"], body["meta"]["source"]) == (False, "shop-api")


def test_closing_without_orders_is_unknown_not_zero(client: TestClient, kit) -> None:
    """주문 조회가 실패하면 매출을 0원으로 보이지 않는다 (data closing_report 확인 사항)."""
    when = kit.at("2026-10-23", "09:00")
    coupons = [
        {"coupon_id": "cpn_1", "total_value": 3000, "funded_by": "company", "created_at": when}
    ]

    async def orders_down():
        return kit.books(mock=False, source="shop-api", unavailable=["orders"], coupons=coupons)

    handlers.state().books_loader = orders_down
    body = client.get("/api/finance/closing").json()
    amounts = {ln["code"]: ln["amount"] for ln in body["data"]["lines"]}
    assert amounts == {
        "gmv": None,
        "commission_revenue": None,
        "coupon_cost": -3000,
        "refund_total": None,
        "pg_fee": None,
        "net_result": None,
    }
    assert "orders" in body["meta"]["unavailable"]
    assert "확인할 수 없습니다" in body["data"]["commentary"]
    overview = client.get("/api/finance/overview").json()["data"]
    assert overview["kpis"]["gmv"] is None and overview["kpis"]["coupon_cost"] == 3000
    todo = next(t for t in overview["todo"] if t["kind"] == "closing_confirm")
    assert todo["amount"] is None
    # 확인할 수 없는 숫자로는 결산을 확정하지 않는다
    body = {"period": "day", "date": "2026-10-22"}
    response = client.post("/api/finance/closing/confirm", json=body, headers=HUMAN)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "books_unavailable"
    assert handlers.state().records.closings == {}


# ---------------------------------------------------------------- 사람 결정
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer mock:finance"},
        {"Authorization": "Bearer nope"},
        {"Authorization": "human:taeyoon"},
    ],
)
def test_closing_confirm_needs_human_token(client: TestClient, headers: dict) -> None:
    response = client.post("/api/finance/closing/confirm", json={"period": "day"}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert handlers.state().records.closings == {}


def test_closing_confirm_records_person(client: TestClient) -> None:
    body = {"period": "day", "date": "2026-10-22"}
    response = client.post("/api/finance/closing/confirm", json=body, headers=HUMAN)
    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["status"], data["confirmed_by"], data["date"]) == (
        "confirmed",
        "taeyoon",
        "2026-10-22",
    )
    assert data["confirmed_at"].endswith("Z")
    again = client.get("/api/finance/closing", params={"date": "2026-10-22"}).json()["data"]
    assert (again["status"], again["confirmed_by"]) == ("confirmed", "taeyoon")
    kinds = [t["kind"] for t in client.get("/api/finance/overview").json()["data"]["todo"]]
    assert "closing_confirm" not in kinds  # 어제 결산을 확정했으니 할 일에서 빠진다
    other = client.post(
        "/api/finance/closing/confirm", json=body, headers={"Authorization": "Bearer human:someone"}
    )
    assert other.json()["data"]["confirmed_by"] == "taeyoon"  # 처음 확정한 사람이 남는다
    bad = client.post("/api/finance/closing/confirm", json={"period": "year"}, headers=HUMAN)
    assert bad.status_code == 400


def test_configured_human_tokens(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "byunghwan:s3cret")
    ok = client.post(
        "/api/finance/closing/confirm",
        json={"date": "2026-10-22"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.json()["data"]["confirmed_by"] == "byunghwan"
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK")  # 목 모드가 아니면 human:<이름>은 받지 않는다
    monkeypatch.delenv("FINANCE_MOCK", raising=False)  # CI는 FINANCE_MOCK=1도 켠다
    refused = client.post(
        "/api/finance/closing/confirm", json={"date": "2026-10-20"}, headers=HUMAN
    )
    assert refused.status_code == 401


def test_reconciliation_note(client: TestClient) -> None:
    items = client.get("/api/finance/reconciliation").json()["data"]["items"]
    target = next(i for i in items if i["status"] == "mismatch")
    url = f"/api/finance/reconciliation/{target['payment_id']}/note"
    assert client.post(url, json={"note": "PG사에 문의함"}).status_code == 401
    assert client.post(url, json={"note": " "}, headers=HUMAN).status_code == 400
    missing = client.post(
        "/api/finance/reconciliation/pay_none/note", json={"note": "x"}, headers=HUMAN
    )
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")
    response = client.post(url, json={"note": "PG사에 문의함"}, headers=HUMAN)
    assert response.status_code == 200
    item = response.json()["data"]
    assert (item["payment_id"], item["note"], item["status"]) == (
        target["payment_id"],
        "PG사에 문의함",
        "mismatch",
    )
    listed = client.get("/api/finance/reconciliation").json()["data"]["items"]
    assert (
        next(i for i in listed if i["payment_id"] == target["payment_id"])["note"]
        == "PG사에 문의함"
    )
    assert handlers.state().records.recon_note_log[0]["by"] == "taeyoon"


# ------------------------------------------------- CORS (대시보드가 브라우저에서 직접)


def test_dashboard_can_post_decisions_only_on_decision_paths(client: TestClient) -> None:
    for path in (
        "/api/finance/closing/confirm",
        "/api/finance/reconciliation/pay_1/note",
    ):
        preflight = client.options(
            path,
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200, path
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:3001"
        assert "POST" in preflight.headers["access-control-allow-methods"]
    get = client.get("/api/finance/overview", headers={"Origin": "http://localhost:3001"})
    assert get.headers.get("access-control-allow-origin") == "http://localhost:3001"
    evil = client.get("/api/finance/overview", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    # 정산 묶음 요청은 고객지원으로 옮겨 재무 결정 경로가 아니다
    gone = client.options(
        "/api/finance/settlements/batch",
        headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "POST"},
    )
    assert gone.status_code == 400
    a2a = client.options(
        "/a2a",
        headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "POST"},
    )
    assert a2a.status_code == 400


def test_removed_endpoints(client: TestClient) -> None:
    for path in (
        "/api/finance/audit",  # 재무는 감사를 하지 않는다
        "/api/finance/anomalies",
        "/api/finance/verifications",
        "/api/finance/ai-reviews",
        "/api/finance/catbooks/sync",
        "/api/finance/catbooks/items",
        "/api/finance/threshold-reviews",
        "/api/finance/loss-reports",  # 2026-10-27 장애 흐름에서 빠지며 없앰
        "/api/finance/market-ops",  # 2026-10-30 마켓운영을 재무에서 뺌
        "/api/finance/policy",  # 2026-11-02 쿠폰·환불 예산·한도를 재무에서 뺌
    ):
        assert client.get(path).status_code == 404
    for path in (
        "/api/finance/ledger",
        "/api/finance/self-test/last",
    ):
        assert client.get(path).status_code == 200, path


def test_seed_dates_move_with_today(client: TestClient, monkeypatch) -> None:
    from finance_agent import finance_config as fc

    later = fc.now() + timedelta(days=10)
    monkeypatch.setattr(fc, "now", lambda: later)
    data = client.get("/api/finance/overview").json()["data"]
    assert data["date"] == later.date().isoformat() and data["kpis"]["gmv"] > 0


def test_support_costs_lists_recorded_inquiries_newest_first(client: TestClient) -> None:
    assert _get(client, "/api/finance/support-costs")["data"] == []
    ledger = handlers.state().ledger
    for n in (1, 2):
        ledger.support_costs[n] = {
            "record_id": f"sc_{n}",
            "inquiry_id": n,
            "order_id": "ord_2001",
            "resolution": "answered",
            "refund_amount": 0,
            "recorded_at": f"2026-10-29T0{n}:00:00+00:00",
        }
    body = _get(client, "/api/finance/support-costs")
    assert [r["inquiry_id"] for r in body["data"]] == [2, 1]
    assert META_KEYS <= set(body["meta"])


# ---------------------------------------------------------------- 할 일에서 바로 결정
def _todo(client: TestClient, kind: str) -> dict | None:
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    return next((t for t in todo if t["kind"] == kind), None)


# ---------------------------------------------------------------- 결산 확정 사고 방지
def test_confirm_needs_a_date(client: TestClient) -> None:
    """빈 본문이 오늘 결산을 확정한 사고(251, 2026-11-04)가 다시 나지 않게 한다."""
    for body in ({}, {"period": "day"}):
        response = client.post("/api/finance/closing/confirm", json=body, headers=HUMAN)
        assert response.status_code == 400
    assert handlers.state().records.closings == {}


@pytest.mark.parametrize(
    ("period", "date", "status"),
    [
        ("day", "2026-10-23", 409),  # 오늘
        ("day", "2026-10-24", 409),  # 미래
        ("day", "2026-10-22", 200),  # 어제
        ("week", "2026-10-22", 409),  # 이번 주
        ("week", "2026-10-15", 200),  # 지난주
        ("month", "2026-10-01", 409),  # 이번 달
        ("month", "2026-09-30", 200),  # 지난달
    ],
)
def test_only_finished_periods_can_be_confirmed(
    client: TestClient, period: str, date: str, status: int
) -> None:
    body = {"period": period, "date": date}
    response = client.post("/api/finance/closing/confirm", json=body, headers=HUMAN)
    assert response.status_code == status
    if status == 409:
        assert response.json()["error"]["code"] == "period_not_closed"
        assert handlers.state().records.closings == {}


def test_report_says_whether_it_can_be_confirmed(client: TestClient) -> None:
    today = client.get("/api/finance/closing", params={"date": "2026-10-23"}).json()["data"]
    yesterday = client.get("/api/finance/closing", params={"date": "2026-10-22"}).json()["data"]
    assert today["closable"] is False and yesterday["closable"] is True


def test_cancel_reopens_and_leaves_a_record(client: TestClient) -> None:
    key = {"period": "day", "date": "2026-10-22"}
    client.post("/api/finance/closing/confirm", json=key, headers=HUMAN)
    response = client.post(
        "/api/finance/closing/cancel", json=key | {"reason": "시험 중 잘못 확정"}, headers=HUMAN
    )
    assert response.status_code == 200 and response.json()["data"]["status"] == "draft"
    records = handlers.state().records
    assert records.closings == {}
    log = records.closing_cancels[0]
    assert (log["date"], log["confirmed_by"], log["canceled_by"]) == (
        "2026-10-22",
        "taeyoon",
        "taeyoon",
    )
    kinds = [t["kind"] for t in client.get("/api/finance/overview").json()["data"]["todo"]]
    assert "closing_confirm" in kinds  # 다시 확정할 일로 돌아온다


def test_cancel_rules(client: TestClient) -> None:
    key = {"period": "day", "date": "2026-10-22"}
    url = "/api/finance/closing/cancel"
    assert client.post(url, json=key | {"reason": "r"}).status_code == 401
    assert client.post(url, json=key | {"reason": "r"}, headers=HUMAN).status_code == 404
    client.post("/api/finance/closing/confirm", json=key, headers=HUMAN)
    assert client.post(url, json=key, headers=HUMAN).status_code == 400
    assert client.post(url, json=key | {"reason": " "}, headers=HUMAN).status_code == 400
    assert handlers.state().records.closings != {}


# ---------------------------------------------------------------- 확정 숫자 고정
def _swap_books(monkeypatch: pytest.MonkeyPatch, books: Any) -> None:
    async def load() -> Any:
        return books

    monkeypatch.setattr(handlers.state(), "books_loader", load)


def _lines(data: dict) -> dict[str, int | None]:
    return {x["code"]: x["amount"] for x in data["lines"]}


def test_confirmed_numbers_stay_and_drift_is_shown(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """확정 뒤 주문이 바뀌어도 보고는 확정 숫자이고, 차이는 따로 나온다."""
    day = "2026-10-21"
    first = kit.order("o1", kit.at(day), [("s", "book", 1, 100_000)])
    second = kit.order("o2", kit.at(day), [("s", "book", 1, 50_000)])
    _swap_books(monkeypatch, kit.books(orders=[first, second]))
    body = {"period": "day", "date": day}
    confirmed = client.post("/api/finance/closing/confirm", json=body, headers=HUMAN).json()["data"]
    assert _lines(confirmed)["gmv"] == 150_000 and confirmed["snapshot_at"]
    assert confirmed["drift"] == []

    _swap_books(monkeypatch, kit.books(orders=[first]))  # 확정 뒤 한 건이 빠졌다
    later = client.get("/api/finance/closing", params={"date": day}).json()["data"]
    assert _lines(later)["gmv"] == 150_000  # 숫자는 그대로
    gmv = next(x for x in later["drift"] if x["code"] == "gmv")
    assert (gmv["confirmed"], gmv["current"], gmv["diff"]) == (150_000, 100_000, -50_000)
    assert later["commentary"] == confirmed["commentary"]


def test_old_confirmation_without_snapshot_still_reads(client: TestClient) -> None:
    handlers.state().records.closings[("day", "2026-10-21")] = {
        "by": "옛날",
        "at": "2026-10-22T00:00:00Z",
    }
    data = client.get("/api/finance/closing", params={"date": "2026-10-21"}).json()["data"]
    assert data["status"] == "confirmed" and data["snapshot_at"] is None and data["drift"] == []


def test_month_with_unsettled_payments_cannot_be_confirmed(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance_agent import closing

    monkeypatch.setattr(closing, "pending_recon", lambda books, start, end: 3)
    body = {"period": "month", "date": "2026-09-30"}
    response = client.post("/api/finance/closing/confirm", json=body, headers=HUMAN)
    assert response.status_code == 409 and response.json()["error"]["code"] == "recon_pending"
    assert handlers.state().records.closings == {}
    day = client.post(
        "/api/finance/closing/confirm", json={"period": "day", "date": "2026-10-20"}, headers=HUMAN
    )
    assert day.status_code == 200  # 일 결산은 경고만 하고 저장한다
    warning = day.json()["data"]["close_warnings"][0]
    assert (warning["code"], warning["count"]) == ("recon_pending", 3)


def test_spike_warning_needs_enough_history(kit: Any) -> None:
    from datetime import date

    from finance_agent import closing

    def build(days: int) -> Any:
        rows = [
            kit.order(f"h{n}", kit.at(f"2026-10-{n:02d}"), [("s", "book", 1, 10_000)])
            for n in range(1, days + 1)
        ]
        rows.append(kit.order("big", kit.at("2026-10-20"), [("s", "book", 1, 100_000)]))
        return kit.books(orders=rows)

    target = date(2026, 10, 20)
    assert "sales_spike" in {w["code"] for w in closing.close_warnings(build(19), "day", target)}
    # 판매일이 적으면 평소를 모른다. 서비스 초기에 틀린 경고를 내지 않는다
    assert "sales_spike" not in {w["code"] for w in closing.close_warnings(build(5), "day", target)}


def test_find_drifts_goes_to_the_todo_list(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance_agent import closing

    day = "2026-10-21"
    first = kit.order("o1", kit.at(day), [("s", "book", 1, 100_000)])
    second = kit.order("o2", kit.at(day), [("s", "book", 1, 50_000)])
    _swap_books(monkeypatch, kit.books(orders=[first, second]))
    client.post("/api/finance/closing/confirm", json={"period": "day", "date": day}, headers=HUMAN)
    records = handlers.state().records
    assert closing.find_drifts(kit.books(orders=[first, second]), records.closings) == []
    drifts = closing.find_drifts(kit.books(orders=[first]), records.closings)
    assert drifts[0]["date"] == day and drifts[0]["net_diff"] < 0
    handlers.state().closing_drifts = drifts
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    assert next(t for t in todo if t["kind"] == "closing_drift")["count"] == 1


def test_cancel_keeps_the_snapshot_in_the_record(client: TestClient) -> None:
    key = {"period": "day", "date": "2026-10-22"}
    client.post("/api/finance/closing/confirm", json=key, headers=HUMAN)
    client.post("/api/finance/closing/cancel", json=key | {"reason": "r"}, headers=HUMAN)
    assert handlers.state().records.closing_cancels[0]["snapshot"]["lines"]


def test_confirmed_month_fixes_statement_and_budget(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import date

    from finance_agent import campaign as cp

    day = "2026-09-10"
    first = kit.order("o1", kit.at(day), [("s", "book", 1, 1_000_000)])
    second = kit.order("o2", kit.at(day), [("s", "book", 1, 1_000_000)])
    both = kit.books(orders=[first, second])
    _swap_books(monkeypatch, both)
    monkeypatch.setattr("finance_agent.closing.pending_recon", lambda books, start, end: 0)
    body = {"period": "month", "date": day}
    assert client.post("/api/finance/closing/confirm", json=body, headers=HUMAN).status_code == 200
    closings = handlers.state().records.closings
    fixed = cp.budget_from_last_month(kit.books(orders=[first]), date(2026, 10, 15), closings)
    assert fixed["amount_krw"] == 41_400 and "확정" in fixed["basis"]  # 2건 기준 138,000의 30%

    _swap_books(monkeypatch, kit.books(orders=[first]))
    params = {"period": "month", "date": day}
    report = client.get("/api/finance/statements", params=params).json()["data"]
    assert report["confirmed"]["current"] is True
    row = next(r for r in report["rows"] if r["code"] == "commission")
    assert row["current"] == 200_000  # 지금 장부는 한 건이지만 확정 숫자를 쓴다


def test_budget_from_a_short_month_is_marked_provisional(kit: Any) -> None:
    from datetime import date

    from finance_agent import campaign as cp

    rows = [kit.order("o1", kit.at("2026-09-28"), [("s", "book", 1, 1_000_000)])]
    got = cp.budget_from_last_month(kit.books(orders=rows), date(2026, 10, 15))
    assert got["provisional"] is True and "1일치" in got["basis"]


# ---------------------------------------------------------------- 조정
def _confirm_september(client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    first = kit.order("o1", kit.at("2026-09-10"), [("s", "book", 1, 1_000_000)])
    second = kit.order("o2", kit.at("2026-09-10"), [("s", "book", 1, 1_000_000)])
    _swap_books(monkeypatch, kit.books(orders=[first, second]))
    monkeypatch.setattr("finance_agent.closing.pending_recon", lambda books, start, end: 0)
    body = {"period": "month", "date": "2026-09-10"}
    assert client.post("/api/finance/closing/confirm", json=body, headers=HUMAN).status_code == 200
    _swap_books(monkeypatch, kit.books(orders=[first]))
    return first


def _september(client: TestClient) -> dict:
    params = {"period": "month", "date": "2026-09-10"}
    return client.get("/api/finance/closing", params=params).json()["data"]


def test_month_drift_makes_a_balanced_proposal(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirm_september(client, kit, monkeypatch)
    proposal = _september(client)["adjustment_proposal"]
    assert proposal["net"] < 0 and proposal["signature"]
    rows = proposal["journal"]
    assert sum(r["debit"] for r in rows) == sum(r["credit"] for r in rows)
    assert all(r["debit"] >= 0 and r["credit"] >= 0 for r in rows)
    assert all(r["memo"].startswith("전기 조정") for r in rows)


def test_person_posts_adjustment_and_month_stays_fixed(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirm_september(client, kit, monkeypatch)
    before = _september(client)
    fixed = _lines(before)
    body = {
        "date": "2026-09-10",
        "reason": "주문 취소 반영",
        "signature": before["adjustment_proposal"]["signature"],
    }
    url = "/api/finance/closing/adjust"
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json={"date": "2026-09-10"}, headers=HUMAN).status_code == 400
    wrong = body | {"signature": "old"}
    assert client.post(url, json=wrong, headers=HUMAN).json()["error"]["code"] == "drift_changed"
    after = client.post(url, json=body, headers=HUMAN).json()["data"]
    assert _lines(after) == fixed  # 확정한 달의 숫자는 그대로
    assert after["drift"] == [] and after["adjustment_proposal"] is None  # 다시 나오지 않는다
    assert after["adjustments"][0]["reason"] == "주문 취소 반영"
    assert client.post(url, json=body, headers=HUMAN).json()["error"]["code"] == "no_drift"

    # 반영한 달(이번 달)의 결산에 전기 조정으로 나온다
    now = client.get(
        "/api/finance/closing", params={"period": "month", "date": "2026-10-23"}
    ).json()["data"]
    assert len(now["prior_adjustments"]) == 1
    net = _lines(now)["net_result"]
    assert now["net_after_adjustments"] == net + before["adjustment_proposal"]["net"]


def test_day_drift_has_no_proposal_and_month_takes_over(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance_agent import closing

    first = kit.order("o1", kit.at("2026-09-10"), [("s", "book", 1, 100_000)])
    second = kit.order("o2", kit.at("2026-09-10"), [("s", "book", 1, 100_000)])
    _swap_books(monkeypatch, kit.books(orders=[first, second]))
    monkeypatch.setattr("finance_agent.closing.pending_recon", lambda books, start, end: 0)
    day = {"period": "day", "date": "2026-09-10"}
    client.post("/api/finance/closing/confirm", json=day, headers=HUMAN)
    now = kit.books(orders=[first])
    _swap_books(monkeypatch, now)
    data = client.get("/api/finance/closing", params=day).json()["data"]
    assert data["drift"] and data["adjustment_proposal"] is None
    records = handlers.state().records.closings
    assert [x["period"] for x in closing.find_drifts(now, records)] == ["day"]
    records[("month", "2026-09-01")] = {"by": "x", "at": "t"}  # 월을 확정하면 일 차이는 빠진다
    assert closing.find_drifts(now, records) == []


# ---------------------------------------------------------------- 분기·연 재무제표, 변동 분석
def test_quarter_statement_uses_confirmed_months(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _confirm_september(client, kit, monkeypatch)
    august = kit.order("a1", kit.at("2026-08-10"), [("s", "book", 1, 500_000)])
    _swap_books(monkeypatch, kit.books(orders=[first, august]))
    params = {"period": "quarter", "date": "2026-09-10"}
    report = client.get("/api/finance/statements", params=params).json()["data"]
    assert report["confirmed"] == {
        "current": False,
        "previous": False,
        "current_months": 1,
        "total_months": 3,
    }
    row = next(r for r in report["rows"] if r["code"] == "commission")
    assert row["current"] == 200_000 + 50_000  # 9월 확정 2건 + 8월 지금 장부


def test_variance_uses_confirmed_numbers(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirm_september(client, kit, monkeypatch)
    data = client.get("/api/finance/variance", params={"date": "2026-09-10"}).json()["data"]
    line = next(x for x in data["lines"] if x["code"] == "gmv")
    assert line["current"] == 2_000_000  # 지금 장부는 1건이지만 확정 숫자다


# ---------------------------------------------------------------- 부가세 고정
def _vat(client: TestClient, date: str = "2026-09-10") -> dict:
    return client.get("/api/finance/tax/vat", params={"date": date}).json()["data"]


def test_vat_confirm_only_finished_settled_quarters(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "/api/finance/tax/vat/confirm"
    assert client.post(url, json={"date": "2026-09-10"}).status_code == 401
    assert client.post(url, json={}, headers=HUMAN).status_code == 400
    current = client.post(url, json={"date": "2026-10-10"}, headers=HUMAN)
    assert current.json()["error"]["code"] == "period_not_closed"
    monkeypatch.setattr("finance_agent.closing.pending_recon", lambda books, start, end: 2)
    pending = client.post(url, json={"date": "2026-09-10"}, headers=HUMAN)
    assert pending.json()["error"]["code"] == "recon_pending"
    assert handlers.state().records.vat_filings == {}


def test_vat_numbers_stay_after_confirm_and_drift_shows(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance_agent import tax

    first = kit.order("o1", kit.at("2026-09-10"), [("s", "book", 1, 1_000_000)])
    second = kit.order("o2", kit.at("2026-09-10"), [("s", "book", 1, 1_000_000)])
    both = kit.books(orders=[first, second])
    _swap_books(monkeypatch, both)
    monkeypatch.setattr("finance_agent.closing.pending_recon", lambda books, start, end: 0)
    body = {"date": "2026-09-10"}
    done = client.post("/api/finance/tax/vat/confirm", json=body, headers=HUMAN).json()["data"]
    assert done["filing_status"] == "confirmed" and done["drift"] == []
    fixed = done["payable"]

    one = kit.books(orders=[first])
    _swap_books(monkeypatch, one)
    later = _vat(client)
    assert later["payable"] == fixed
    assert {x["code"] for x in later["drift"]} >= {"payable"}
    filings = handlers.state().records.vat_filings
    assert tax.find_vat_drifts(one, filings)[0]["from"] == "2026-07-01"
    handlers.state().vat_drifts = tax.find_vat_drifts(one, filings)
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    assert next(t for t in todo if t["kind"] == "vat_drift")["link"] == "tax"
    seller = client.get("/api/finance/tax/seller-report", params=body).json()["data"]
    assert sum(r["total"] for r in seller["rows"]) == 2_000_000  # 명세도 확정 숫자

    url = "/api/finance/tax/vat/cancel"
    assert client.post(url, json=body, headers=HUMAN).status_code == 400
    undone = client.post(url, json=body | {"reason": "재검토"}, headers=HUMAN).json()["data"]
    assert undone["filing_status"] == "draft"
    assert handlers.state().records.vat_filing_cancels[0]["reason"] == "재검토"
    assert client.post(url, json=body | {"reason": "r"}, headers=HUMAN).status_code == 404


# ---------------------------------------------------------------- 장부 밖 손익
OTHER = {
    "month": "2026-09",
    "salary": 300_000,
    "rent": 100_000,
    "other_sga": 50_000,
    "non_operating_income": 20_000,
    "non_operating_expense": 10_000,
}


def _rows(report: dict) -> dict[str, Any]:
    return {r["code"]: r for r in report["rows"]}


def _statement(client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch) -> dict:
    rows = [kit.order("o1", kit.at("2026-09-10"), [("s", "book", 1, 10_000_000)])]
    _swap_books(monkeypatch, kit.books(orders=rows))
    params = {"period": "month", "date": "2026-09-10"}
    return client.get("/api/finance/statements", params=params).json()["data"]


def test_no_entry_keeps_no_data_and_entry_fills_the_rows(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _rows(_statement(client, kit, monkeypatch))
    assert empty["pretax_income"]["current"] is None and empty["other_sga"]["note"]
    operating = empty["operating_income"]["current"]

    url = "/api/finance/other-pnl"
    assert client.post(url, json=OTHER).status_code == 401
    assert client.post(url, json=OTHER, headers=HUMAN).status_code == 200
    rows = _rows(_statement(client, kit, monkeypatch))
    assert rows["other_sga"]["current"] == -450_000
    assert rows["operating_income"]["current"] == operating - 450_000
    assert rows["non_operating_income"]["current"] == 20_000
    assert rows["non_operating_expense"]["current"] == -10_000
    assert rows["pretax_income"]["current"] == operating - 450_000 + 10_000
    assert rows["income_tax"]["current"] is None and rows["net_income"]["current"] is None
    assert rows["other_sga"]["note"] == "재무 담당 입력값으로 계산"


def test_entered_tax_gives_net_income(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/api/finance/other-pnl", json=OTHER | {"income_tax": 5_000}, headers=HUMAN)
    rows = _rows(_statement(client, kit, monkeypatch))
    assert rows["net_income"]["current"] == rows["pretax_income"]["current"] - 5_000


@pytest.mark.parametrize(
    "bad",
    [
        {"month": "2026-13"},
        {"month": "2099-01"},
        OTHER | {"salary": -1},
        OTHER | {"rent": "100"},
        OTHER | {"rent": True},
        OTHER | {"income_tax": -5},
    ],
)
def test_other_pnl_rejects_bad_input(client: TestClient, bad: dict) -> None:
    assert client.post("/api/finance/other-pnl", json=bad, headers=HUMAN).status_code == 400


def test_edit_keeps_history_and_confirmed_month_is_locked(
    client: TestClient, kit: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "/api/finance/other-pnl"
    client.post(url, json=OTHER, headers=HUMAN)
    second = client.post(url, json=OTHER | {"salary": 1}, headers=HUMAN).json()["data"]["entry"]
    assert second["history"][0]["salary"] == 300_000

    rows = [kit.order("o1", kit.at("2026-09-10"), [("s", "book", 1, 1_000_000)])]
    _swap_books(monkeypatch, kit.books(orders=rows))
    monkeypatch.setattr("finance_agent.closing.pending_recon", lambda books, start, end: 0)
    body = {"period": "month", "date": "2026-09-10"}
    assert client.post("/api/finance/closing/confirm", json=body, headers=HUMAN).status_code == 200
    locked = client.post(url, json=OTHER, headers=HUMAN)
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "month_confirmed"
    view = client.get(url, params={"month": "2026-09"}).json()["data"]
    assert view["locked"] is True and view["entry"]["salary"] == 1
    # 저장한 재무제표 값에 입력이 들어가 고정된다
    params = {"period": "month", "date": "2026-09-10"}
    report = client.get("/api/finance/statements", params=params).json()["data"]
    assert _rows(report)["other_sga"]["current"] == -(1 + 100_000 + 50_000)


def test_budget_and_tax_estimate_use_entered_costs(kit: Any) -> None:
    from datetime import date

    from finance_agent import campaign as cp
    from finance_agent import tax

    rows = [
        kit.order(f"o{n}", kit.at(f"2026-09-{n:02d}"), [("s", "book", 1, 1_000_000)])
        for n in range(1, 21)
    ]
    books = kit.books(orders=rows)
    other = {"2026-09": OTHER}
    plain = cp.budget_from_last_month(books, date(2026, 10, 15))
    less = cp.budget_from_last_month(books, date(2026, 10, 15), None, other)
    assert less["amount_krw"] == plain["amount_krw"] - 135_000 and "입력 비용" in less["basis"]
    before = tax.income_tax_estimate(books, 2026, date(2026, 10, 15))
    after = tax.income_tax_estimate(books, 2026, date(2026, 10, 15), other)
    assert after["operating_result"] == before["operating_result"] - 440_000
    assert after["entered_months"] == 1

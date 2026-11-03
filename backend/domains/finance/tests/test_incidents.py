"""시나리오 A 재무 몫 테스트: 사건별 쿠폰 장부(⑨)와 환불 금액, 재설계 문서 12장.

네트워크 없음. 실제 모드는 가짜 안전 관문·가짜 총괄(httpx MockTransport)과
가짜 data(`call_agent`)로 본다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    EventEnvelope,
    EventType,
    TaskStatus,
)
from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent import handlers, incidents, sources
from finance_agent import ledger as lg
from finance_agent.main import app
from finance_agent.sources import LiveSources, MockSources

CTX = "ctx_inc_scn_a_0001"
OLD = "ctx_inc_0100"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
IMPACT = {
    "failed_order_count": 6,
    "affected_customer_count": 4,
    "damage_amount": 95000,
    "affected_customer_ids": ["buy_0001", "buy_0002", "buy_0003", "buy_0004"],
    "by_category": [
        {"category": "books", "failed_order_count": 4, "damage_amount": 82500},
        {"category": "stationery", "failed_order_count": 2, "damage_amount": 12500},
    ],
    "summary": "결제 실패 6건",
}
LIST_KEYS = {
    "context_id",
    "component",
    "opened_at",
    "damage_amount",
    "compensation_total",
    "coupon_count",
    "refund_total",
    "money_out_total",
}
DETAIL_KEYS = {
    "context_id",
    "component",
    "window",
    "impact",
    "compensation",
    "refunds",
    "money_out_total",
    "ledger_source",
}
COUPON_KEYS = {
    "buyer_id",
    "execution_id",
    "coupon_id",
    "count",
    "unit_value",
    "amount",
    "purpose_code",
    "reason_code",
    "at",
}


def _exe(
    exe: str,
    buyer: str,
    *,
    ctx: str = CTX,
    status: str = "executed",
    count: int = 1,
    unit: int = 2000,
    total: int | None = None,
    computed: int | None = None,
    purpose: str = "compensation",
    key: str | None = None,
    minute: int = 0,
    grade: str = "A",
    review_id: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """장부 모양(normalize 뒤)의 실행 기록."""
    return {
        "execution_id": exe,
        "created_at": NOW - timedelta(hours=2) + timedelta(minutes=minute),
        "action": "issue_coupon",
        "requester": "marketing",
        "context_id": ctx,
        "params": {
            "buyer_id": buyer,
            "purpose_code": purpose,
            "reason_code": "payment_failure",
            "count": count,
            "unit_value": unit,
            "total_value": total if total is not None else count * unit,
        },
        "status": status,
        "grade": grade,
        "hold_reason": None if status != "held" else "over_threshold",
        "computed_amount": computed if computed is not None else count * unit,
        "review_id": review_id,
        "result_ref": ref,
        "idempotency_key": key or f"{ctx}:issue_coupon:{buyer}:{exe}",
    }


def _raw_record(e: dict[str, Any]) -> dict[str, Any]:
    """safety `ExecutionRecord` JSON 모양."""
    return {
        "execution_id": e["execution_id"],
        "created_at": e["created_at"].isoformat(),
        "request": {
            "action": e["action"],
            "requester": e["requester"],
            "context_id": e["context_id"],
            "tenant_id": "shop-demo",
            "idempotency_key": e["idempotency_key"],
            "params": e["params"],
        },
        "decision": {
            "execution_id": e["execution_id"],
            "grade": e["grade"],
            "status": e["status"],
            "computed_amount": e["computed_amount"],
            "hold_reason": e["hold_reason"],
            "review_id": e["review_id"],
            "result_ref": e["result_ref"],
        },
    }


def _feed(rows: list[dict[str, Any]] | None, source: str = lg.LEDGER_SAFETY) -> lg.ExecutionFeed:
    return lg.ExecutionFeed(rows, source)


def _event(n: int, ctx: str, total: int, **extra: Any) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_i{n}",
        type=EventType.COUPON_ISSUED,
        occurred_at=NOW,
        context_id=ctx,
        source="marketing",
        idempotency_key=f"coupon.issued:cpn_i{n}",
        data={
            "coupon_id": f"cpn_i{n}",
            "campaign_id": None,
            "purpose_code": "compensation",
            "count": 1,
            "total_value": total,
            **extra,
        },
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    events.reset_mock_bus()
    monkeypatch.setitem(lg.EVENTS, "connected", False)
    handlers.reset_state(MockSources())


@pytest.fixture
def mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)


@pytest.fixture
def real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI가 켜 두는 목 모드 변수를 끈다."""
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("FINANCE_MOCK", raising=False)
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety")


def _safety(
    records: list[dict[str, Any]] | None, seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    """가짜 안전 관문. records가 None이면 꺼져 있다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if records is None:
            raise httpx.ConnectError("safety down")
        if request.url.path == "/api/safety/executions":
            ctx = request.url.params.get("context_id")
            rows = [r for r in records if ctx is None or r["request"]["context_id"] == ctx]
            return httpx.Response(200, json={"data": rows, "meta": {}})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _use_safety(client: httpx.AsyncClient) -> None:
    s = handlers.state()

    async def executions(ctx: str | None) -> lg.ExecutionFeed:
        return await lg.load_executions(ctx, http_client=client)

    s.executions_loader = executions


ORC_ROW = {  # 총괄 `GET /api/incidents` 행 (재무가 쓰지 않는 키도 들어 있다)
    "context_id": CTX,
    "kind": "payment_outage",
    "title": "결제 장애",
    "component": "payment",
    "status": "resolved",
    "opened_at": "2026-10-22T05:00:00Z",
    "closed_at": "2026-10-22T05:30:00Z",
    "steps": [{"no": "1"}],
    "events": [],
    "summary": ["진단 완료"],
    "metrics": {"mttr_seconds": 1800},
    "shared": {},
    "pending_holds": [],
}


def _orchestrator(rows: list[dict[str, Any]] | None) -> httpx.AsyncClient:
    """가짜 총괄 사건 목록. rows가 None이면 총괄이 꺼져 있다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if rows is None:
            raise httpx.ConnectError("orchestrator down")
        if request.url.path == "/api/incidents":
            return httpx.Response(200, json={"data": rows, "meta": {"mock": False}})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _use_books() -> None:
    """장부를 시드로 꽂는다. 실제 모드 라우트는 `state().books()`로 shop-api를 부른다 —
    테스트는 네트워크를 타지 않는다 (CLAUDE.md "테스트가 네트워크에 기대지 않게 쓴다")."""

    async def seeded() -> bk.Books:
        return bk.seed_books()

    handlers.state().books_loader = seeded


@pytest.fixture
def fake_data(monkeypatch: pytest.MonkeyPatch) -> list[AgentRequest]:
    calls: list[AgentRequest] = []

    async def fake_call_agent(name: str, request: AgentRequest) -> AgentResult:
        assert name == "data" and request.skill == "impact_analysis"
        calls.append(request)
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=dict(IMPACT))

    monkeypatch.setattr(sources, "call_agent", fake_call_agent)
    return calls


def _detail(ctx: str = CTX) -> dict[str, Any]:
    """지금 상태의 실행 기록으로 만든 사건 상세 data."""
    feed = asyncio.run(handlers.state().executions_loader(ctx))
    data, _ = asyncio.run(incidents.incident_view(ctx, feed))
    return data


# ---------------------------------------------------------------- ⑨ 장부 줄과 중복 방지
def test_coupon_rows_only_compensation_from_gate() -> None:
    rows = [
        _exe("exe_1", "buy_0001", ref="cpn_1"),
        _exe("exe_2", "buy_0002", status="held", grade="C", review_id="rev_2"),
        _exe("exe_3", "buy_0003", status="rejected", grade="C"),
        _exe("exe_4", "buy_0004", purpose="campaign"),  # 캠페인은 보상 장부가 아님
        _exe("exe_5", "buy_0005", status="verifying"),  # 결과가 아직 없음
        _exe("exe_6", "buy_0006", ctx="ctx_inc_other"),  # 다른 사건
    ]
    out = lg.execution_coupons(rows, CTX)
    assert [r["execution_id"] for r in out] == ["exe_1"]  # 끝난 것만 장부에 적는다
    first = out[0]
    assert (first["coupon_id"], first["amount"], first["reason_code"]) == (
        "cpn_1",
        2000,
        "payment_failure",
    )
    assert lg.totals(out) == {"total_executed": 2000, "count_executed": 1}


def test_execution_rows_dedupe_by_execution_and_idempotency_key() -> None:
    same_key = f"{CTX}:issue_coupon:buy_0002"
    rows = [
        _exe("exe_1", "buy_0001"),
        _exe("exe_1", "buy_0001"),  # 같은 실행 기록이 두 번 와도 한 줄
        _exe("exe_2", "buy_0002", key=same_key, status="held", grade="C"),
        _exe("exe_3", "buy_0002", key=same_key, minute=5),  # 같은 요청 다시 보내기 → 마지막만
    ]
    out = lg.execution_coupons(rows, CTX)
    assert [r["execution_id"] for r in out] == ["exe_1", "exe_3"]
    assert lg.totals(out)["total_executed"] == 4000


def test_merge_prefers_gate_records_and_never_double_counts_events() -> None:
    gate = [_exe("exe_1", "buy_0001"), _exe("exe_2", "buy_0002")]
    event_rows = [
        lg.coupon_from_event(CTX, {"coupon_id": "cpn_a", "count": 1, "total_value": 2000}, NOW),
        lg.coupon_from_event(
            CTX,
            {"coupon_id": "cpn_b", "count": 1, "total_value": 2000, "execution_id": "exe_2"},
            NOW,
        ),
    ]
    rows, source = lg.merge(CTX, _feed(gate), event_rows, events_available=True)
    assert source == "safety_executions"
    assert rows is not None and lg.totals(rows)["total_executed"] == 4000  # 6,000이 아니다
    assert [r["coupon_id"] for r in rows] == [None, "cpn_b"]  # 이벤트는 coupon_id만 채운다


def test_merge_falls_back_to_events_then_fails_closed() -> None:
    event_rows = [
        lg.coupon_from_event(CTX, {"coupon_id": "cpn_a", "count": 2, "total_value": 4000}, NOW)
    ]
    rows, source = lg.merge(CTX, _feed([]), event_rows, events_available=False)
    assert source == "events" and rows is not None
    assert (rows[0]["unit_value"], rows[0]["status"], rows[0]["buyer_id"]) == (
        2000,
        "executed",
        None,
    )
    assert lg.merge(CTX, _feed([]), [], events_available=False) == ([], "safety_executions")
    assert lg.merge(CTX, _feed(None), [], events_available=True) == ([], "events")
    assert lg.merge(CTX, _feed(None), [], events_available=False) == (None, None)
    assert lg.merge(CTX, _feed([], lg.LEDGER_SEED), [], False) == ([], "seed")


def test_load_executions_reads_gate_records(real_mode) -> None:
    seen: list[httpx.Request] = []
    rec = _raw_record(_exe("exe_9", "buy_0001", ref="cpn_9"))
    feed = asyncio.run(lg.load_executions(CTX, http_client=_safety([rec], seen)))
    assert feed.source == "safety_executions" and feed.rows is not None
    assert seen[0].method == "GET" and seen[0].url.path == "/api/safety/executions"
    assert seen[0].url.params["context_id"] == CTX
    [row] = lg.execution_coupons(feed.rows, CTX)
    assert (row["coupon_id"], row["buyer_id"], row["amount"]) == ("cpn_9", "buy_0001", 2000)
    down = asyncio.run(lg.load_executions(CTX, http_client=_safety(None)))
    assert down.rows is None


# ------------------------------------------------------------ 보상 쿠폰 장부 (실제 모드)
def test_compensation_total_in_real_mode_comes_from_gate_records(real_mode, fake_data) -> None:
    handlers.reset_state(LiveSources(_orchestrator([])))  # 총괄이 이 사건을 모른다
    records = [_raw_record(_exe(f"exe_{n}", f"buy_000{n}")) for n in range(1, 5)]
    records.append(_raw_record(_exe("exe_9", "buy_0009", ctx="ctx_inc_other")))
    _use_safety(_safety(records))
    data = _detail()
    comp = data["compensation"]
    # 다른 사건(ctx_inc_other)의 쿠폰은 섞이지 않는다
    assert (comp["count_executed"], comp["total_executed"]) == (4, 8000)
    assert data["ledger_source"] == "safety_executions"
    assert [c["execution_id"] for c in comp["coupons"]] == ["exe_1", "exe_2", "exe_3", "exe_4"]
    # 총괄이 이 사건을 모르면 구간도 모르고, 구간을 모르면 data에 묻지 않는다
    assert data["impact"] is None and fake_data == []


def test_compensation_without_any_ledger_source_is_unknown(real_mode, fake_data) -> None:
    handlers.reset_state(LiveSources(_orchestrator(None)))  # 총괄도 꺼짐
    _use_safety(_safety(None))  # 안전 관문 꺼짐 + 이벤트 버스 없음
    data, unavailable = asyncio.run(incidents.incident_view(CTX, _feed(None)))
    comp = data["compensation"]
    # 0으로 바꾸지 않고 "확인할 수 없음"(None)으로 둔다
    assert (comp["count_executed"], comp["total_executed"]) == (None, None)
    assert "compensation" in unavailable and data["ledger_source"] is None


def test_compensation_uses_events_when_no_gate_record_and_does_not_double_count(mock_mode) -> None:
    handlers.reset_state(MockSources())
    # 시드 실행 기록이 있는 사건: 같은 쿠폰의 이벤트가 와도 더하지 않는다
    asyncio.run(handlers.on_event(_event(1, CTX, 2000, execution_id="exe_s0060")))
    data = _detail()
    assert (data["compensation"]["total_executed"], data["ledger_source"]) == (8000, "seed")
    # 실행 기록이 없는 사건은 이벤트 장부
    asyncio.run(handlers.on_event(_event(2, "ctx_inc_0001", 5000)))
    other = _detail("ctx_inc_0001")
    assert (other["compensation"]["total_executed"], other["ledger_source"]) == (5000, "events")


# ---------------------------------------------------------------- 사건 API 형식 (목 모드)
def test_incident_list_shape_and_numbers(mock_mode) -> None:
    body = TestClient(app).get("/api/finance/incidents").json()
    assert set(body) == {"data", "meta"}
    assert body["meta"]["mock"] is True and body["meta"]["ledger_source"] == "seed"
    rows = {r["context_id"]: r for r in body["data"]}
    for r in body["data"]:
        assert set(r) == LIST_KEYS and r["context_id"].startswith("ctx_inc_")
    cur = rows[CTX]
    assert cur == {
        "context_id": CTX,
        "component": "payment",
        "opened_at": "2026-10-22T05:00:00Z",
        "damage_amount": 95000,
        "compensation_total": 8000,
        "coupon_count": 4,
        # 이 사건 구간(05:00~05:30)에 영향 고객에게 나간 환불은 시드에 없다
        "refund_total": 0,
        "money_out_total": 8000,
    }
    assert body["data"][0]["context_id"] == CTX  # 최근 사건 먼저


def test_incident_detail_shape_matches_spec(mock_mode) -> None:
    body = TestClient(app).get(f"/api/finance/incidents/{CTX}").json()
    data, meta = body["data"], body["meta"]
    assert set(data) == DETAIL_KEYS and set(meta) >= {"mock", "unavailable"}
    assert meta["unavailable"] == [] and data["ledger_source"] == "seed"
    assert data["window"] == {"from": "2026-10-22T05:00:00Z", "to": "2026-10-22T05:30:00Z"}
    impact = data["impact"]
    assert set(impact) == {
        "failed_orders",
        "affected_customers",
        "damage_amount",
        "by_category",
        "affected_customer_ids",
        "window_exact",
        "source",
        "collected_at",
    }
    assert (impact["failed_orders"], impact["affected_customers"], impact["damage_amount"]) == (
        6,
        4,
        95000,
    )
    assert impact["by_category"] == [
        {"category": "books", "orders": 4, "amount": 82500},
        {"category": "stationery", "orders": 2, "amount": 12500},
    ]
    assert impact["affected_customer_ids"] == ["buy_0001", "buy_0002", "buy_0003", "buy_0004"]
    assert impact["source"] == "data.impact_analysis"
    assert impact["window_exact"] is True  # 목 모드 시드 구간은 장애 구간이다
    comp = data["compensation"]
    assert set(comp) == {"coupons", "total_executed", "count_executed"}
    assert (comp["total_executed"], comp["count_executed"]) == (8000, 4)
    for c in comp["coupons"]:
        assert set(c) == COUPON_KEYS
        assert (c["unit_value"], c["reason_code"]) == (2000, "payment_failure")
        assert c["coupon_id"].startswith("cpn_")
    # 재무는 장애 대응 흐름의 단계가 아니다 (2026-10-27): 손실 집계·사건 단계를 뺐다.
    # 화면 호환으로 남겨 뒀던 두 키도 화면 정리(ax-frontend#61) 뒤에 지웠다
    assert "loss" not in data
    assert "steps" not in data
    # 쿠폰·환불 한도 사용률과 점검은 재무 일이 아니다 (2026-11-02). 금액 합계와 내역만 남는다
    assert "limits" not in data and "checks" not in data


def test_unknown_incident_is_404(mock_mode) -> None:
    response = TestClient(app).get("/api/finance/incidents/ctx_inc_9999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


GOOD = [_exe(f"exe_{n}", f"buy_000{n}", minute=n) for n in range(1, 5)]


def test_held_coupons_are_not_in_the_ledger(mock_mode) -> None:
    rows = [*GOOD, _exe("exe_5", "buy_0001", status="held", grade="C", review_id="rev_5")]
    data, _ = asyncio.run(incidents.incident_view(CTX, _feed(rows)))
    comp = data["compensation"]
    assert (comp["total_executed"], comp["count_executed"]) == (8000, 4)
    assert "exe_5" not in {c["execution_id"] for c in comp["coupons"]}


def test_no_ledger_source_makes_everything_unknown(real_mode) -> None:
    handlers.reset_state(MockSources())
    data, unavailable = asyncio.run(incidents.incident_view(CTX, _feed(None)))
    assert "compensation" in unavailable
    assert data["compensation"]["total_executed"] is None and data["ledger_source"] is None
    assert data["money_out_total"] is None  # 모르는 돈을 0원으로 더하지 않는다
    assert "loss" not in data and "steps" not in data  # 손실 집계·사건 단계 없음


# ---------------------------------------------------------------- 실제 모드 사건 API
def test_real_mode_incident_uses_gate_records_and_has_no_window(real_mode, fake_data) -> None:
    handlers.reset_state(LiveSources(_orchestrator(None)))  # 총괄을 못 읽으면 구간도 모른다
    records = [_raw_record(e) for e in GOOD]
    _use_safety(_safety(records))
    _use_books()
    client = TestClient(app)
    body = client.get(f"/api/finance/incidents/{CTX}").json()
    data = body["data"]
    assert body["meta"]["mock"] is False and data["ledger_source"] == "safety_executions"
    # 총괄 사건 목록을 못 읽으면 구간·영향이 null이고 data에 묻지도 않는다 (fail-closed).
    # 재무는 장애 대응 흐름의 단계가 아니다 (요구사항 DEP-FIN-01)
    assert data["window"] is None
    assert data["impact"] is None and "impact" in body["meta"]["unavailable"] and fake_data == []
    assert data["compensation"]["count_executed"] == 4
    listing = client.get("/api/finance/incidents").json()
    assert listing["meta"]["ledger_source"] == "safety_executions"
    assert [r["context_id"] for r in listing["data"]] == [CTX]
    assert listing["data"][0]["damage_amount"] is None  # 영향을 모르면 피해 금액도 모른다


# ------------------------------------------- 사건 구간을 총괄에서 읽기 (#417 제안 A, #418)
def test_real_mode_window_comes_from_orchestrator_incidents(real_mode, fake_data) -> None:
    """영향 구간을 안 실은 사건은 `opened_at`·`closed_at`을 근사 구간으로 쓴다 (읽기 전용).

    이 둘은 AI 처리가 시작·끝난 시각이라 장애 시작보다 늦을 수 있다
    (감시가 장애를 지켜본 뒤 사건을 연다).
    그래서 숫자는 보여 주되 환불 귀속은 하지 않는다 (2026-10-30 시연 오탐).
    """
    handlers.reset_state(LiveSources(_orchestrator([{"context_id": "ctx_inc_other"}, ORC_ROW])))
    _use_safety(_safety([_raw_record(e) for e in GOOD]))
    _use_books()
    body = TestClient(app).get(f"/api/finance/incidents/{CTX}").json()
    data = body["data"]
    assert data["window"] == {"from": "2026-10-22T05:00:00Z", "to": "2026-10-22T05:30:00Z"}
    assert data["component"] == "payment"
    # 구간을 알게 되니 data `impact_analysis`를 계약대로 부른다 (사건 번호는 meta로)
    [sent] = fake_data
    assert sent.skill == "impact_analysis" and sent.meta.context_id == CTX
    assert sent.input == {
        "window_from": "2026-10-22T05:00:00Z",
        "window_to": "2026-10-22T05:30:00Z",
    }
    assert (data["impact"]["failed_orders"], data["impact"]["damage_amount"]) == (6, 95000)
    assert data["impact"]["window_exact"] is False
    assert body["meta"]["unavailable"] == ["refunds"]  # 근사 구간이라 환불을 사건에 묶지 않는다
    # 목록 행의 피해 금액도 같은 숫자다
    [row] = TestClient(app).get("/api/finance/incidents").json()["data"]
    assert (row["context_id"], row["damage_amount"]) == (CTX, 95000)


def test_open_incident_has_no_window_yet(real_mode, fake_data) -> None:
    """아직 열린 사건은 끝 시각이 없어 구간이 되지 않는다. 부품만 안다."""
    row = {**ORC_ROW, "status": "open", "closed_at": None}
    handlers.reset_state(LiveSources(_orchestrator([row])))
    _use_safety(_safety([_raw_record(e) for e in GOOD]))
    data = _detail()
    assert data["window"] is None and data["impact"] is None
    assert data["component"] == "payment" and fake_data == []


def test_orchestrator_rows_outside_the_contract_are_unknown(real_mode, fake_data) -> None:
    """계약(`OrchestratorIncident`)과 다른 행은 "확인할 수 없음"이고 추정하지 않는다."""
    for bad in (
        {**ORC_ROW, "opened_at": "2026-10-22T05:00:00"},  # 시간대 없음
        {**ORC_ROW, "opened_at": None},  # 시작 시각 없음
        {**ORC_ROW, "status": None},  # 상태 없음
    ):
        handlers.reset_state(LiveSources(_orchestrator([bad])))
        _use_safety(_safety([_raw_record(e) for e in GOOD]))  # reset_state 뒤에 다시 꽂는다
        data = _detail()
        assert data["window"] is None and data["impact"] is None
    assert fake_data == []  # 구간을 모르면 data에 묻지 않는다


def test_orchestrator_is_not_read_in_mock_mode(mock_mode, fake_data) -> None:
    """목 모드는 장부 시드로 구간을 알아서 총괄을 읽지 않는다."""
    assert asyncio.run(MockSources().incident_window(CTX)) is None
    handlers.reset_state(MockSources())
    info = asyncio.run(handlers.incident_info(CTX))
    assert (fc.iso(info["window_from"]), fc.iso(info["window_to"])) == (
        "2026-10-22T05:00:00Z",
        "2026-10-22T05:30:00Z",
    )


def test_impact_window_from_the_orchestrator_is_used_first(real_mode, fake_data) -> None:
    """총괄이 영향 분석에 쓴 구간(`impact_from`·`impact_to`)을 실으면 그 구간이 정확한 구간이다."""
    row = {**ORC_ROW, "impact_from": "2026-10-22T04:59:38Z", "impact_to": "2026-10-22T05:15:00Z"}
    handlers.reset_state(LiveSources(_orchestrator([row])))
    _use_safety(_safety([_raw_record(e) for e in GOOD]))
    _use_books()
    body = TestClient(app).get(f"/api/finance/incidents/{CTX}").json()
    data = body["data"]
    [sent] = fake_data
    assert sent.input == {
        "window_from": "2026-10-22T04:59:38Z",
        "window_to": "2026-10-22T05:15:00Z",
    }
    assert data["impact"]["window_exact"] is True
    assert body["meta"]["unavailable"] == []
    assert data["refunds"] is not None  # 정확한 구간이면 환불을 사건에 묶는다


def test_window_end_is_never_in_the_future(mock_mode, monkeypatch) -> None:
    future = fc.now() + timedelta(minutes=15)
    start = fc.now() - timedelta(minutes=1)
    monkeypatch.setattr(
        incidents,
        "incident_info",
        AsyncMock(return_value={"window_from": start, "window_to": future, "component": "payment"}),
    )
    data, _ = asyncio.run(incidents.incident_view(CTX, _feed(None)))
    assert data["window"]["to"] <= fc.iso(fc.now())

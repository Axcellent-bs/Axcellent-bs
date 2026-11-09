"""A2A 읽기·기록 skill `record_incident_finance`: 총괄이 장애 이벤트 때 재무 장부에 기록하게 한다.

재무는 기록만 한다(보상·환불을 결정하지 않는다). 모르는 금액은 0이 아니라 null이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from finance_agent import finance_config as fc
from finance_agent import handlers, persist
from finance_agent import ledger as lg
from finance_agent.main import app, load_card
from finance_agent.schemas import IncidentFinanceOutput
from finance_agent.sources import MockSources

CTX = "ctx_inc_rec_0001"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setitem(lg.EVENTS, "connected", False)
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


def _coupon(exe: str, buyer: str, amount: int, status: str = "executed") -> dict[str, Any]:
    return {
        "execution_id": exe,
        "created_at": fc.now() - timedelta(minutes=10),
        "action": "issue_coupon",
        "requester": "marketing",
        "context_id": CTX,
        "params": {
            "buyer_id": buyer,
            "purpose_code": "compensation",
            "reason_code": "payment_failure",
            "count": 1,
            "unit_value": amount,
            "total_value": amount,
        },
        "status": status,
        "grade": "A",
        "hold_reason": None,
        "computed_amount": amount,
        "review_id": None,
        "result_ref": None,
        "idempotency_key": f"{CTX}:issue_coupon:{buyer}:{exe}",
    }


def _use_feed(rows: list[dict[str, Any]] | None) -> None:
    async def executions(ctx: str | None) -> lg.ExecutionFeed:
        return lg.ExecutionFeed(rows, lg.LEDGER_SAFETY)

    handlers.state().executions_loader = executions


def _ask(data: dict, ctx: str = CTX, token: str = "mock:orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": ctx,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "record_incident_finance",
        "input": data,
    }
    response = TestClient(app).post("/a2a", json=body)
    assert response.status_code == 200
    return response.json()


def _window() -> dict[str, Any]:
    now = fc.now()
    return {
        "component": "payment",
        "window_from": (now - timedelta(minutes=30)).isoformat(),
        "window_to": now.isoformat(),
    }


def test_the_skill_is_a_read_skill_only_for_the_orchestrator() -> None:
    skill = next(s for s in load_card().skills if s.id == "record_incident_finance")
    assert skill.grade == "read"
    assert list(skill.allowed_requesters) == ["orchestrator"]
    blocked = _ask(_window(), token="mock:marketing")
    assert blocked["status"] != "completed"


def test_incident_is_recorded_with_ledger_coupons_and_given_impact() -> None:
    _use_feed(
        [
            _coupon("exe_1", "buy_0001", 2000),
            _coupon("exe_2", "buy_0002", 2000),
            _coupon("exe_3", "buy_0003", 2000, status="held"),  # 보류는 나간 돈이 아니다
        ]
    )
    result = _ask(
        _window()
        | {
            "failed_order_count": 6,
            "affected_customer_count": 2,
            "damage_amount": 95000,
            "affected_customer_ids": ["buy_0001", "buy_0002"],
        }
    )
    assert (result["status"], result["outcome"]) == ("completed", "recorded")
    out = IncidentFinanceOutput.model_validate(result["output"])
    assert out.damage_amount == 95000  # 데이터 숫자 그대로 (다시 계산하지 않는다)
    assert (out.compensation_total, out.compensation_coupon_count) == (4000, 2)
    assert out.refund_total == 0  # 귀속할 수 있고 환불이 없으면 0원
    assert out.money_out_total == 4000
    assert out.unavailable == [] and out.recorded is True
    # 장부에 남는다: 구간(재시작 뒤 장애 이벤트 재무 화면용)과 기록
    ledger = handlers.state().ledger
    assert ledger.incident_windows[CTX]["component"] == "payment"
    assert ledger.incident_reports[CTX]["money_out_total"] == 4000
    # 말풍선이 먼저 보여 줄 돈 숫자가 앞에 온다
    assert list(result["output"])[:4] == [
        "damage_amount",
        "compensation_total",
        "refund_total",
        "money_out_total",
    ]


def test_unknown_numbers_stay_null_not_zero() -> None:
    _use_feed(None)  # 안전 관문 기록을 못 읽음
    out = IncidentFinanceOutput.model_validate(_ask(_window())["output"])
    assert out.damage_amount is None and out.compensation_total is None
    assert out.refund_total is None  # 영향 고객을 모르면 환불을 이벤트에 묶지 않는다
    assert out.money_out_total is None
    assert set(out.unavailable) == {"impact", "compensation", "refunds"}


def test_calling_again_replaces_the_record() -> None:
    _use_feed([_coupon("exe_1", "buy_0001", 2000)])
    _ask(_window() | {"damage_amount": 1000, "affected_customer_ids": ["buy_0001"]})
    _use_feed([_coupon("exe_1", "buy_0001", 2000), _coupon("exe_2", "buy_0002", 3000)])
    _ask(_window() | {"damage_amount": 1000, "affected_customer_ids": ["buy_0001"]})
    report = handlers.state().ledger.incident_reports[CTX]
    assert report["compensation_total"] == 5000
    assert len(handlers.state().ledger.incident_reports) == 1


@pytest.mark.parametrize(
    ("ctx", "data"),
    [
        ("ctx_support_1", {}),  # 장애 이벤트가 아니다
        (CTX, {"window_from": "2026-10-23T12:00:00Z", "window_to": "2026-10-23T11:00:00Z"}),
    ],
)
def test_bad_requests_are_refused_without_writing(ctx: str, data: dict) -> None:
    _use_feed([])
    result = _ask(_window() | data, ctx=ctx)
    assert (result["status"], result["outcome"]) == ("failed", "invalid_input")
    assert handlers.state().ledger.incident_reports == {}


def test_incident_reports_survive_a_restart() -> None:
    _use_feed([_coupon("exe_1", "buy_0001", 2000)])
    _ask(_window() | {"damage_amount": 1000, "affected_customer_ids": ["buy_0001"]})
    saved = persist.dump(handlers.state())
    handlers.reset_state(MockSources())
    persist.restore(handlers.state(), saved)
    assert handlers.state().ledger.incident_reports[CTX]["compensation_total"] == 2000

"""장애 이벤트 재무 장부 기록 (`record_incident_finance`, orchestrator → finance, 읽기·기록).

총괄이 장애 이벤트를 처리하면서 영향 분석·보상이 끝나면 재무를 부른다. 재무는 그 이벤트를 장부에
**기록만** 한다. 돈을 움직이지 않고, 보상·환불을 결정하거나 승인하지 않는다(재무는 장부에만 적는다).

- 장애 구간·부품은 총괄이 준 값을 그대로 남긴다(`ledger.incident_windows`). 재시작해도 재무
  "장애 이벤트 재무" 화면이 구간을 안다.
- 영향(실패 주문·영향 고객·피해 금액)은 데이터 `impact_analysis` 결과를 총괄이 옮겨 준 값 그대로다.
  재무가 다시 계산하지 않는다.
- 보상 쿠폰은 재무가 이미 받은 장부(안전 관문 실행 기록 + 쿠폰 이벤트)에서 합계를 읽는다.
- 환불은 장애 구간 안에 영향 고객에게 나간 환불 금액만 더한다(`incidents.incident_refunds`).
  구간이나 영향 고객을 모르면 0으로 채우지 않고 null(확인할 수 없음)이다.
- 같은 이벤트를 다시 부르면 기록을 새 값으로 바꾼다(보상이 늦게 끝나도 마지막 값이 남는다).
"""

from __future__ import annotations

from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from finance_agent import finance_config as fc
from finance_agent import ledger as lg
from finance_agent.handlers import _evidence, _parse, _result, compensation_ledger, state
from finance_agent.incidents import INCIDENT_PREFIX, incident_refunds
from finance_agent.schemas import IncidentFinanceInput, IncidentFinanceOutput


async def record_incident_finance(request: AgentRequest) -> AgentResult:
    data = _parse(request, IncidentFinanceInput)
    if isinstance(data, AgentResult):
        return data
    context_id = request.meta.context_id
    if not context_id.startswith(INCIDENT_PREFIX):
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": [f"context_id는 {INCIDENT_PREFIX}로 시작하는 장애 이벤트여야 한다"]},
        )
    if data.window_to < data.window_from:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": ["window_to가 window_from보다 앞이다"]},
        )
    ledger = state().ledger
    window_from, window_to = fc.iso(data.window_from), fc.iso(data.window_to)
    ledger.incident_windows[context_id] = {
        k: v
        for k, v in {
            "component": data.component,
            "window_from": window_from,
            "window_to": window_to,
        }.items()
        if v is not None
    }
    unavailable: list[str] = []
    impact_known = data.damage_amount is not None
    if not impact_known:
        unavailable.append("impact")

    rows, ledger_source = await compensation_ledger(context_id)
    totals = lg.totals(rows) if rows is not None else None
    if totals is None:
        unavailable.append("compensation")
    comp_total = (totals or {}).get("total_executed")
    comp_count = (totals or {}).get("count_executed")

    books = await state().books()
    window = {"from": window_from, "to": min(window_to, fc.iso(fc.now()))}
    refunds = incident_refunds(books, window, frozenset(data.affected_customer_ids))
    if refunds is None:
        unavailable.append("refunds")
    refund_total = refunds["total"] if refunds is not None else None
    money_out = None if refund_total is None or comp_total is None else comp_total + refund_total

    report = {
        # 화면 말풍선에 먼저 보일 숫자부터 (돈 → 건수)
        "damage_amount": data.damage_amount,
        "compensation_total": comp_total,
        "refund_total": refund_total,
        "money_out_total": money_out,
        "compensation_coupon_count": comp_count,
        "failed_orders": data.failed_order_count,
        "affected_customers": data.affected_customer_count,
        "context_id": context_id,
        "component": data.component,
        "window_from": window_from,
        "window_to": window_to,
        "ledger_source": ledger_source,
        "unavailable": unavailable,
        "recorded": True,
        "recorded_at": fc.iso(fc.now()),
    }
    ledger.incident_reports[context_id] = report
    output = IncidentFinanceOutput(**report).model_dump(mode="json")
    summary = (
        f"{context_id} 장애 재무 기록: 피해 {_won(data.damage_amount)}, "
        f"보상 {_won(comp_total)}, 환불 {_won(refund_total)}"
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="recorded",
        output=output,
        evidence=_evidence(
            [
                "data.impact_analysis (총괄 전달)",
                "safety:/api/safety/executions",
                "shop-api:refunds",
            ],
            summary,
            [context_id],
        ),
    )


def _won(value: Any) -> str:
    return "확인할 수 없음" if value is None else fc.won(int(value))

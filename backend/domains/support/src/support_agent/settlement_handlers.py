"""판매자 정산 skill (settlement 서비스).

2026-11-12 팀장 결정으로 판매자 정산을 재무에서 고객지원으로 옮겼다. 고객지원이 환불·클레임을
처리하고, 그 결과로 판매자 정산금을 내고 지급 승인을 요청한다. 재무는 아래 읽기 skill로 결과를 받아
화면에 보여 주고 회계(미지급금 롤포워드·현금 흐름·은행 출금 대조)에만 쓴다.

- `seller_settlement` (read): 한 주의 판매자별 정산서와 묶음 상태
- `seller_settlement_weeks` (read): 여러 주의 정산서 (재무 현금 흐름·마감 점검용)
- `seller_settlement_batches` (read): 모든 주의 묶음 기록 (재무 미지급금·은행 출금 대조용)
- `seller_settlement_deadlines` (read): 판매 대금 지급 기한 점검
- `request_seller_settlement` (A): 사람이 누른 지급 승인 요청. `execute("confirm_settlement")`가
  안전 장치에서 항상 C로 보류되고, 사람이 보류 큐에서 승인한다

정산은 클레임(claims)과 다른 프로세스(settlement 서비스)에서 돈다(2026-11-12). 주문 30만 건 장부를
읽고 계산하는 동안 클레임 처리가 느려졌기 때문이다. 조회는 저장된 장부·계산 결과로 바로 답하고,
첫 장부를 모으는 중이면 `settlement_preparing`으로 바로 답한다(화면은 "준비 중").
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from ax_agent_template.execute import execute
from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from support_agent import handlers as h
from support_agent import settlement, settlement_batches
from support_agent import settlement_books as sb
from support_agent import settlement_config as sc
from support_agent.settlement_schemas import (
    RequestSellerSettlementInput,
    SellerSettlementBatchesInput,
    SellerSettlementDeadlinesInput,
    SellerSettlementInput,
    SellerSettlementWeeksInput,
)

UNAVAILABLE_EXECUTION = "exe_unavailable"  # agent-template execute()가 safety에 못 닿았을 때
PREPARING = "settlement_preparing"  # 첫 장부를 모으는 중 (부르는 쪽은 "준비 중"으로 보여 준다)


async def _books(request: AgentRequest, fresh: bool = False) -> sb.SettlementBooks | AgentResult:
    try:
        return await sb.load_books(fresh)
    except sb.Preparing:
        return error_result(
            request.meta,
            TaskStatus.FAILED,
            PREPARING,
            "판매자 정산 장부를 모으는 중이다. 잠시 뒤 다시 본다",
        )


def _unavailable(request: AgentRequest, books: sb.SettlementBooks) -> AgentResult:
    missing = ", ".join(books.unavailable)
    return error_result(
        request.meta,
        TaskStatus.FAILED,
        "shop_unavailable",
        f"판매 서버에서 정산 자료를 받지 못했다({missing}). 정산금을 0으로 채우지 않는다",
    )


async def _view(
    books: sb.SettlementBooks, period: str | None, cached: bool = True
) -> dict[str, Any]:
    """정산서 + 묶음 상태. 조회는 같은 장부·같은 주의 계산을 다시 쓴다(같은 계산은 하나만 돈다).

    지급 요청(`cached=False`)은 새로 계산한다. 묶음 상태를 덮어쓰므로 붙여 둔 결과는 복사해 쓴다.
    """
    today = sc.today()
    if cached:
        shared = await books.computed(
            ("statements", *settlement.week_key(period, today)),
            lambda: settlement.statements(books, period, today),
        )
        result = copy.deepcopy(shared)
    else:
        result = await asyncio.to_thread(settlement.statements, books, period, today)
    batch = await settlement_batches.refreshed(result["period"])
    return settlement.apply_batch(result, batch)


async def seller_settlement(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SellerSettlementInput)
    if isinstance(parsed, AgentResult):
        return parsed
    books = await _books(request)
    if isinstance(books, AgentResult):
        return books
    if books.unavailable_core():
        return _unavailable(request, books)
    data = await _view(books, parsed.period)
    return h._done(request, data | {"meta": books.meta()})


async def seller_settlement_weeks(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SellerSettlementWeeksInput)
    if isinstance(parsed, AgentResult):
        return parsed
    for period in parsed.periods:
        try:
            sc.week_range(period, sc.today())
        except ValueError as exc:
            return error_result(request.meta, TaskStatus.FAILED, "invalid_input", str(exc))
    books = await _books(request)
    if isinstance(books, AgentResult):
        return books
    if books.unavailable_core():
        return _unavailable(request, books)
    weeks = {}
    for period in dict.fromkeys(parsed.periods):
        data = await _view(books, period)
        weeks[data["period"]] = data
    return h._done(request, {"weeks": weeks, "meta": books.meta()})


async def seller_settlement_batches(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SellerSettlementBatchesInput)
    if isinstance(parsed, AgentResult):
        return parsed
    batches = await settlement_batches.refreshed_all()
    return h._done(request, {"batches": {k: dict(v) for k, v in sorted(batches.items())}})


async def seller_settlement_deadlines(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SellerSettlementDeadlinesInput)
    if isinstance(parsed, AgentResult):
        return parsed
    books = await _books(request)
    if isinstance(books, AgentResult):
        return books
    if books.unavailable_core():
        return _unavailable(request, books)
    batches = await settlement_batches.refreshed_all()
    today = sc.today()
    report = await books.computed(
        ("deadlines", today.isoformat(), json.dumps(batches, sort_keys=True, default=str)),
        lambda: settlement.deadline_report(books, batches, today),
    )
    report = copy.deepcopy(report)
    return h._done(request, report | {"meta": books.meta()})


async def request_seller_settlement(request: AgentRequest) -> AgentResult:
    """사람이 누른 묶음 지급 승인 요청. 지급 확정은 안전 장치 보류 큐에서 사람이 한다."""
    parsed = h._parse(request, RequestSellerSettlementInput)
    if isinstance(parsed, AgentResult):
        return parsed
    # 돈이 걸린 결정은 `FRESH_SECONDS` 안에 새로 읽은 장부로 새로 계산한다
    books = await _books(request, fresh=True)
    if isinstance(books, AgentResult):
        return books
    if books.unavailable_core():
        return _unavailable(request, books)
    try:
        data = await _view(books, parsed.period, cached=False)
    except ValueError as exc:
        return error_result(request.meta, TaskStatus.FAILED, "invalid_input", str(exc))
    name = data["period"]
    current = settlement_batches.STORE.get(name)
    if current and current["status"] in ("requested", "approved"):
        # 같은 기간은 한 번만 요청한다
        return h._done(request, {"settlement": data | {"meta": books.meta()}, "requested": False})
    rows = settlement.ready(data)
    if not rows:
        return error_result(
            request.meta, TaskStatus.FAILED, "nothing_to_settle", "보류 없이 지급할 판매자가 없다"
        )

    attempt = (current or {}).get("attempt", 0) + 1
    seller_ids = [r["seller_id"] for r in rows]
    total = sum(r["payout"] for r in rows)
    summary = f"{name} 판매자 정산 {len(rows)}곳 {sc.won(total)} 지급 승인 요청"
    keys = ("seller_id", "name", "orders", "gross", "commission", "seller_coupon", "refunds")
    params = {
        "period": name,
        "from": data["from"],
        "to": data["to"],
        "pay_date": data["pay_date"],
        "seller_ids": seller_ids,
        "total_payout": total,
        "statements": [{k: r[k] for k in (*keys, "payout")} for r in rows],
        "summary": summary,
        "requested_by": parsed.requested_by,  # 요청한 사람은 승인하지 못한다 (안전 관문)
    }
    context_id = f"ctx_req_settlement_{name}"
    meta = request.meta.model_copy(
        update={
            "context_id": context_id,
            "idempotency_key": f"{context_id}:confirm_settlement:{name}:{attempt}",
        }
    )
    evidence = Evidence(
        queries=["support:seller_settlement", "shop-api:/api/shop/orders"],
        data_summary=summary,
        collected_at=sc.now(),
        ref_ids=seller_ids,
    )
    decision = await execute("confirm_settlement", params, meta, evidence)
    if decision.execution_id == UNAVAILABLE_EXECUTION or decision.status == "failed":
        return error_result(
            request.meta,
            TaskStatus.FAILED,
            "safety_unavailable",
            "안전 장치에 닿지 않아 정산 묶음을 요청하지 않았다 (지급 기록 없음)",
        )
    settlement_batches.STORE.put(
        name,
        {
            "status": settlement_batches.batch_status(decision.status),
            "review_id": decision.review_id,
            "execution_id": decision.execution_id,
            "total_payout": total,
            "seller_ids": seller_ids,
            "payouts": {r["seller_id"]: r["payout"] for r in rows},
            "attempt": attempt,
            "requested_by": parsed.requested_by,
            "requested_at": sc.iso(sc.now()),
            **({"approved_at": sc.iso(sc.now())} if decision.status == "executed" else {}),
        },
    )
    after = await _view(books, name)
    return h._done(request, {"settlement": after | {"meta": books.meta()}, "requested": True})


SETTLEMENT_HANDLERS = {
    "seller_settlement": seller_settlement,
    "seller_settlement_weeks": seller_settlement_weeks,
    "seller_settlement_batches": seller_settlement_batches,
    "seller_settlement_deadlines": seller_settlement_deadlines,
    "request_seller_settlement": request_seller_settlement,
}

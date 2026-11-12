"""판매자 정산 skill (settlement 서비스).

2026-11-12 팀장 결정으로 판매자 정산을 재무에서 고객지원으로 옮겼다. 고객지원이 환불·클레임을
처리하고, 그 결과로 판매자 정산금을 내고 지급 승인을 요청한다. 재무는 아래 읽기 skill로 결과를 받아
화면에 보여 주고 회계(미지급금 롤포워드·현금 흐름·은행 출금 대조)에만 쓴다.

- `seller_settlement` (read): 한 주의 판매자별 정산서와 묶음 상태
- `seller_settlement_weeks` (read): 여러 주의 정산서 (재무 현금 흐름·마감 점검용)
- `seller_settlement_batches` (read): 모든 주의 묶음 기록 (재무 미지급금·은행 출금 대조용)
- `seller_settlement_deadlines` (read): 판매 대금 지급 기한 점검
- `request_seller_settlement` (A): 사람이 누른 지급 승인 요청. `execute("confirm_settlement")`가
  안전 장치에서 항상 C로 보류되고, 고객지원 담당이 보류 큐에서 승인한다
- 고객지원 AI는 지급일이 된 주를 스스로 같은 길(`request_week`)로 요청한다(`settlement_auto.py`).
  요청자가 사람이 아니므로(`support-ai`) 어느 고객지원 담당이든 승인할 수 있다

정산은 클레임(claims)과 다른 프로세스(settlement 서비스)에서 돈다(2026-11-12). 주문 30만 건 장부를
읽고 계산하는 동안 클레임 처리가 느려졌기 때문이다. 조회는 저장된 장부·계산 결과로 바로 답하고,
첫 장부를 모으는 중이면 `settlement_preparing`으로 바로 답한다(화면은 "준비 중").
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from typing import Any

from ax_agent_template.execute import execute
from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
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
PERIOD_NOT_CLOSED = "period_not_closed"  # 기간이 아직 끝나지 않은 주는 지급을 요청하지 않는다
ORDERS_PARTIAL = "orders_partial"  # 주문 일부를 못 읽었다 (조회는 보여 주고 지급 요청은 막는다)


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


# 고객지원 AI가 스스로 올린 요청의 요청자. 사람이 아니므로 어느 고객지원 담당이든 승인할 수 있다
# (안전 관문은 "요청한 사람 ≠ 승인한 사람"만 본다)
AI_REQUESTER = "support-ai"


@dataclass
class RequestOutcome:
    """묶음 요청 결과. `error`가 있으면 요청하지 않았다(코드, 사람이 읽을 말)."""

    settlement: dict[str, Any] | None = None
    requested: bool = False
    error: tuple[str, str] | None = None


_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def _request_lock() -> asyncio.Lock:
    """사람 버튼과 AI 자동 요청이 같은 주를 동시에 요청하지 않게 한 번에 하나만 요청한다."""
    global _lock
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    return _lock[1]


async def request_week(period: str | None, requested_by: str, meta: RequestMeta) -> RequestOutcome:
    """한 주의 묶음 지급 승인 요청 (사람 버튼·고객지원 AI 자동 요청이 같이 쓴다).

    지급 확정은 안전 장치 보류 큐에서 고객지원 담당이 한다. 막는 경우:
    - `period_not_closed`: 기간이 아직 끝나지 않은 주
    - `shop_unavailable`·`orders_partial`: 주문을 못 받았거나 일부를 못 읽었다
      (금액이 모자랄 수 있다)
    - `nothing_to_settle`: 보류 없이 줄 판매자가 없다
    - `safety_unavailable`: 안전 장치에 닿지 않았다(지급 기록 없음)
    """
    today = sc.today()
    try:
        name, _, _ = sc.week_range(period, today)
    except ValueError as exc:
        return RequestOutcome(error=("invalid_input", str(exc)))
    if not settlement.period_closed(name, today):
        return RequestOutcome(
            error=(
                PERIOD_NOT_CLOSED,
                f"{name} 정산 기간이 아직 끝나지 않았다. 그 주 일요일이 지나야 지급을 요청한다",
            )
        )
    # 돈이 걸린 결정은 `FRESH_SECONDS` 안에 새로 읽은 장부로 새로 계산한다
    try:
        books = await sb.load_books(fresh=True)
    except sb.Preparing:
        return RequestOutcome(
            error=(PREPARING, "판매자 정산 장부를 모으는 중이다. 잠시 뒤 다시 본다")
        )
    if books.unavailable_core():
        missing = ", ".join(books.unavailable)
        return RequestOutcome(
            error=(
                "shop_unavailable",
                f"판매 서버에서 정산 자료를 받지 못했다({missing}). 정산금을 0으로 채우지 않는다",
            )
        )
    if ORDERS_PARTIAL in books.unavailable:
        return RequestOutcome(
            error=(
                ORDERS_PARTIAL,
                "판매 서버 주문 일부를 읽지 못해 정산금이 모자랄 수 있다. "
                "다 읽은 뒤 지급을 요청한다",
            )
        )
    async with _request_lock():
        data = await _view(books, name, cached=False)
        current = settlement_batches.STORE.get(name)
        if current and current["status"] in ("requested", "approved"):
            # 같은 기간은 한 번만 요청한다
            return RequestOutcome(settlement=data | {"meta": books.meta()}, requested=False)
        if current and current.get("closed_reason") == "review_missing":
            # 보류 건을 잃은 주는 다시 요청하기 전에 실행 기록을 한 번 더 본다(두 번 요청하지 않게)
            try:
                record = await settlement_batches.executed_in_safety(
                    name, current.get("execution_id")
                )
            except settlement_batches.SafetyUnreachable:
                return RequestOutcome(
                    error=(
                        "safety_unavailable",
                        "안전 장치 실행 기록을 읽지 못해 다시 요청하지 않았다 (지급 기록 없음)",
                    )
                )
            if record is not None:
                settlement_batches.mark_approved(current, record)
                settlement_batches.STORE.save()
                after = await _view(books, name)
                return RequestOutcome(settlement=after | {"meta": books.meta()}, requested=False)
        rows = settlement.ready(data)
        if not rows:
            return RequestOutcome(error=("nothing_to_settle", "보류 없이 지급할 판매자가 없다"))

        attempt = (current or {}).get("attempt", 0) + 1
        seller_ids = [r["seller_id"] for r in rows]
        total = sum(r["payout"] for r in rows)
        who = "고객지원 AI 자동 요청" if requested_by == AI_REQUESTER else f"요청 {requested_by}"
        summary = f"{name} 판매자 정산 {len(rows)}곳 {sc.won(total)} 지급 승인 요청 ({who})"
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
            # 요청한 사람은 승인하지 못한다 (안전 관문). AI 요청이면 어느 고객지원 담당이든 승인한다
            "requested_by": requested_by,
        }
        exec_meta = meta.model_copy(
            update={
                "context_id": settlement_batches.context_id_of(name),
                "idempotency_key": f"{settlement_batches.key_prefix(name)}{attempt}",
            }
        )
        evidence = Evidence(
            queries=["support:seller_settlement", "shop-api:/api/shop/orders"],
            data_summary=summary,
            collected_at=sc.now(),
            ref_ids=seller_ids,
        )
        decision = await execute("confirm_settlement", params, exec_meta, evidence)
        if decision.execution_id == UNAVAILABLE_EXECUTION or decision.status == "failed":
            return RequestOutcome(
                error=(
                    "safety_unavailable",
                    "안전 장치에 닿지 않아 정산 묶음을 요청하지 않았다 (지급 기록 없음)",
                )
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
                "requested_by": requested_by,
                "requested_at": sc.iso(sc.now()),
                **({"approved_at": sc.iso(sc.now())} if decision.status == "executed" else {}),
            },
        )
        after = await _view(books, name)
    return RequestOutcome(settlement=after | {"meta": books.meta()}, requested=True)


async def request_seller_settlement(request: AgentRequest) -> AgentResult:
    """사람이 누른 묶음 지급 승인 요청. 고객지원 AI도 같은 길(`request_week`)로 스스로 요청한다."""
    parsed = h._parse(request, RequestSellerSettlementInput)
    if isinstance(parsed, AgentResult):
        return parsed
    out = await request_week(parsed.period, parsed.requested_by, request.meta)
    if out.error is not None:
        code, message = out.error
        return error_result(request.meta, TaskStatus.FAILED, code, message)
    return h._done(request, {"settlement": out.settlement, "requested": out.requested})


SETTLEMENT_HANDLERS = {
    "seller_settlement": seller_settlement,
    "seller_settlement_weeks": seller_settlement_weeks,
    "seller_settlement_batches": seller_settlement_batches,
    "seller_settlement_deadlines": seller_settlement_deadlines,
    "request_seller_settlement": request_seller_settlement,
}

"""finance skill 핸들러.

| skill | 등급 | 요청자 | 하는 일 |
| --- | --- | --- | --- |
| `closing_report` | read | orchestrator, data | ① 기간 손익 요약 (`closing.py`) |
| `audit_report` | read | orchestrator, data | ⑦ 사후 감사 결과 (`audit.py`) |

`verify_amount`·`review_hold`는 재무 재설계(2026-10-23)로, `loss_aggregation`은 2026-10-27에
없앴다 (재무는 장애 대응 흐름의 단계가 아니다 — 요구사항 DEP-FIN-01, 공통 구성안 결정 22).
장애 비용은 평상시 장부로 들어가 `closing_report`·`audit_report`에서 드러난다. 금액 다시 계산 규칙은
`recheck.py`에 남아 사후 감사가 쓴다. 고객사 클레임 건별 확인과 `review_threshold_change`(AI가 기준
변경을 판단)도 재무 일이 아니라 없앴다. 한도는 제안만 하고(`policy.py`) 사람이 기준표 PR로 바꾼다.

- 숫자는 전부 코드로 계산한다. 요청 쪽이 적은 총액·등급은 믿지 않는다.
- 외부 입력(`request.untrusted`)은 읽지 않는다.
- 마켓운영(판매자 입점 심사·정산 보류)은 재무 일이 아니라 없앴다(2026-10-30). 재무는 판매자 정산
  내역을 계산만 하고, 판매자를 받거나 돈을 묶는 결정은 하지 않는다.
- 기록은 메모리에 두고 `FINANCE_STATE_FILE`이 있으면 파일에도 남긴다
  (`persist.py`, 다시 켜도 이어짐).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from ax_agent_template.config import is_mock
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    EventEnvelope,
    EventType,
    Evidence,
    RequestMeta,
    TaskStatus,
)
from finance_agent import anomalies, audit, chat_log, closing
from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent import ledger as lg
from finance_agent.books import Books, load_books
from finance_agent.recheck import recompute
from finance_agent.schemas import (
    AuditReportInput,
    AuditReportOutput,
    ClosingReportInput,
    ClosingReportOutput,
    SupportCostInput,
    SupportCostOutput,
)
from finance_agent.sources import Impact, LiveSources, MockSources, Sources, order_key

__all__ = ["HANDLERS", "SUBSCRIBED_EVENTS", "on_event", "recompute", "reset_state", "state"]


class Ledger:
    """이벤트로 쌓는 지급·환불 기록과 처리 결과."""

    def __init__(self) -> None:
        self.coupons: dict[str, dict[str, int]] = {}  # context_id → {"count", "total"}
        self.refunds: dict[str, int] = {}  # order_id → 환불 누적
        self.seen_events: set[str] = set()
        # 문의 번호 → 고객지원 처리 결과 비용 기록
        self.support_costs: dict[int, dict[str, Any]] = {}
        self.coupon_events: dict[str, list[dict[str, Any]]] = {}  # context_id → 보상 쿠폰 이벤트

    def compensation(self, context_id: str) -> dict[str, int]:
        return self.coupons.get(context_id, {"count": 0, "total": 0})


class State:
    def __init__(self, sources: Sources | None = None) -> None:
        self.sources: Sources = sources or (MockSources() if is_mock("finance") else LiveSources())
        self.ledger = Ledger()
        self.last_self_test: dict[str, Any] | None = None  # 마지막 "재무 AI 테스트" 결과
        self.last_daily_closing: dict[str, Any] | None = None  # 마지막 자동 결산(전날) 결과
        self.records = FinanceRecords()
        self.books_loader: Callable[[], Awaitable[Books]] = load_books  # 테스트에서 바꾼다
        # 사건별 쿠폰 장부(⑨)의 실행 기록과 기준표. 테스트에서 바꾼다
        self.executions_loader: Callable[[str | None], Awaitable[lg.ExecutionFeed]] = (
            lg.load_executions
        )
        self.thresholds_loader: Callable[[], Awaitable[dict[str, Any] | None]] = lg.load_thresholds

    async def books(self) -> Books:
        return await self.books_loader()



class FinanceRecords:
    """사람의 결정 기록. `FINANCE_STATE_FILE`이 있으면 `persist.py`가 파일에 남긴다."""

    def __init__(self) -> None:
        self.closings: dict[tuple[str, str], dict[str, Any]] = {}  # (period, date) → {by, at}
        self.recon_notes: dict[str, str] = {}  # payment_id → 처리 메모
        self.recon_note_log: list[dict[str, Any]] = []
        self.batches: dict[str, dict[str, Any]] = {}  # 정산 기간 → 묶음 요청 상태
        self.anomalies: dict[str, dict[str, Any]] = {}  # 의심 건 번호 → 처리 전·후 기록
        self.policy_acks: dict[str, dict[str, Any]] = {}  # 규칙 키 → 경보 확인 기록
        self.bank_rows: list[dict[str, Any]] = []  # 사람이 올린 은행 입금 내역


STATE: State | None = None


def state() -> State:
    global STATE
    if STATE is None:
        STATE = State()
    return STATE


def reset_state(sources: Sources | None = None) -> None:
    """테스트 사이에 저장소를 비운다."""
    global STATE
    STATE = State(sources)


async def note_audit(findings: list[dict[str, Any]], tenant_id: str) -> None:
    """사후 감사가 찾은 의심 건을 처리할 때까지 남기고, 처음 본 열린 건만 총괄에 알린다."""
    store = state().records.anomalies
    pending = anomalies.note_findings(store, findings)
    if pending:
        anomalies.mark_announced(store, await chat_log.audit_findings(pending, tenant_id))


# ---------------------------------------------------------------- 공통
def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(queries: list[str], summary: str, ref_ids: list[str] | None = None) -> Evidence:
    return Evidence(
        queries=queries, data_summary=summary, collected_at=_now(), ref_ids=ref_ids or []
    )


def _result(request: AgentRequest, status: TaskStatus, **kwargs: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _parse[T: BaseModel](request: AgentRequest, model: type[T]) -> T | AgentResult:
    try:
        return model.model_validate(request.input)
    except ValidationError as error:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
        return _result(
            request, TaskStatus.FAILED, outcome="invalid_input", output={"errors": errors}
        )


def _positive_int(value: Any) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok else None


# ---------------------------------------------------------------- 이벤트 (지급·환불 기록)
async def on_event(envelope: EventEnvelope) -> None:
    """`coupon.issued`(보상 쿠폰)와 `payment.refunded`를 누적한다. 같은 event_id는 한 번만."""
    ledger = state().ledger
    if envelope.event_id in ledger.seen_events:
        return
    ledger.seen_events.add(envelope.event_id)
    data = envelope.data
    if envelope.type == EventType.COUPON_ISSUED and envelope.context_id:
        if data.get("purpose_code") != "compensation":
            return
        count, total = _positive_int(data.get("count")), _positive_int(data.get("total_value"))
        if count is None or total is None:
            return
        row = ledger.coupons.setdefault(envelope.context_id, {"count": 0, "total": 0})
        row["count"] += count
        row["total"] += total
        ledger.coupon_events.setdefault(envelope.context_id, []).append(
            lg.coupon_from_event(envelope.context_id, data, envelope.occurred_at)
        )
        chat_log.ledger_recorded(
            envelope.context_id, envelope.tenant_id, envelope.source, "coupon", total, count
        )
    elif envelope.type == EventType.PAYMENT_REFUNDED:
        order_id, amount = order_key(data.get("order_id")), _positive_int(data.get("refund_amount"))
        if order_id is not None and amount is not None:
            ledger.refunds[order_id] = ledger.refunds.get(order_id, 0) + amount
            chat_log.ledger_recorded(
                envelope.context_id, envelope.tenant_id, envelope.source, "refund", amount
            )


SUBSCRIBED_EVENTS = [EventType.COUPON_ISSUED, EventType.PAYMENT_REFUNDED]


# ---------------------------------------------------------------- 사건 보상 쿠폰 공용
async def compensation_ledger(
    context_id: str, feed: lg.ExecutionFeed | None = None
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """한 사건의 보상 쿠폰 장부 줄과 출처 (`ledger.merge`)."""
    if feed is None:
        feed = await state().executions_loader(context_id)
    event_rows = state().ledger.coupon_events.get(context_id, [])
    return lg.merge(context_id, feed, event_rows, lg.events_connected())


def incident_meta(context_id: str) -> dict[str, Any]:
    """사건의 장애 구간·부품 중 재무가 혼자 아는 것. (목 모드) 시드 → 모름.

    실제 모드에서는 모름이다. 구간을 알려 주던 총괄의 `loss_aggregation` 요청을 2026-10-27에
    없앴다(재무는 장애 대응 흐름의 단계가 아니다). 실제 모드 구간은 `incident_info`가
    총괄 사건 목록을 읽어서 채운다.
    """
    if is_mock("finance"):
        seeded = bk.seed_incidents().get(context_id)
        if seeded:
            return seeded | {"category": None}
    return {"component": None, "window_from": None, "window_to": None, "category": None}


async def incident_info(context_id: str) -> dict[str, Any]:
    """사건의 장애 구간·부품. 목 모드는 시드, 실제 모드는 총괄 사건 목록(읽기 전용).

    총괄에게 사건 **중**에 묻는 것이 아니다 — 평상시 사후 감사·사건 조회에서만 읽는다
    ([협업] #417 제안 A, 요청 #418, 계약 `data_access/orchestrator_incidents.py`).
    못 받으면 모름(None)이고 추정값을 만들지 않는다(fail-closed).
    """
    info = incident_meta(context_id)
    if info["window_from"] and info["window_to"]:
        return info
    found = await state().sources.incident_window(context_id)
    if not found:
        return info
    return info | {k: v for k, v in found.items() if v is not None}


async def incident_impact(
    context_id: str, meta: RequestMeta | None = None, info: dict[str, Any] | None = None
) -> Impact | None:
    """data `impact_analysis` 결과 그대로(다시 계산하지 않음). 구간을 모르면 None.

    `info`를 받으면 사건 구간을 다시 조회하지 않는다 (`incident_view`가 한 번만 읽게).
    """
    if info is None:
        info = await incident_info(context_id)
    window = None
    if info["window_from"] and info["window_to"]:
        window = {
            "window_from": fc.iso(info["window_from"]),
            "window_to": fc.iso(info["window_to"]),
            **({"category": info["category"]} if info.get("category") else {}),
        }
    base = meta or RequestMeta(
        context_id=context_id, token="finance-dashboard", deadline=_now() + timedelta(seconds=15)
    )
    return await state().sources.impact(
        base.model_copy(update={"context_id": context_id}), context_id, window
    )


# ------------------------------------------------------- closing_report·audit_report (읽기)
BOOK_QUERIES = [
    "shop-api:/api/shop/orders",
    "shop-api:/api/shop/coupons",
    "safety:/api/safety/executions",
]


def closing_key(period: str, d: Any) -> tuple[str, str]:
    """결산 확정 기록의 키. 주는 그 주 월요일, 월은 그 달 1일로 맞춘다."""
    start, _ = closing.period_range(period, d)  # type: ignore[arg-type]
    return period, (d if period == "day" else start).isoformat()


async def closing_report(request: AgentRequest) -> AgentResult:
    """① 기간 손익 요약. 대시보드 `GET /api/finance/closing`과 같은 계산이다."""
    data = _parse(request, ClosingReportInput)
    if isinstance(data, AgentResult):
        return data
    books = await state().books()
    d = data.date or fc.today()
    key = closing_key(data.period, d)
    report = await closing.closing_report(books, data.period, d, state().records.closings.get(key))
    output = ClosingReportOutput(
        **{k: v for k, v in report.items() if k not in ("from", "to")},
        window_from=report["from"],
        window_to=report["to"],
        unavailable=books.meta()["unavailable"],
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="partial" if output.unavailable else "reported",
        output=output.model_dump(mode="json"),
        evidence=_evidence(
            [*BOOK_QUERIES[:2], f"closing:{data.period}:{key[1]}"], report["commentary"]
        ),
    )


async def audit_report(request: AgentRequest) -> AgentResult:
    """⑦ 사후 감사 결과. 대시보드 `GET /api/finance/audit`과 같은 계산이다."""
    data = _parse(request, AuditReportInput)
    if isinstance(data, AgentResult):
        return data
    books = await state().books()

    async def lookup(ctx: str) -> Impact | None:
        return await incident_impact(ctx, request.meta)

    report, missing = await audit.audit_report(
        books, data.days, impact_lookup=lookup, context_id=data.context_id
    )
    await note_audit(report["findings"], request.meta.tenant_id)
    if data.finding_ref:
        # 알림받은 의심 건 하나: 기간이 지나도 목록에 남은 기록으로 본다
        entry = state().records.anomalies.get(data.finding_ref)
        found = [f for f in report["findings"] if anomalies.signature(f) == data.finding_ref]
        report["findings"] = found or ([anomalies.finding_view(entry)] if entry else [])
    output = AuditReportOutput(
        window_from=report["from"],
        window_to=report["to"],
        stats=report["stats"],
        findings=report["findings"],
        unavailable=books.meta(missing)["unavailable"],
    )
    codes = sorted({f.code for f in output.findings})
    summary = (
        f"최근 {data.days}일 감사 의심 {len(output.findings)}건 ({', '.join(codes) or '없음'})"
    )
    refs = [r for f in output.findings for r in f.refs][:20]
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="partial" if output.unavailable else "reported",
        output=output.model_dump(mode="json"),
        evidence=_evidence([*BOOK_QUERIES, f"audit:{data.days}d"], summary, refs),
    )


# ------------------------------------------------- record_support_cost (읽기·장부 기록)
SUPPORT_COST_QUERIES = ["shop-api:/api/shop/orders"]


async def record_support_cost(request: AgentRequest) -> AgentResult:
    """고객지원이 닫은 문의의 처리 결과를 받아 배송비·비용 기록을 확인하고 재무 장부에 남긴다.

    돈을 움직이거나 외부 상태를 바꾸지 않는다(그래서 읽기 등급이다). 경비를 쓰지 않은 문의
    (답변만 한 문의, 환불·쿠폰이 없는 문의)도 상담 처리 결과를 기록으로 남긴다.
    같은 문의는 한 번만 기록한다. 주문을 장부에서 찾지 못하면 배송비를 비우고 이유를 적는다.
    """
    data = _parse(request, SupportCostInput)
    if isinstance(data, AgentResult):
        return data
    ledger = state().ledger
    refund = data.refund_amount or 0
    coupon = data.coupon_id is not None
    unavailable: list[str] = []
    shipping_fee: int | None = None
    if data.order_id is not None:
        books = await state().books()
        order = books.order(data.order_id)
        if order is not None:
            shipping_fee = int(order.get("shipping_fee") or 0)
        else:
            unavailable.append(f"장부에 주문 {data.order_id}가 없어 배송비를 확인하지 못함")
        unavailable.extend(books.meta([])["unavailable"])
    else:
        unavailable.append("문의에 주문이 연결되지 않아 배송비를 확인하지 못함")
    expense = refund > 0 or coupon
    previous = ledger.support_costs.get(data.inquiry_id)
    record = previous or {
        "record_id": f"sc_{data.inquiry_id}",
        "inquiry_id": data.inquiry_id,
        "order_id": data.order_id,
        "resolution": data.resolution,
        "claim_decision": data.claim_decision,
        "refund_amount": refund,
        "coupon_issued": coupon,
        "expense_used": expense,
        "shipping_fee": shipping_fee,
        "context_id": request.meta.context_id,
        "recorded_at": _now().isoformat(),
    }
    ledger.support_costs[data.inquiry_id] = record
    spent = "경비 사용 없음"
    if expense:
        spent = f"환불 {refund:,}원" + (", 쿠폰 지급" if coupon else "")
    note = f"문의 #{data.inquiry_id} 처리 결과 기록: {data.resolution}, {spent}"
    output = SupportCostOutput(
        record_id=record["record_id"],
        inquiry_id=data.inquiry_id,
        order_id=record["order_id"],
        status="already_recorded" if previous is not None else "recorded",
        resolution=record["resolution"],
        claim_decision=record["claim_decision"],
        refund_amount=record["refund_amount"],
        coupon_issued=record["coupon_issued"],
        expense_used=record["expense_used"],
        shipping_fee=record["shipping_fee"],
        note=note,
        unavailable=unavailable,
    )
    refs = [f"inquiry:{data.inquiry_id}"] + ([f"order:{data.order_id}"] if data.order_id else [])
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="partial" if unavailable else "recorded",
        output=output.model_dump(mode="json"),
        evidence=_evidence(SUPPORT_COST_QUERIES, note, refs),
    )


HANDLERS = {
    "closing_report": closing_report,
    "audit_report": audit_report,
    "record_support_cost": record_support_cost,
}

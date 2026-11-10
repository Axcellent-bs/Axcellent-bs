"""사건(장애) 보상의 "대기 → 사람 승인 → 발송" skill (준하님 결정 2026-11-04: 사람 확인 후).

- `propose_incident_compensation`(A): 총괄이 영향 고객 목록을 넘기면 고객마다 대기 한 줄로 쌓는다.
  쿠폰은 요청하지 않는다. 지급·한도 검사는 승인 뒤 마케팅 `issue_coupon`이 한다.
- `decide_incident_compensation`(A, 사람 토큰): 고객별로 승인·거절한다. 승인된 고객만 기존
  `request_incident_compensation` 경로로 마케팅에 요청한다.
- `list_incident_queue`(읽기): 대기·승인·거절 목록.

저장소와 상태는 `incident_queue.py`. 쓰기 두 개는 안전 관문(`execute()`)에 등록돼 있다.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ax_agent_template.server import error_result
from ax_contracts.schemas import (
    HANDED_TO_HUMAN,
    AgentRequest,
    AgentResult,
    Evidence,
    TaskStatus,
)
from ax_contracts.schemas.event import SupportIncidentCompensationData
from support_agent import compensation, events
from support_agent import handlers as h
from support_agent.incident_handlers import (
    INCIDENT_CONTEXT_PREFIX,
    _key,
    clean_buyers,
    issued_in_incident,
    request_incident_compensation,
)
from support_agent.incident_queue import OPEN_STATUSES, QueueItem
from support_agent.incident_schemas import (
    DecidedBuyer,
    DecideIncidentCompensationInput,
    DecideIncidentCompensationOutput,
    IncidentQueue,
    ListIncidentQueueInput,
    ListIncidentQueueOutput,
    ProposeIncidentCompensationInput,
    ProposeIncidentCompensationOutput,
    QueuedBuyer,
    QueueRow,
)
from support_agent.store import now_iso

SUMMARY_FIELDS = (
    "pending",
    "approved",
    "held",
    "sent",
    "rejected",
    "remaining",
    "coupons_sent",
    "sent_value",
)

QUEUE_NOTES = [
    "대기에 올린 고객은 사람이 승인하기 전에는 쿠폰을 요청하지 않는다",
    "지급과 금액 한도 검사는 승인 뒤 마케팅 issue_coupon이 안전 관문을 거쳐 한다",
]


def _digest(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _invalid(request: AgentRequest, message: str) -> AgentResult:
    return error_result(request.meta, TaskStatus.FAILED, "invalid_input", message)


async def propose_incident_compensation(request: AgentRequest) -> AgentResult:
    """영향 고객을 보상 대기로 쌓는다. 쿠폰은 요청하지 않는다 (총괄만, 사건 안에서만)."""
    parsed = h._parse(request, ProposeIncidentCompensationInput)
    if isinstance(parsed, AgentResult):
        return parsed
    context_id = request.meta.context_id
    if not context_id.startswith(INCIDENT_CONTEXT_PREFIX):
        return _invalid(request, "사건(ctx_inc_) 요청에서만 받는다")
    buyers = clean_buyers(parsed.buyer_ids)
    if buyers is None:
        return _invalid(request, "구매자 ID 형식이 맞지 않는다")
    decision = compensation.decide(parsed.reason_code, parsed.count)
    if not decision.eligible:
        return h._done(
            request, {"eligible": False, "reason": decision.reason}, outcome="not_eligible"
        )
    requester = request.meta.requester or "orchestrator"

    async def apply() -> tuple[dict[str, Any], bool]:
        async with h.LEDGER.lock(context_id):
            issued_buyers, issued_total = issued_in_incident(context_id)
            open_buyers, open_total = h.INCIDENT_QUEUE.totals(context_id, OPEN_STATUSES)
            results: list[QueuedBuyer] = []
            added = 0
            limit_reached = False
            for buyer in buyers:
                if _key(context_id, buyer, parsed.reason_code) in h.LEDGER.results:
                    results.append(QueuedBuyer(buyer_id=buyer, status="already_compensated"))
                    continue
                existing = h.INCIDENT_QUEUE.get(context_id, buyer, parsed.reason_code)
                if existing is not None:
                    results.append(
                        QueuedBuyer(
                            buyer_id=buyer, status="already_queued", queue_status=existing.status
                        )
                    )
                    continue
                if (
                    issued_buyers + open_buyers + added + 1 > compensation.MAX_INCIDENT_BUYERS
                    or issued_total + open_total + (added + 1) * decision.total_value
                    > compensation.MAX_INCIDENT_TOTAL
                ):
                    limit_reached = True
                    results.append(QueuedBuyer(buyer_id=buyer, status="skipped"))
                    continue
                h.INCIDENT_QUEUE.add(
                    context_id,
                    buyer,
                    parsed.reason_code,
                    count=decision.count,
                    unit_value=decision.unit_value,
                    impact_ref=parsed.impact_ref,
                    created_by=requester,
                    tenant_id=request.meta.tenant_id,
                )
                results.append(QueuedBuyer(buyer_id=buyer, status="queued", queue_status="pending"))
                added += 1
        notes = list(QUEUE_NOTES)
        if limit_reached:
            notes.append(
                f"사건당 상한(최대 {compensation.MAX_INCIDENT_BUYERS}명, "
                f"{compensation.MAX_INCIDENT_TOTAL}원)에 닿아 남은 고객은 올리지 않았다"
            )
        body = ProposeIncidentCompensationOutput(
            reason_code=parsed.reason_code,
            unit_value=decision.unit_value,
            count=decision.count,
            queued=added,
            results=results,
            limit_reached=limit_reached,
            notes=notes,
        )
        return body.model_dump(mode="json"), False

    return await h._write(
        request,
        "propose_incident_compensation",
        f"{context_id}:{_digest(parsed.reason_code, parsed.count, *sorted(buyers))}",
        {"reason_code": parsed.reason_code, "buyers": len(buyers)},
        apply,
    )


async def _send_approved(
    request: AgentRequest, context_id: str, items: list[QueueItem]
) -> tuple[dict[str, DecidedBuyer], bool]:
    """승인된 줄을 기존 `request_incident_compensation` 경로로 마케팅에 요청하고 줄을 고친다.

    쿠폰 번호(`sequence`)가 사건 단위라서 요청의 사건 번호를 큐의 사건으로 바꿔 보낸다.
    마케팅에 닿지 않은 고객은 `approved`로 남아 다시 승인하면 이어서 보낸다.
    """
    decided: dict[str, DecidedBuyer] = {}
    stopped = False
    groups: dict[tuple[str, int, str | None], list[QueueItem]] = {}
    for item in items:
        groups.setdefault((item.reason_code, item.count, item.impact_ref), []).append(item)
    for (reason, count, impact_ref), rows in groups.items():
        fields: dict[str, Any] = {
            "buyer_ids": [r.buyer_id for r in rows],
            "reason_code": reason,
            "count": count,
        }
        if impact_ref:
            fields["impact_ref"] = impact_ref
        meta = request.meta.model_copy(update={"context_id": context_id, "idempotency_key": None})
        sub = AgentRequest(meta=meta, skill="request_incident_compensation", input=fields)
        result = await request_incident_compensation(sub)
        coupons = result.output.get("results") if isinstance(result.output, dict) else None
        if result.status != TaskStatus.COMPLETED or not isinstance(coupons, list):
            held = result.outcome == HANDED_TO_HUMAN  # 쿠폰 서비스가 꺼져 담당 부서로 넘어갔다
            stopped = stopped or not held
            for row in rows:
                if held:
                    h.INCIDENT_QUEUE.update(row, status="held")
                decided[row.buyer_id] = DecidedBuyer(
                    buyer_id=row.buyer_id,
                    status="held" if held else "approved",
                    total_value=row.total_value,
                )
            continue
        by_buyer = {c["buyer_id"]: c for c in coupons if isinstance(c, dict)}
        for row in rows:
            sent = by_buyer.get(row.buyer_id) or {}
            state = sent.get("status")
            if state in ("requested", "duplicate"):
                coupon_id = sent.get("coupon_id")
                coupon = coupon_id if isinstance(coupon_id, str) else None
                h.INCIDENT_QUEUE.update(row, status="sent", coupon_id=coupon, sent_at=now_iso())
                decided[row.buyer_id] = DecidedBuyer(
                    buyer_id=row.buyer_id,
                    status="sent",
                    coupon_id=coupon,
                    total_value=row.total_value,
                )
            elif state == "held":  # 마케팅 한도 초과, 안전 관문 보류 큐에서 사람이 승인한다
                h.INCIDENT_QUEUE.update(row, status="held")
                decided[row.buyer_id] = DecidedBuyer(
                    buyer_id=row.buyer_id, status="held", total_value=row.total_value
                )
            else:  # pending·failed·skipped: 아직 못 보냈다
                stopped = True
                decided[row.buyer_id] = DecidedBuyer(
                    buyer_id=row.buyer_id, status="approved", total_value=row.total_value
                )
    return decided, stopped


async def _announce(request: AgentRequest, context_id: str, rows: list[DecidedBuyer]) -> None:
    """이번 결정으로 줄이 바뀌었으면 사건 큐의 지금 상태를 총괄에 한 번 알린다.

    알리기만 하고 발행이 실패해도 결정은 그대로다.

    바뀐 것이 없으면(`not_found`·`not_pending`뿐) 알리지 않는다. 고객별 값은 싣지 않는다.
    """
    if not any(r.status in ("approved", "rejected", "sent", "held") for r in rows):
        return
    summary = h.INCIDENT_QUEUE.summary(context_id)
    data = SupportIncidentCompensationData(
        incident_context_id=context_id,
        **{name: summary[name] for name in SUMMARY_FIELDS},
    )
    await events.emit_incident_compensation_decided(request, data, datetime.now(UTC))


async def decide_incident_compensation(request: AgentRequest) -> AgentResult:
    """사람이 대기 고객을 고객별로 승인·거절한다. 승인된 고객만 마케팅에 쿠폰을 요청한다."""
    parsed = h._parse(request, DecideIncidentCompensationInput)
    if isinstance(parsed, AgentResult):
        return parsed
    context_id = parsed.incident_context_id
    approve = clean_buyers(parsed.approve_buyer_ids)
    reject = clean_buyers(parsed.reject_buyer_ids)
    if approve is None or reject is None:
        return _invalid(request, "구매자 ID 형식이 맞지 않는다")
    if not approve and not reject:
        return _invalid(request, "승인하거나 거절할 고객이 없다")
    if set(approve) & set(reject):
        return _invalid(request, "같은 고객을 승인과 거절에 함께 넣을 수 없다")
    author = parsed.author.strip()
    digest = _digest(context_id, *sorted(approve), "|", *sorted(reject))

    async def apply() -> tuple[dict[str, Any], bool]:
        results: list[dict[str, Any]] = []
        for buyer in approve:
            item = h.INCIDENT_QUEUE.find_buyer(context_id, buyer)
            if item is None:
                results.append({"buyer_id": buyer, "status": "not_found"})
            elif item.status == "pending":
                h.INCIDENT_QUEUE.decide(item, "approved", author)
                results.append({"buyer_id": buyer, "status": "approved"})
            elif item.status == "approved":  # 승인했는데 못 보낸 줄: 아래에서 이어서 보낸다
                results.append({"buyer_id": buyer, "status": "approved"})
            else:
                results.append({"buyer_id": buyer, "status": "not_pending"})
        for buyer in reject:
            item = h.INCIDENT_QUEUE.find_buyer(context_id, buyer)
            if item is None:
                results.append({"buyer_id": buyer, "status": "not_found"})
            elif item.status == "pending":
                h.INCIDENT_QUEUE.decide(item, "rejected", author)
                results.append({"buyer_id": buyer, "status": "rejected"})
            else:
                results.append({"buyer_id": buyer, "status": "not_pending"})
        return {"results": results}, False

    recorded = await h._write(
        request,
        "decide_incident_compensation",
        f"{context_id}:{digest}",
        {"approve": len(approve), "reject": len(reject)},
        apply,
    )
    if "results" not in recorded.output:
        return recorded  # 안전 관문이 막았거나 보류했다. 줄은 바뀌지 않았다

    first = {r["buyer_id"]: r["status"] for r in recorded.output["results"]}
    to_send: list[QueueItem] = []
    for buyer in approve:
        item = h.INCIDENT_QUEUE.find_buyer(context_id, buyer)
        if first.get(buyer) == "approved" and item is not None and item.status == "approved":
            to_send.append(item)
    sent: dict[str, DecidedBuyer] = {}
    stopped = False
    if to_send:  # 사건 잠금은 request_incident_compensation이 직접 건다 (다시 걸면 멈춘다)
        sent, stopped = await _send_approved(request, context_id, to_send)
    rows: list[DecidedBuyer] = []
    for buyer in [*approve, *reject]:
        if buyer in sent:
            rows.append(sent[buyer])
            continue
        item = h.INCIDENT_QUEUE.find_buyer(context_id, buyer)
        rows.append(
            DecidedBuyer(
                buyer_id=buyer,
                status=first.get(buyer, "not_found"),
                coupon_id=item.coupon_id if item else None,
                total_value=item.total_value if item else 0,
            )
        )
    notes = list(QUEUE_NOTES)
    if stopped:
        notes.append("마케팅에 닿지 않은 고객은 승인 상태로 남았다. 다시 승인하면 이어서 보낸다")
    await _announce(request, context_id, rows)
    body = DecideIncidentCompensationOutput(
        incident_context_id=context_id,
        results=rows,
        approved=sum(1 for b in approve if first.get(b) == "approved"),
        rejected=sum(1 for r in rows if r.status == "rejected"),
        sent=sum(1 for r in rows if r.status == "sent"),
        stopped_early=stopped,
        notes=notes,
    )
    return h._done(request, body.model_dump(mode="json"))


async def list_incident_queue(request: AgentRequest) -> AgentResult:
    """보상 대기·승인·거절 목록을 돌려준다 (읽기라 `execute()`를 안 거친다)."""
    parsed = h._parse(request, ListIncidentQueueInput)
    if isinstance(parsed, AgentResult):
        return parsed
    statuses = None if parsed.include_closed else OPEN_STATUSES
    rows = h.INCIDENT_QUEUE.list(parsed.incident_context_id, statuses)
    by_context: dict[str, list[QueueItem]] = {}
    for row in rows:
        by_context.setdefault(row.context_id, []).append(row)
    if parsed.incident_context_id is not None:
        by_context.setdefault(parsed.incident_context_id, [])
    incidents: list[IncidentQueue] = []
    for context, items in sorted(by_context.items()):
        pending = [i for i in items if i.status == "pending"]
        incidents.append(
            IncidentQueue(
                context_id=context,
                pending_buyers=len(pending),
                pending_total=sum(i.total_value for i in pending),
                rows=[QueueRow(**i.model_dump(include=set(QueueRow.model_fields))) for i in items],
            )
        )
    output = ListIncidentQueueOutput(incidents=incidents, notes=QUEUE_NOTES)
    return h._done(
        request,
        output.model_dump(mode="json"),
        evidence=Evidence(
            ref_ids=[i.context_id for i in incidents],
            data_summary=f"보상 대기가 있는 사건 {len(incidents)}건",
        ),
    )


INCIDENT_QUEUE_HANDLERS = {
    "propose_incident_compensation": propose_incident_compensation,
    "decide_incident_compensation": decide_incident_compensation,
    "list_incident_queue": list_incident_queue,
}

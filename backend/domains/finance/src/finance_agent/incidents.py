"""사건별 재무 화면 (재설계 문서 12장). 쿠폰·환불은 끝난 금액만 적는 장부다.

| 메서드·경로 | 하는 일 |
| --- | --- |
| `GET /api/finance/incidents` | 재무가 아는 사건 목록 (장부·관문 기록의 `ctx_inc_`) |
| `GET /api/finance/incidents/{context_id}` | 영향·보상 쿠폰 내역·환불 금액 합계 |

- 재무는 장애 대응 흐름의 단계가 아니다 (요구사항 DEP-FIN-01, 공통 구성안 결정 22).
  2026-10-27에 손실 집계(`loss`)와 사건 단계 표시(`steps` 9·10·17)를 뺐다. 장애 비용은
  평상시 장부로 들어가 결산(`closing_report`)에서 드러난다.
  화면 호환으로 한 단계 남겨 뒀던 `steps`·`loss_total` 키도 지웠다 (화면 정리 ax-frontend#61).
- 쿠폰·환불 한도 사용률(`limits`)과 점검(`checks`)은 재무 일이 아니라 2026-11-02에 뺐다.
- 영향(`impact`)은 data `impact_analysis` 결과 그대로다(실제 모드 A2A, 목 모드 시드).
  다시 계산하지 않는다. 장애 구간은 총괄 사건 목록(`GET /api/incidents`)을 읽기 전용으로
  읽어서 안다 ([협업] #417 제안 A, 요청 #418). 못 읽으면 구간·영향 모두 null이다.
- 상태를 바꾸지 않는다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ax_agent_template.config import is_mock
from finance_agent import finance_config as fc
from finance_agent import ledger as lg
from finance_agent.handlers import compensation_ledger, incident_impact, incident_info, state
from finance_agent.sources import Impact

INCIDENT_PREFIX = "ctx_inc_"


# ---------------------------------------------------------------- 조립
def incident_refunds(
    books: Any, window: dict[str, Any] | None, impact_ids: frozenset[str] | set[str]
) -> dict[str, Any] | None:
    """사건으로 빠져나간 환불 **금액**. 사건 구간 안에 영향 고객에게 나간 환불만 더한다.

    건별 환불 처리는 고객지원 일이라 재무는 금액만 적는다(건수는 금액을 읽는 단위로 함께).
    구간(총괄 사건 목록)이나 영향 고객(data 영향 분석) 중 하나라도 없으면 귀속할 수
    없으므로 `None`이다(fail-closed). 재무가 숫자를 새로 만들지 않고 장부 금액만 더한다.
    """
    if books is None or not window or not impact_ids:
        return None
    start, end = window["from"], window["to"]
    total, count = 0, 0
    for r in books.refunds:
        if r.get("buyer_id") not in impact_ids:
            continue
        at = fc.iso(r["created_at"]) if r.get("created_at") else None
        if at is None or not (start <= at <= end):
            continue
        total += int(r.get("amount") or 0)
        count += 1
    return {"total": total, "count": count}


async def incident_view(
    context_id: str,
    feed: lg.ExecutionFeed,
    books: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """사건 상세 data와 unavailable 목록."""
    unavailable: list[str] = []
    info = await incident_info(context_id)  # 실제 모드 구간은 총괄 사건 목록에서 (읽기 전용)
    impact: Impact | None = await incident_impact(context_id, info=info)
    rows, ledger_source = await compensation_ledger(context_id, feed)
    # 근사 구간(AI 처리 시각)의 영향 고객 목록으로는 환불을 사건에 묶지 않는다
    # (앞부분 고객이 빠질 수 있다)
    exact = impact is not None and impact.window_exact
    impact_ids = impact.affected_customer_ids if impact and exact else frozenset()

    if impact is None:
        unavailable.append("impact")
    if rows is None:
        unavailable.append("compensation")

    coupons = [lg.public(r) for r in rows or []]
    totals = lg.totals(rows) if rows is not None else None

    window = None
    if info["window_from"] and info["window_to"]:
        window = {"from": fc.iso(info["window_from"]), "to": fc.iso(info["window_to"])}
    impact_out = None
    if impact is not None:
        impact_out = {
            "failed_orders": impact.failed_orders,
            "affected_customers": impact.affected_customers,
            "damage_amount": impact.lost_amount,
            "by_category": [
                {"category": c, "orders": n, "amount": a} for c, n, a in impact.by_category
            ],
            "affected_customer_ids": sorted(impact.affected_customer_ids),
            # False면 AI 처리 시각으로 대신한 근사 구간의 숫자
            # (데이터 AI 화면 숫자보다 적을 수 있다)
            "window_exact": impact.window_exact,
            "source": "data.impact_analysis",
            "collected_at": fc.iso(fc.now()),
        }
    refunds = incident_refunds(books, window, impact_ids)
    if refunds is None:
        unavailable.append("refunds")
    comp_total = (totals or {}).get("total_executed")
    money_out = None if refunds is None or comp_total is None else comp_total + refunds["total"]
    data = {
        "context_id": context_id,
        "component": info.get("component"),
        "window": window,
        "impact": impact_out,
        "compensation": {"coupons": coupons, **(totals or dict.fromkeys(_TOTAL_KEYS))},
        "refunds": refunds,
        # 재무가 보는 숫자: 보상 + 환불로 사건에서 빠져나간 돈 (둘 중 하나라도 없으면 null)
        "money_out_total": money_out,
        "ledger_source": ledger_source,
    }
    return data, unavailable


_TOTAL_KEYS = ("total_executed", "total_held", "count_executed", "count_held")


def known_incidents(feed: lg.ExecutionFeed) -> list[str]:
    """장부(이벤트)·안전 관문 실행 기록에 나온 사건 번호."""
    ledger = state().ledger
    ids = set(ledger.coupon_events) | set(ledger.coupons)
    ids |= {str(e.get("context_id")) for e in feed.rows or [] if e.get("context_id")}
    return sorted(i for i in ids if i.startswith(INCIDENT_PREFIX))


def overall_source(feed: lg.ExecutionFeed, used: list[str | None]) -> str | None:
    """목록 meta의 `ledger_source`: 실행 기록 출처 → 이벤트 → 둘 다 없으면 None."""
    if feed.rows is not None:
        return feed.source
    if lg.events_connected() or lg.LEDGER_EVENTS in used:
        return lg.LEDGER_EVENTS
    return None


def _opened_at(detail: dict[str, Any], feed: lg.ExecutionFeed) -> str | None:
    if detail["window"]:
        return detail["window"]["from"]
    times = [c["at"] for c in detail["compensation"]["coupons"] if c.get("at")]
    times += [
        fc.iso(e.get("created_at"))
        for e in feed.rows or []
        if e.get("context_id") == detail["context_id"] and e.get("created_at")
    ]
    times = [t for t in times if t]
    return min(times) if times else None


def summary_row(detail: dict[str, Any], feed: lg.ExecutionFeed) -> dict[str, Any]:
    comp = detail["compensation"]
    return {
        "context_id": detail["context_id"],
        "component": detail["component"],
        "opened_at": _opened_at(detail, feed),
        "damage_amount": (detail["impact"] or {}).get("damage_amount"),
        "compensation_total": comp["total_executed"],
        "coupon_count": comp["count_executed"],
        "held_count": comp["count_held"],
        "refund_total": (detail["refunds"] or {}).get("total"),
        "money_out_total": detail["money_out_total"],
    }


# ---------------------------------------------------------------- 라우트
def add_incident_api(app: FastAPI) -> None:
    @app.get("/api/finance/incidents")
    async def list_incidents() -> dict:
        feed = await state().executions_loader(None)
        books = await state().books()  # 환불 장부 (사건 구간·영향 고객으로 귀속)
        rows, used = [], []
        for ctx in known_incidents(feed):
            detail, _ = await incident_view(ctx, feed, books)
            used.append(detail["ledger_source"])
            rows.append(summary_row(detail, feed))
        rows.sort(key=lambda r: r["opened_at"] or "", reverse=True)
        meta: dict[str, Any] = {
            "mock": is_mock("finance"),
            "collected_at": fc.iso(fc.now()),
            "ledger_source": overall_source(feed, used),
            "unavailable": [] if feed.rows is not None else ["safety_executions"],
        }
        return {"data": rows, "meta": meta}

    @app.get("/api/finance/incidents/{context_id}", response_model=None)
    async def get_incident(context_id: str) -> dict | JSONResponse:
        feed = await state().executions_loader(context_id)
        if context_id not in known_incidents(feed):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "not_found",
                        "message": f"재무가 아는 사건이 아니다: {context_id}",
                    }
                },
            )
        books = await state().books()
        data, unavailable = await incident_view(context_id, feed, books)
        meta = {
            "mock": is_mock("finance"),
            "collected_at": fc.iso(fc.now()),
            "unavailable": unavailable,
        }
        return {"data": data, "meta": meta}

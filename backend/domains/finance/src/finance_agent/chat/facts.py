"""재무 대화에 넣는 사실. 재무 장부와 화면 계산을 읽기만 한다.

숫자는 여기서 코드가 모은다(오늘 손익, 월말 체크리스트, 통장 대조, 현금 예측, 정산 묶음,
캠페인 손익, 인사이트, 장애 이벤트 재무 기록). LLM은 이것을 풀어 쓸 뿐이다.
하나를 못 읽어도 나머지는 넣고, 못 읽은 이름은 `unavailable`에 남긴다
(모르는 숫자를 0으로 채우지 않는다).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

RECENT = 8
TEXT_CHARS = 120


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:TEXT_CHARS]


async def _overview() -> dict[str, Any]:
    from finance_agent.finance_api import overview_data

    data, meta = await overview_data()
    return {
        "date": data["date"],
        "today_kpis": data["kpis"],
        "todo": [{"title": _short(t["title"]), "amount": t.get("amount")} for t in data["todo"]],
        "unavailable": meta.get("unavailable", []),
    }


async def _checklist() -> dict[str, Any]:
    from finance_agent import close_checklist
    from finance_agent.books import off
    from finance_agent.handlers import state

    books = await state().books()
    out = await off(close_checklist.checklist, books, state().records)
    return {
        "month": out["month"],
        "items": [
            {"label": i["label"], "status": i["status"], "detail": _short(i["detail"])}
            for i in out["items"]
        ],
    }


async def _bank() -> dict[str, Any]:
    from finance_agent import bank
    from finance_agent.books import off
    from finance_agent.handlers import state

    books = await state().books()
    records = state().records
    deposits = await off(bank.match, books, records.bank_rows)
    outs = bank.outflows(
        records.bank_rows,
        records.bank_out_periods,
        records.batches,
        seller_name=books.seller_name,
        other_pnl=records.other_pnl,
        campaigns=records.campaigns,
        notes=records.bank_notes,
    )
    return {
        "deposits": {
            "uploaded": deposits["uploaded"],
            "covered": deposits["covered"],
            "counts": deposits["counts"],
            "exceptions": [
                {"date": d["date"], "status": d["status_label"], "diff": d["diff"]}
                for d in bank.exceptions(deposits)[:RECENT]
            ],
        },
        "withdrawals": {
            "transfers": [
                {"name": t["name"], "amount": t["amount"], "status": t["status_label"]}
                for t in outs["transfers"][:RECENT]
            ],
            "unexplained_open": [
                {"date": u["date"], "amount": u["amount"], "memo": _short(u["memo"])}
                for u in outs["unexplained"]
                if not u.get("note")
            ][:RECENT],
        },
        "latest_balance": bank.latest_balance(records.bank_rows),
    }


async def _cash() -> dict[str, Any]:
    from finance_agent import bank, cashflow
    from finance_agent.books import off
    from finance_agent.handlers import state

    books = await state().books()
    records = state().records
    out = await off(
        cashflow.forecast,
        books,
        records.batches,
        opening=bank.latest_balance(records.bank_rows),
        fixed=cashflow.fixed_costs(records.other_pnl),
    )
    return {
        "opening": out.get("opening"),
        "fixed": out.get("fixed"),
        "short_weeks": out.get("short_weeks"),
        "weeks": [
            {k: w.get(k) for k in ("week", "inflow", "outflow", "fixed", "net", "balance")}
            for w in out["weeks"][:4]
        ],
    }


async def _campaigns() -> dict[str, Any]:
    from finance_agent.handlers import current_insights, monthly_budget, state

    records = state().records
    budget = await monthly_budget()
    rows = sorted(records.campaigns.values(), key=lambda c: c["period_to"], reverse=True)
    return {
        "role": "재무는 캠페인 비용을 승인·반려하지 않고 의견만 낸다",
        "month_budget": {
            "amount_krw": budget["amount_krw"],
            "provisional": budget["provisional"],
            "basis": _short(budget["basis"]),
        },
        "results": [
            {
                "name": c["campaign_name"],
                "channel": c["channel"],
                "spend_krw": c["spend_krw"],
                "profit_krw": c.get("profit_krw"),
                "verdict": c.get("verdict"),
            }
            for c in rows[:RECENT]
        ],
        "open_lessons": [
            {"channel": x["channel"], "campaign": x["campaign_name"]}
            for x in records.lessons
            if x.get("status") == "open"
        ][:RECENT],
        "recent_opinions": [
            {
                "campaign": r["campaign_name"],
                "requested_krw": r["requested_krw"],
                "opinion": r["decision"],
                "suggested_krw": r["approved_limit_krw"],
            }
            for r in list(reversed(records.budget_reviews))[:RECENT]
        ],
        "insights": [
            {"title": _short(i["title"]), "recommendation": _short(i.get("recommendation"))}
            for i in (await current_insights())[:RECENT]
        ],
    }


async def _incidents() -> list[dict[str, Any]]:
    from finance_agent.handlers import state

    rows = sorted(
        state().ledger.incident_reports.values(), key=lambda r: r["recorded_at"], reverse=True
    )
    keys = (
        "context_id",
        "component",
        "damage_amount",
        "compensation_total",
        "refund_total",
        "money_out_total",
        "recorded_at",
    )
    return [{k: r.get(k) for k in keys} for r in rows[:RECENT]]


def _closings() -> dict[str, Any]:
    from finance_agent.handlers import state

    records = state().records
    confirmed = sorted(
        (f"{p}:{d}" for (p, d) in records.closings),
        reverse=True,
    )
    batches = {
        name: {"status": b.get("status"), "total_payout": b.get("total_payout")}
        for name, b in sorted(records.batches.items(), reverse=True)[:RECENT]
    }
    return {
        "confirmed_closings": confirmed[:RECENT],
        "other_pnl_months": sorted(records.other_pnl)[-RECENT:],
        "settlement_batches": batches,
    }


async def collect() -> dict[str, Any]:
    """재무 사실 묶음. 부분 실패는 `unavailable`에 이름만 남긴다(예외 내용은 넣지 않는다)."""
    parts: dict[str, Callable[[], Awaitable[Any]]] = {
        "overview": _overview,
        "month_close_checklist": _checklist,
        "bank": _bank,
        "cashflow": _cash,
        "campaigns": _campaigns,
        "incident_finance": _incidents,
    }
    out: dict[str, Any] = {"unavailable": []}
    for name, fn in parts.items():
        try:
            out[name] = await fn()
        except Exception:  # noqa: BLE001 - 하나를 못 읽어도 대화는 이어 간다
            logger.warning("재무 대화 사실 %s를 읽지 못했다", name, exc_info=True)
            out["unavailable"].append(name)
    try:
        out["records"] = _closings()
    except Exception:  # noqa: BLE001
        out["unavailable"].append("records")
    return out

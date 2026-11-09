"""재무 대화에 넣는 사실. 재무 장부와 화면 계산을 읽기만 한다.

숫자는 여기서 코드가 모은다(오늘 손익, 월말 체크리스트, 통장 대조, 현금 예측, 정산 묶음,
캠페인 손익, 인사이트, 장애 이벤트 재무 기록). LLM은 이것을 풀어 쓸 뿐이다.
하나를 못 읽어도 나머지는 넣고, 못 읽은 이름은 `unavailable`에 남긴다
(모르는 숫자를 0으로 채우지 않는다).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
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


async def _collect_now() -> dict[str, Any]:
    """재무 사실 묶음을 새로 만든다. 부분 실패는 `unavailable`에 이름만 남긴다(예외 내용은 빼고)."""
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


# ------------------------------------------------------- 기억해 두기 (2026-11-09 대화 느림)
# 사실 묶음을 만드는 데 화면 계산 6개가 들어간다. 로컬 주문 8.5만 건에서 장부를 이미 모은 뒤에도
# 12초 안팎이 걸려, 대화 한 번이 LLM까지 30초 가까이 걸렸다. 화면 응답 기억하기(`screen_cache.py`)와
# 같은 방식으로 기억해 둔다.
# - 같은 장부·같은 사람 기록이면 `FRESH_SECONDS` 안에는 그대로 쓴다
# - 장부만 새로 모였으면(기록은 그대로) `MAX_STALE_SECONDS` 안에는 바로 쓰고 뒤에서 다시 만든다.
#   언제 모은 장부인지는 overview의 숫자와 같다(장부도 최대 5분 묵은 것을 바로 준다)
# - 사람 기록(결산 확정·통장 파일·정산 묶음·장애 장부 등)이 바뀌었으면 기다려서 새로 만든다
# - 대화방을 열면(`warm`) 뒤에서 미리 만든다. 새 장부가 모이면 최근에 대화한 경우만 다시 만든다
# - 목 모드(시드·시험)는 매번 새로 만든다
FRESH_SECONDS = 60.0
MAX_STALE_SECONDS = 600.0
WARM_WINDOW_SECONDS = 600.0


class _FactsCache:
    def __init__(self) -> None:
        self.value: dict[str, Any] | None = None
        self.books_id: int | None = None
        self.records_key: str | None = None
        self.made = 0.0
        self.used = float("-inf")
        self.task: asyncio.Task[dict[str, Any]] | None = None

    def clear(self) -> None:
        self.__init__()


CACHE = _FactsCache()


def enabled() -> bool:
    from ax_agent_template.config import is_mock

    return not is_mock("finance")


def _books_id() -> int | None:
    from finance_agent import books as bk

    cached = bk._live_cache
    return id(cached[1]) if cached else None


def _records_key() -> str:
    """사람 기록 전체의 지문. 파일에 남기는 내용과 같은 것(`persist._text`)의 해시다."""
    from finance_agent import persist
    from finance_agent.handlers import state

    return hashlib.sha1(persist._text(state()).encode("utf-8")).hexdigest()


def _start() -> asyncio.Task[dict[str, Any]]:
    """만드는 중이 아니면 새로 만들기 시작한다. 같은 묶음을 동시에 두 번 만들지 않는다."""
    if CACHE.task is None or CACHE.task.done():
        records_key = _records_key()

        async def run() -> dict[str, Any]:
            value = await _collect_now()
            CACHE.value, CACHE.books_id = value, _books_id()
            CACHE.records_key, CACHE.made = records_key, time.monotonic()
            return value

        CACHE.task = asyncio.get_running_loop().create_task(run())
        CACHE.task.add_done_callback(_log_failure)
    return CACHE.task


async def _build() -> dict[str, Any]:
    """만드는 중이면 그 결과를 기다린다."""
    return await asyncio.shield(_start())


def _refresh_later() -> None:
    _start()


def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("재무 대화 사실을 미리 만들지 못했다: %s", type(task.exception()).__name__)


async def collect() -> dict[str, Any]:
    """재무 사실 묶음. 실제 모드에서는 기억해 둔 것을 쓴다(위 설명)."""
    if not enabled():
        return await _collect_now()
    CACHE.used = time.monotonic()
    value = CACHE.value
    if value is not None and CACHE.records_key == _records_key():
        age = time.monotonic() - CACHE.made
        if CACHE.books_id == _books_id() and age < FRESH_SECONDS:
            return value
        if age < MAX_STALE_SECONDS:
            _refresh_later()
            return value
    return await _build()


def warm() -> None:
    """대화방을 열었다(목록·대화 읽기). 기억한 묶음이 없거나 낡았으면 뒤에서 미리 만든다."""
    if not enabled():
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    CACHE.used = time.monotonic()
    fresh = (
        CACHE.value is not None
        and CACHE.books_id == _books_id()
        and time.monotonic() - CACHE.made < FRESH_SECONDS
    )
    if not fresh:
        _refresh_later()


def on_new_books() -> None:
    """새 장부가 모였다. 최근에 대화했으면 뒤에서 다시 만든다."""
    if enabled() and time.monotonic() - CACHE.used < WARM_WINDOW_SECONDS:
        _refresh_later()


def install() -> None:
    from finance_agent import books as bk

    if on_new_books not in bk.ON_NEW_BOOKS:
        bk.ON_NEW_BOOKS.append(on_new_books)

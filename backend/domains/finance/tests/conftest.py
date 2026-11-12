"""재무 재설계 모듈 테스트 공용: 날짜 고정, 작은 장부 만들기."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from finance_agent import finance_config as fc
from finance_agent import settlement_feed
from finance_agent.books import Books

# 테스트에서는 재무 AI 자동 테스트(백그라운드 루프)를 끈다. 켜 보는 테스트는 monkeypatch로 직접 켠다
os.environ["FINANCE_SELF_TEST_SECONDS"] = "0"
# 매일 결산 자동 실행도 끈다 (켜 보는 테스트는 monkeypatch로 직접 켠다)
os.environ["FINANCE_DAILY_CLOSING_SECONDS"] = "0"

FIXED_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)  # 금요일. 지난주 = 2026-W42 (10-12~10-18)


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(fc, "now", lambda: FIXED_NOW)
    return FIXED_NOW


def at(day: str, hhmm: str = "10:00") -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(hours=hour, minutes=minute)


def order(
    order_id: str,
    when: datetime,
    items: list[tuple[str, str, int, int]],
    *,
    buyer: str = "buy_0001",
    shipping: int = 0,
    delivered: bool = True,
    payment_status: str = "succeeded",
) -> dict[str, Any]:
    """items: (seller_id, category_code, qty, unit_price)."""
    rows = [
        {
            "product_id": f"prd_{n}",
            "seller_id": seller,
            "category_code": cat,
            "qty": qty,
            "unit_price": price,
            "points_share": 0,
            "paid_amount": qty * price,
        }
        for n, (seller, cat, qty, price) in enumerate(items)
    ]
    total = sum(r["paid_amount"] for r in rows) + shipping
    return {
        "order_id": order_id,
        "buyer_id": buyer,
        "status": "paid",
        "total_amount": total,
        "refunded_amount": 0,
        "shipping_fee": shipping,
        "points_used": 0,
        "created_at": when,
        "delivered_at": when + timedelta(days=1) if delivered else None,
        "items": rows,
        "payments": [
            {
                "payment_id": order_id.replace("ord_", "pay_"),
                "amount": total,
                "status": payment_status,
                "created_at": when,
            }
        ],
    }


def books(**kw: Any) -> Books:
    base: dict[str, Any] = {
        "sellers": [
            {"seller_id": "sel_a", "name": "가게A"},
            {"seller_id": "sel_b", "name": "가게B"},
        ],
        "executions": [],
        "mock": True,
        "source": "seed",
    }
    return Books(**(base | kw))


@pytest.fixture
def kit() -> SimpleNamespace:
    """테스트 파일에서 쓰는 도우미 묶음 (importlib 모드라 conftest를 import하지 않는다)."""
    return SimpleNamespace(at=at, order=order, books=books, now=FIXED_NOW, statement=statement)


# ---------------------------------------------------------------- 가짜 고객지원 (판매자 정산)
# 판매자 정산은 고객지원이 계산한다. 재무 테스트는 네트워크 없이 고객지원 응답을 흉내 낸다
# (`settlement_feed.set_caller`). 기본은 "고객지원이 꺼짐"이고, 받을 자료가 필요한 테스트는
# `support` fixture로 내역서·묶음·기한을 넣는다.
PERIOD = re.compile(r"^\d{4}-W\d{2}$")
SUPPORT_META = {
    "mock": True,
    "collected_at": "2026-10-23T12:00:00Z",
    "source": "shop-api",
    "unavailable": [],
}


def _result(status: TaskStatus, output: dict[str, Any], outcome: str | None = None) -> AgentResult:
    meta = RequestMeta(
        context_id="ctx_test_support", token="t", deadline=FIXED_NOW + timedelta(days=3650)
    )
    return AgentResult(meta=meta, status=status, output=output, outcome=outcome)


def statement(period: str, rows: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    """고객지원 `seller_settlement` output 모양(`meta` 뺀 것). `rows`는 판매자별 내역서."""
    year, week = period.split("-W")
    monday = date.fromisocalendar(int(year), int(week), 1)
    rows = [
        {
            "seller_id": r["seller_id"],
            "name": r.get("name", r["seller_id"]),
            "orders": r.get("orders", 1),
            "gross": r.get("gross", r.get("payout", 0)),
            "commission": r.get("commission", 0),
            "commission_rate": 0.1,
            "seller_coupon": 0,
            "refunds": r.get("refunds", 0),
            "payout": r.get("payout", 0),
            "hold_reasons": r.get("hold_reasons", []),
            "status": r.get("status", "ready"),
            "review_id": None,
            "pending_confirmation": {"orders": 0, "gross": 0},
            "products": [],
        }
        for r in rows or []
    ]
    base = {
        "period": period,
        "from": f"{monday.isoformat()}T00:00:00Z",
        "to": f"{(monday + timedelta(days=7)).isoformat()}T00:00:00Z",
        "pay_date": (monday + timedelta(days=12)).isoformat(),
        "statements": rows,
        "applied_rules": [{"id": "SET-001"}],
        "batch": {
            "status": "none",
            "review_id": None,
            "execution_id": None,
            "total_payout": 0,
            "seller_ids": [],
        },
    }
    return base | kw


class FakeSupport:
    """고객지원 claims 서비스 흉내. `weeks`·`batches`·`deadlines`를 테스트가 채운다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.down = False
        self.last_week = "2026-W42"  # FIXED_NOW 기준 지난주
        self.weeks: dict[str, dict[str, Any]] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.deadlines: dict[str, Any] = {
            "today": "2026-10-23",
            "deadline_days": 20,
            "basis": "구매 확정일부터",
            "warn_days": 5,
            "since": "2026-08-14",
            "counts": {"overdue": 0, "at_risk": 0, "due_soon": 0, "ok": 0},
            "waiting_orders": 0,
            "sellers": [],
        }

    def week(self, period: str) -> dict[str, Any]:
        return self.weeks.get(period) or statement(period)

    async def __call__(self, skill: str, payload: dict[str, Any]) -> AgentResult | None:
        self.calls.append((skill, payload))
        if self.down:
            return _result(TaskStatus.FAILED, {}, "unreachable")
        if skill == "seller_settlement":
            period = payload.get("period") or self.last_week
            if not PERIOD.match(period):
                return _result(TaskStatus.FAILED, {}, "invalid_input")
            return _result(TaskStatus.COMPLETED, self.week(period) | {"meta": SUPPORT_META})
        if skill == "seller_settlement_weeks":
            weeks = {p: self.week(p) for p in payload["periods"]}
            return _result(TaskStatus.COMPLETED, {"weeks": weeks, "meta": SUPPORT_META})
        if skill == "seller_settlement_batches":
            return _result(TaskStatus.COMPLETED, {"batches": self.batches})
        if skill == "seller_settlement_deadlines":
            return _result(TaskStatus.COMPLETED, self.deadlines | {"meta": SUPPORT_META})
        return _result(TaskStatus.FAILED, {}, "unknown_skill")


async def _support_down(skill: str, payload: dict[str, Any]) -> AgentResult | None:
    return None  # 고객지원이 정산 skill을 맡지 않았거나 꺼짐


@pytest.fixture(autouse=True)
def _no_support() -> Iterator[None]:
    settlement_feed.set_caller(_support_down)
    yield
    settlement_feed.set_caller(None)


@pytest.fixture
def support() -> FakeSupport:
    fake = FakeSupport()
    settlement_feed.set_caller(fake)
    return fake

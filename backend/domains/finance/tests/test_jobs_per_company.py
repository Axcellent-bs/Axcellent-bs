"""매일 결산·월 마감 기한 알림을 회사마다 (회사 분리 11/26). 네트워크 없음.

요청 밖에서 도는 두 루프가 캣북스 장부만 봐서 누비투어는 "매일 결산"이 비어 있고 마감 알림도 없었다.
이제 회차마다 회사(캣북스 + 회사 연결이 켜진 회사)를 돌며 그 회사 장부·기록으로 계산하고 남긴다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from ax_agent_template import companies, company_connection, company_scope, events
from finance_agent import handlers, main
from finance_agent.sources import MockSources

NUBI = "c-aaaaaa"
YESTERDAY = date(2026, 10, 22)  # conftest의 FIXED_NOW(2026-10-23) 전날


@dataclass
class _Conn:
    enabled: bool = True


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch, fixed_now):  # noqa: ANN001, ANN201
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())
    monkeypatch.setattr(companies, "company_ids", lambda: ["catbooks", NUBI])
    monkeypatch.setattr(
        company_connection, "read_connection", lambda cid=None: _Conn() if cid == NUBI else None
    )
    yield
    main.DAILY_CLOSING_SCHEDULE.update(next_run_at=None)  # 루프가 남긴 다음 실행 시각을 지운다


async def _books(kit, orders: list[dict[str, Any]]):  # noqa: ANN001, ANN202
    return kit.books(orders=orders, refunds=[], coupons=[])


async def _one_round(loop, monkeypatch, name: str) -> list[str | None]:  # noqa: ANN001
    """루프를 한 회차만 돌리고, 그 회차에 일을 한 회사(요청 회사)를 돌려준다."""
    seen: list[str | None] = []
    original = getattr(main, name)
    done = asyncio.Event()

    async def spy(*args: Any) -> Any:
        seen.append(company_scope.active())
        try:
            return await original(*args)
        finally:
            if len(seen) >= 2:
                done.set()

    monkeypatch.setattr(main, name, spy)
    task = asyncio.create_task(loop(3600, first_delay=0))
    await asyncio.wait_for(done.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return seen


@pytest.mark.asyncio
async def test_daily_closing_runs_per_company_into_its_own_record(kit, monkeypatch) -> None:  # noqa: ANN001
    cat_orders = [kit.order("ord_c", kit.at("2026-10-22"), [("sel_a", "books", 2, 10000)])]
    nubi_orders = [kit.order("ord_n", kit.at("2026-10-22"), [("sel_n", "tour", 1, 300000)])]
    handlers.state().books_loader = lambda: _books(kit, cat_orders)
    with company_scope.using(NUBI):
        handlers.state().books_loader = lambda: _books(kit, nubi_orders)

    seen = await _one_round(main.daily_closing_loop, monkeypatch, "run_and_store_daily_closing")

    assert seen == ["catbooks", NUBI]  # 캣북스 차례도 캣북스 범위(11/27), 누비투어는 그 회사 범위
    cat = handlers.state().last_daily_closing
    with company_scope.using(NUBI):
        nubi = handlers.state().last_daily_closing

    def gmv(stored: dict[str, Any]) -> int:
        return next(x["amount"] for x in stored["report"]["lines"] if x["code"] == "gmv")

    assert cat["date"] == nubi["date"] == YESTERDAY.isoformat()
    assert gmv(cat) == 20000 and gmv(nubi) == 300000  # 회사마다 자기 장부, 섞이지 않음


@pytest.mark.asyncio
async def test_close_alerts_run_per_company(monkeypatch) -> None:  # noqa: ANN001
    async def none() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(main, "run_close_alerts", none)
    seen = await _one_round(main.close_alert_loop, monkeypatch, "run_close_alerts")
    assert seen == ["catbooks", NUBI]


@pytest.mark.asyncio
async def test_one_company_failing_does_not_stop_the_next(kit, monkeypatch) -> None:  # noqa: ANN001
    orders = [kit.order("ord_n", kit.at("2026-10-22"), [("sel_n", "tour", 1, 1000)])]
    with company_scope.using(NUBI):
        handlers.state().books_loader = lambda: _books(kit, orders)

    async def broken() -> Any:
        raise RuntimeError("캣북스 장부 고장")

    handlers.state().books_loader = broken
    seen = await _one_round(main.daily_closing_loop, monkeypatch, "run_and_store_daily_closing")
    assert seen == ["catbooks", NUBI]
    with company_scope.using(NUBI):
        assert handlers.state().last_daily_closing["date"] == YESTERDAY.isoformat()

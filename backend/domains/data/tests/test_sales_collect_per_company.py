"""판매 기록 수집을 연결된 회사마다 (11/26 스터디온·누비투어 매출 분석이 비던 것).

수집기는 캣북스(`DATA_SALES_TENANT_ID`) 하나만 받아 다른 회사 판매 기록이 아예 쌓이지 않았다.
이제 회사 연결이 켜진 회사마다 그 회사 범위에서 받아 `tenant_id=회사 id`로 쌓는다.

- 캣북스 줄·숫자는 그대로
- 회사끼리 섞이지 않음 (누비투어 tenant에 캣북스 주문 없음)
- 한 회사 출처 오류여도 다른 회사 수집은 진행
- 목·가짜 출처(시험·데모)는 캣북스 하나만
- 메모리 모드도 같은 회사 목록
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection, company_scope
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent import sales_collect as sc
from data_agent import sales_memory
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore

NUBI = "c-aaaaaa"
STUDY = "c-bbbbbb"
NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)
ENV: dict[str, str] = {}  # 목·가짜 아님 (회사 목록을 돈다), 하루 집계 꺼짐


@dataclass
class _Conn:
    enabled: bool = True


def _orders(prefix: str, n: int, price: int) -> list[SalesOrderRow]:
    out = []
    for i in range(n):
        when = NOW - timedelta(days=1, hours=i)
        line = SalesOrderLine(product_id=f"{prefix}-p{i % 3}", category_code="x", title="t", qty=1,
                              unit_price=price, coupon_share=0, points_share=0)  # fmt: skip
        out.append(SalesOrderRow(
            order_id=f"{prefix}-o{i}", buyer_id=f"{prefix}-b{i % 4}", status="paid",
            created_at=when, updated_at=when, items_amount=price, payment_status="succeeded",
            lines=[line],
        ))  # fmt: skip
    return out


ROWS = {NUBI: _orders("nubi", 5, 10000), STUDY: _orders("study", 3, 30000)}


def _factory(fail: str | None = None):  # noqa: ANN202
    def make(env):  # noqa: ANN001, ANN202
        cid = company_scope.scope_id(company_scope.active())
        if cid == fail:
            raise RuntimeError("출처 고장")
        return sc.MockSalesSource(ROWS.get(cid, []))

    return make


@pytest.fixture(autouse=True)
def _companies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(company_list, "company_ids", lambda: ["catbooks", NUBI, STUDY, "c-cccccc"])
    conns = {NUBI: _Conn(), STUDY: _Conn(), "c-cccccc": _Conn(enabled=False)}
    monkeypatch.setattr(company_connection, "read_connection", lambda cid=None: conns.get(cid))


def _window() -> tuple[datetime, datetime]:
    return NOW - timedelta(days=7), NOW


def _catbooks_store() -> SalesStore:
    store = SalesStore(":memory:")
    src = sc.MockSalesSource(_orders("cat", 6, 20000))
    got = asyncio.run(
        sc.collect_once(src, store, tenant_id=TENANT_ID_DEFAULT, source_name="shop-api", now=NOW)
    )
    assert got.errors == []
    return store


def test_watched_companies_are_connected_others_only() -> None:
    assert sc.watched_companies(ENV) == [NUBI, STUDY]  # 캣북스·연결 꺼진 회사는 빠짐
    assert sc.watched_companies({"DATA_MOCK": "1"}) == []
    assert sc.watched_companies({"DATA_SALES_SOURCE": "fake"}) == []


def test_each_company_is_stored_under_its_own_tenant_and_catbooks_is_unchanged() -> None:
    store = _catbooks_store()
    before, _ = tally(store, TENANT_ID_DEFAULT, *_window())
    results = asyncio.run(sc.collect_other_companies(store, ENV, NOW, {}, _factory()))
    assert set(results) == {NUBI, STUDY} and all(not r.errors for r in results.values())
    after, _ = tally(store, TENANT_ID_DEFAULT, *_window())
    assert (after.revenue, after.orders) == (before.revenue, before.orders) == (120000, 6)
    nubi, _ = tally(store, NUBI, *_window())
    study, _ = tally(store, STUDY, *_window())
    assert (nubi.revenue, nubi.orders) == (50000, 5)
    assert (study.revenue, study.orders) == (90000, 3)
    with store.reading() as conn:
        owners = conn.execute(
            "SELECT tenant_id, substr(order_id, 1, instr(order_id, '-') - 1) AS p, COUNT(*) "
            "FROM sales_orders GROUP BY tenant_id, p"
        ).fetchall()
    assert {(r[0], r[1]) for r in owners} == {
        (TENANT_ID_DEFAULT, "cat"), (NUBI, "nubi"), (STUDY, "study")}  # 섞이지 않음  # fmt: skip


def test_one_company_failing_does_not_stop_the_others() -> None:
    store = SalesStore(":memory:")
    results = asyncio.run(sc.collect_other_companies(store, ENV, NOW, {}, _factory(fail=NUBI)))
    assert results[NUBI].errors and not results[STUDY].errors
    assert tally(store, STUDY, *_window())[0].orders == 3
    assert tally(store, NUBI, *_window())[0].orders == 0


def test_request_tenant_follows_the_request_company(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sc.TENANT_ENV, raising=False)
    assert sc.request_tenant({}) == TENANT_ID_DEFAULT
    with company_scope.using(STUDY):
        assert sc.request_tenant({}) == STUDY


def test_memory_mode_collects_other_companies_too() -> None:
    swapped: list[SalesStore] = []

    def factory(env):  # noqa: ANN001, ANN202
        cid = company_scope.scope_id(company_scope.active())
        if cid == company_scope.CATBOOKS:
            return sc.MockSalesSource(_orders("cat", 2, 20000))
        return sc.MockSalesSource(ROWS.get(cid, []))

    asyncio.run(sales_memory.run_forever(ENV, swapped.append, 60, source_factory=factory,
                                         clock=lambda: NOW, rounds=1))  # fmt: skip
    assert len(swapped) == 1
    store = swapped[0]
    assert tally(store, TENANT_ID_DEFAULT, *_window())[0].orders == 2
    assert tally(store, NUBI, *_window())[0].orders == 5
    assert tally(store, STUDY, *_window())[0].orders == 3


def test_recollect_clears_only_that_company_cursor_and_refills_lines(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """`--recollect 회사 id` (11/26): 그 회사 커서만 비워 다음 회차에 처음부터 다시 받는다.

    이름·분류 없이 쌓인 줄이 다시 받은 값(변환기가 상품 목록으로 채운 값)으로 덮인다.
    지우는 기록 없음.
    """
    env = {sc.DB_ENV: str(tmp_path / "sales.db")}
    bare = [r.model_copy(update={"lines": [ln.model_copy(update={"title": None,
            "category_code": None}) for ln in r.lines]}) for r in ROWS[STUDY]]  # fmt: skip
    store = SalesStore(sc.db_path(env))
    try:
        cat = sc.MockSalesSource(_orders("cat", 2, 20000))
        asyncio.run(sc.collect_once(cat, store, tenant_id=TENANT_ID_DEFAULT,
                                    source_name="shop-api", now=NOW))  # fmt: skip
        asyncio.run(sc.collect_company(store, STUDY, env, NOW, {},
                                       lambda e: sc.MockSalesSource(bare)))  # fmt: skip
        cat_cursor = store.sync_state(TENANT_ID_DEFAULT, "shop-api").cursor
        assert store.sync_state(STUDY).cursor
    finally:
        store.close()

    assert sc.recollect(env, "catbooks") == 2
    assert sc.recollect(env, STUDY) == 0

    store = SalesStore(sc.db_path(env))
    try:
        assert store.sync_state(TENANT_ID_DEFAULT, "shop-api").cursor == cat_cursor  # 캣북스 그대로
        assert not store.sync_state(STUDY).cursor
        asyncio.run(sc.collect_company(store, STUDY, env, NOW, {}, _factory()))
        with store.reading() as conn:
            got = conn.execute(
                "SELECT COUNT(*), SUM(title IS NULL), SUM(category_code IS NULL) "
                "FROM sales_order_lines WHERE tenant_id = ?",
                (STUDY,),
            ).fetchone()
        assert tuple(got) == (3, 0, 0)  # 줄 수 그대로, 이름·분류 채워짐
        assert tally(store, STUDY, *_window())[0].orders == 3
    finally:
        store.close()

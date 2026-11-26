"""데이터 주기 작업·메모리 모드·데이터 출처 화면을 회사마다 (11/26 회사 분리, 점검표 묶음 4).

캣북스 하나만 돌던 것:
- 업무 리포트 일간 저장 → 회사 연결이 켜진 회사마다 그 회사 범위·그 회사 id로 저장
- 판매 분석 미리 계산·구매자 요약 표 → 회사마다 그 회사 카탈로그로
- 메모리 모드 → 캣북스 처음 받기가 실패해도 다른 회사는 받음
- 데이터 출처 화면 → 요청 회사의 판매 기록 수집 상태 (다른 회사 상태가 보이지 않음)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection, company_scope
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent import monitor, sales_memory, sales_reads
from data_agent import sales_collect as sc
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore
from data_agent.skills import data_sources as ds

NUBI = "c-aaaaaa"
STUDY = "c-bbbbbb"
NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)
ENV: dict[str, str] = {}


@dataclass
class _Conn:
    enabled: bool = True


@pytest.fixture(autouse=True)
def _companies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(company_list, "company_ids", lambda: ["catbooks", NUBI, STUDY])
    conns = {NUBI: _Conn(), STUDY: _Conn()}
    monkeypatch.setattr(company_connection, "read_connection", lambda cid=None: conns.get(cid))
    monkeypatch.delenv(sc.TENANT_ENV, raising=False)


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


def _window() -> tuple[datetime, datetime]:
    return NOW - timedelta(days=7), NOW


# ---------------------------------------------------------------- 업무 리포트 일간 저장


def test_report_snapshots_run_per_company_in_its_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str | None, str | None]] = []

    def build_for(tenant: str, company: str | None):  # noqa: ANN202
        async def build_day(day: date) -> None:
            return None

        seen.append((tenant, company, company_scope.active()))
        return build_day

    async def tick(store, build_day, tenant, now, backfill):  # noqa: ANN001, ANN202, ARG001
        if tenant == NUBI:
            raise RuntimeError("누비투어 고장")  # 한 회사 오류가 다른 회사를 막지 않는다
        return []

    monkeypatch.setattr(monitor, "snapshot_tick", tick)
    asyncio.run(monitor.run_company_snapshots_forever(
        object(), build_for, TENANT_ID_DEFAULT, lambda: sc.watched_companies(ENV),
        clock=lambda: NOW, rounds=1,
    ))  # fmt: skip
    assert [(t, c) for t, c, _ in seen] == [(TENANT_ID_DEFAULT, None), (NUBI, NUBI), (STUDY, STUDY)]
    # 캣북스는 예전처럼 범위 없이, 다른 회사는 그 회사 범위 안에서 만든다
    assert seen[0][2] is None
    assert company_scope.scope_id(seen[1][2]) == NUBI
    assert company_scope.scope_id(seen[2][2]) == STUDY


# ---------------------------------------------------------------- 판매 분석 미리 계산


@dataclass
class _Store:
    versions: dict[str, int]

    def ensure_summary(self, tenant_id: str) -> None:
        return None

    def data_version(self, tenant_id: str) -> int:
        return self.versions.get(tenant_id, 0)


def test_warm_runs_per_company_with_its_own_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store({TENANT_ID_DEFAULT: 3, NUBI: 2, STUDY: 1})
    warmed: list[tuple[str, str]] = []

    def fake_warm(s, tenant_id, cat, now):  # noqa: ANN001, ANN202, ARG001
        if tenant_id == NUBI:
            raise RuntimeError("누비투어 고장")
        warmed.append((tenant_id, cat))
        return ["sales_analysis 7d"]

    def catalog() -> str:  # 그 회사 범위의 카탈로그
        return f"cat:{company_scope.scope_id(company_scope.active())}"

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(sales_reads, "warm", fake_warm)
    monkeypatch.setattr(sales_reads.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sales_reads.run_forever(
            lambda: store, catalog, TENANT_ID_DEFAULT,
            others=lambda: sc.watched_companies(ENV),
        ))  # fmt: skip
    assert warmed == [
        (TENANT_ID_DEFAULT, f"cat:{company_scope.CATBOOKS}"),  # 캣북스 그대로
        (STUDY, f"cat:{STUDY}"),  # 누비투어가 실패해도 스터디온은 그 회사 카탈로그로
    ]


def test_warm_without_others_is_catbooks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store({TENANT_ID_DEFAULT: 1, NUBI: 1})
    warmed: list[str] = []
    monkeypatch.setattr(sales_reads, "warm",
                        lambda s, t, c, n: warmed.append(t) or [])  # noqa: ARG005  # fmt: skip

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(sales_reads.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sales_reads.run_forever(lambda: store, lambda: None, TENANT_ID_DEFAULT))
    assert warmed == [TENANT_ID_DEFAULT]


# ---------------------------------------------------------------- 메모리 모드


def test_memory_mode_other_companies_do_not_wait_for_catbooks() -> None:
    """캣북스 처음 받기가 실패해도 다른 회사는 받고, 캣북스는 다음 주기에 같은 저장소로 받는다."""
    swapped: list[SalesStore] = []
    calls = {"cat": 0}

    def factory(env):  # noqa: ANN001, ANN202
        cid = company_scope.scope_id(company_scope.active())
        if cid == company_scope.CATBOOKS:
            calls["cat"] += 1
            if calls["cat"] == 1:
                raise RuntimeError("캣북스 출처 고장")
            return sc.MockSalesSource(_orders("cat", 2, 20000))
        return sc.MockSalesSource(ROWS.get(cid, []))

    asyncio.run(sales_memory.run_forever(ENV, swapped.append, 60, source_factory=factory,
                                         clock=lambda: NOW, rounds=1))  # fmt: skip
    assert len(swapped) == 1  # 빈 메모리 저장소를 먼저 끼움
    store = swapped[0]
    assert tally(store, NUBI, *_window())[0].orders == 5
    assert tally(store, STUDY, *_window())[0].orders == 3
    assert tally(store, TENANT_ID_DEFAULT, *_window())[0].orders == 0

    swapped.clear()
    calls["cat"] = 1  # 다음 실행: 캣북스 출처가 살아남 → 처음 받기 성공, 한 번만 끼움
    asyncio.run(sales_memory.run_forever(ENV, swapped.append, 60, source_factory=factory,
                                         clock=lambda: NOW, rounds=1))  # fmt: skip
    assert len(swapped) == 1
    assert tally(swapped[0], TENANT_ID_DEFAULT, *_window())[0].orders == 2


def test_load_full_into_existing_store_keeps_other_companies() -> None:
    store = SalesStore(":memory:")
    asyncio.run(sc.collect_once(sc.MockSalesSource(ROWS[NUBI]), store, tenant_id=NUBI,
                                source_name="shop-api", now=NOW))  # fmt: skip
    cat = sc.MockSalesSource(_orders("cat", 2, 20000))
    got = asyncio.run(sales_memory.load_full(cat, TENANT_ID_DEFAULT, "shop-api", NOW, into=store))
    assert got is store
    assert tally(store, NUBI, *_window())[0].orders == 5
    assert tally(store, TENANT_ID_DEFAULT, *_window())[0].orders == 2


# ---------------------------------------------------------------- 데이터 출처 화면


def _filled_store() -> SalesStore:
    store = SalesStore(":memory:")
    asyncio.run(sc.collect_once(sc.MockSalesSource(_orders("cat", 2, 20000)), store,
                                tenant_id=TENANT_ID_DEFAULT, source_name="shop-api",
                                now=NOW))  # fmt: skip
    return store


def test_sales_row_shows_the_request_company_only() -> None:
    store = _filled_store()
    cat = ds.sales_state(store.sync_state(TENANT_ID_DEFAULT), ENV, NOW + timedelta(minutes=5))
    assert cat.collector_id == "sales_collect" and not cat.stalled
    with company_scope.using(NUBI):
        tenant = sc.request_tenant(ENV)
    assert tenant == NUBI
    nubi = ds.sales_state(store.sync_state(tenant), ENV, NOW)
    # 누비투어 화면에 캣북스 수집 시각이 보이지 않는다
    assert nubi.stalled and nubi.last_collected_at is None
    assert nubi.warning == "수집 멈춤: 판매 기록을 아직 끝까지 받은 적이 없음"


def test_data_sources_handler_uses_request_company(monkeypatch: pytest.MonkeyPatch) -> None:
    from data_agent import handlers

    store = _filled_store()
    handlers.use_sales_store(store)
    got: dict[str, object] = {}

    async def fake_list(env, *, recheck, tenant_id, sales_sync):  # noqa: ANN001, ANN202, ARG001
        got["tenant"] = tenant_id
        got["sync"] = sales_sync
        raise RuntimeError("여기까지만")

    monkeypatch.setattr(ds, "list_sources", fake_list)

    class _Meta:
        tenant_id = NUBI

    class _Req:
        meta = _Meta()

    monkeypatch.setattr(handlers, "_parse", lambda request, model: model())
    try:
        with company_scope.using(NUBI), pytest.raises(RuntimeError):
            asyncio.run(handlers.data_sources(_Req()))  # type: ignore[arg-type]
        assert got["tenant"] == NUBI
        assert got["sync"].last_synced_at is None  # 캣북스 sync가 아님
        with pytest.raises(RuntimeError):
            asyncio.run(handlers.data_sources(_Req()))  # type: ignore[arg-type]
        assert got["tenant"] == TENANT_ID_DEFAULT  # 캣북스 그대로
        assert got["sync"].last_synced_at is not None
    finally:
        handlers.use_sales_store(None)
        store.close()

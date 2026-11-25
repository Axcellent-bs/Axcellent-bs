"""고객 분석 집계 읽기 (3-2 ③~, 설계 2.2·2.4·10절 결정 1 = A안 "어제 기준").

- `DATA_SALES_READ=aggregate`이고 어제(한국) 기준 스냅샷이 있으면 고객 분석은 스냅샷을 읽는다.
  기간 끝은 어제 24시, 근거 기간도 그 기간, 요약 끝에 "(11월 15일 기준)"
- 숫자는 같은 기간 원본 세션 숫자로 화면 함수가 낸 것과 같다
- 스위치 꺼짐·그 기준일 스냅샷 없음·다른 회사·집계 파일을 못 읽음은 원본 경로 그대로
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from data_agent import catalog
from data_agent.aggregate import daily, read
from data_agent.aggregate import read_buyers as rb
from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.store import DB_ENV, AggregateStore
from data_agent.sales_cache import ResultCache, cached
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import SalesStore
from data_agent.skills import basket_pairs as bp
from data_agent.skills import channel_value as cv
from data_agent.skills import event_proposal as ep
from data_agent.skills import retention as rt
from data_agent.skills import segment_analysis as seg
from data_agent.skills.retention import HISTORY_FROM

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00 → 기준일 11/15
AS_OF = date(2026, 11, 15)


def filled() -> SalesStore:
    store = SalesStore(":memory:")
    # 90일 창 경계는 남기고 하루 주문 수만 줄인다 (60 → 20, 11/25 CI 시간 줄이기)
    cfg = load_config(FAKE_DIR / "catbooks.toml").model_copy(update={"orders_per_day": 20})
    src = FakeSalesSource(cfg, clock=lambda: NOW)
    assert asyncio.run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.upsert(TENANT, old_customers(), NOW)
    return store


def old_customers() -> list[SalesOrderRow]:
    """첫 구매가 200일 전인 새 고객(네이버·모르는 출처·직접 6명씩, 짝수는 재구매).
    가치 기간이 다 지났다."""
    first = NOW - timedelta(days=200)
    out = []
    for i in range(18):
        src = "naver" if i < 6 else ("ghost" if i < 12 else None)
        out.append(_order(f"f{i}", f"c{i}", first, src))
        if i % 2 == 0:
            out.append(_order(f"s{i}", f"c{i}", first + timedelta(days=10), None))
    return out


def _order(n: str, buyer: str, when: datetime, src: str | None) -> SalesOrderRow:
    ln = SalesOrderLine(product_id="p1", category_code="book", title="책", qty=1, unit_price=10000,
                        coupon_share=0, points_share=0)  # fmt: skip
    return SalesOrderRow.model_validate({
        "order_id": n, "buyer_id": buyer, "status": "paid", "created_at": when, "updated_at": when,
        "items_amount": 10000, "payment_status": "succeeded", "lines": [ln], "src_source": src,
    })  # fmt: skip


@pytest.fixture
def cat():  # noqa: ANN201
    return catalog.load_catalog("catbooks")


@pytest.fixture(scope="module")
def _built(tmp_path_factory: pytest.TempPathFactory) -> tuple[SalesStore, Path]:
    """판매 원본과 하루 계산을 파일에서 한 번만 만든다 (시험마다 다시 하면 4초씩, 11/25 CI 시간).
    판매 원본은 시험이 바꾸지 않고, 집계 파일은 시험마다 복사본을 쓴다."""
    path = tmp_path_factory.mktemp("agg") / "aggregates.db"
    sales, agg = filled(), AggregateStore(path)
    run = daily.run_daily(sales, agg, TENANT, catalog.load_catalog("catbooks").sales, NOW,
                          run_id="d1")  # fmt: skip
    assert run is not None and run.status == "done"
    assert AS_OF.isoformat() in agg.snapshot_days(TENANT)
    agg.close()
    return sales, path


@pytest.fixture
def world(_built, tmp_path, monkeypatch: pytest.MonkeyPatch) -> SalesStore:  # noqa: ANN001
    sales, built = _built
    path = tmp_path / "aggregates.db"
    for suffix in ("", "-wal", "-shm"):
        src = built.with_name(built.name + suffix)
        if src.exists():
            shutil.copyfile(src, path.with_name(path.name + suffix))
    monkeypatch.setenv(DB_ENV, str(path))
    monkeypatch.delenv(read.READ_ENV, raising=False)
    return sales


def test_as_of_is_yesterday_in_korea() -> None:
    assert rb.as_of_for(NOW) == AS_OF
    assert rb.as_of_for(datetime(2026, 11, 15, 15, 1, tzinfo=UTC)) == AS_OF  # 한국 11/16 0:01
    assert rb.note(AS_OF) == "(11월 15일 기준)"


@pytest.mark.parametrize("period", ["365d"])
def test_channel_value_reads_yesterday_snapshot(world, cat, period, monkeypatch) -> None:  # noqa: ANN001
    before, _ = cv.channel_value(world, TENANT, period, cat.sales, cat.id, NOW)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    out, excluded = cv.channel_value(world, TENANT, period, cat.sales, cat.id, NOW)
    assert out.status == "ok" and out.rows
    end = day_window(AS_OF)[1]
    start = rb.window_of(period, AS_OF).start
    assert (out.basis.period_from, out.basis.period_to) == (start, end)
    assert out.summary.endswith("(11월 15일 기준)") and not before.summary.endswith("기준)")
    with world.segment_session(TENANT, HISTORY_FROM, end, []) as s:
        raw, refunds, want_excluded = s.channel_value(start, end, cat.sales.value_horizon_days, end)
    want = cv.build_rows(raw, refunds, cat.sales)
    assert [r.model_dump() for r in out.rows] == [r.model_dump() for r in want]
    assert excluded == want_excluded


def test_channel_value_stays_on_source_without_snapshot(world, cat, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    later = NOW + timedelta(days=2)  # 기준일 11/17 스냅샷이 없다
    want = cv.channel_value(world, TENANT, "90d", cat.sales, cat.id, later)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    assert cv.channel_value(world, TENANT, "90d", cat.sales, cat.id, later) == want
    assert rb.from_snapshot("other-co", NOW, lambda a, d: d) is None
    broken = tmp_path / "broken"
    broken.mkdir()
    monkeypatch.setenv(DB_ENV, str(broken))
    assert rb.from_snapshot(TENANT, NOW, lambda a, d: d) is None


def test_retention_reads_yesterday_snapshot(world, cat, monkeypatch) -> None:  # noqa: ANN001
    """조건 없는 재구매·이탈은 어제 기준 스냅샷. 같은 기준 시각(어제 24시)의 원본 계산과 같다."""
    end = day_window(AS_OF)[1]
    want = rt.retention(world, TENANT, 3, {}, cat.sales, cat.id, end)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    out = rt.retention(world, TENANT, 3, {}, cat.sales, cat.id, NOW)
    assert out.status == "ok" and any(not c.suppressed for c in out.cohorts)
    assert out.cohorts == want.cohorts and out.at_risk == want.at_risk
    assert out.basis.period_to == end and out.basis.period_from == want.basis.period_from
    assert out.summary.endswith("(11월 15일 기준)")


def test_retention_with_filters_stays_on_source(world, cat, monkeypatch) -> None:  # noqa: ANN001
    a = cat.sales.attributes[0]
    f = {a.key: a.values[0] if isinstance(a.values, list) else next(iter(a.values))}
    want = rt.retention(world, TENANT, 3, f, cat.sales, cat.id, NOW)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    assert rt.retention(world, TENANT, 3, f, cat.sales, cat.id, NOW) == want


@pytest.mark.parametrize("level", ["genre", "product"])
def test_basket_pairs_reads_yesterday_snapshot(world, cat, level, monkeypatch) -> None:  # noqa: ANN001
    """보이는 쌍(순서·수·이름)은 같은 기준 시각 원본 계산과 같다. 숨긴 쌍은 자리 수만."""
    w = rb.window_of("30d", AS_OF)
    same = seg.Window(start=w.start, end=w.end, previous_start=w.start, previous_end=w.start)
    with monkeypatch.context() as m:  # 원본 경로를 스냅샷과 같은 기간(꽉 찬 30일)으로
        m.setattr(seg, "segment_window", lambda period, now: same)
        want = bp.basket(world, TENANT, level, "30d", {}, 10, cat.sales, cat.id, w.end)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    out = bp.basket(world, TENANT, level, "30d", {}, 10, cat.sales, cat.id, NOW)
    shown = [p for p in want.pairs if not p.suppressed]
    assert out.status == "ok" and shown
    assert [p for p in out.pairs if not p.suppressed] == shown
    assert sum(p.suppressed for p in out.pairs) == sum(p.suppressed for p in want.pairs)
    assert out.summary.endswith("(11월 15일 기준)")


def test_basket_pairs_with_two_filters_stays_on_source(world, cat, monkeypatch) -> None:  # noqa: ANN001
    a, b = cat.sales.attributes[:2]
    f = {a.key: next(iter(a.values)), b.key: next(iter(b.values))}
    want = bp.basket(world, TENANT, "genre", "90d", f, 10, cat.sales, cat.id, NOW)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    assert bp.basket(world, TENANT, "genre", "90d", f, 10, cat.sales, cat.id, NOW) == want


def _same_window(monkeypatch, period: str):  # noqa: ANN001, ANN202
    """원본 경로를 스냅샷과 같은 기간(기준일까지 꽉 찬 N일)으로 계산하게."""
    w = rb.window_of(period, AS_OF)
    same = seg.Window(start=w.start, end=w.end, previous_start=w.start, previous_end=w.start)
    monkeypatch.setattr(seg, "segment_window", lambda p, now: same)
    return w.end


@pytest.mark.parametrize("n_attrs", [1, 2])
def test_favorites_reads_yesterday_snapshot(world, cat, n_attrs, monkeypatch) -> None:  # noqa: ANN001
    attrs = cat.sales.attributes[:n_attrs]
    checked = 0
    for values in itertools.product(*(list(a.values) for a in attrs)):
        f = {a.key: v for a, v in zip(attrs, values, strict=True)}
        with monkeypatch.context() as m:
            end = _same_window(m, "90d")
            want = seg.favorites(world, TENANT, dict(reversed(f.items())), "90d", 10, cat.sales,
                                 cat.id, end)  # fmt: skip
        with monkeypatch.context() as m:
            m.setenv(read.READ_ENV, read.READ_AGGREGATE)
            out = seg.favorites(world, TENANT, dict(reversed(f.items())), "90d", 10, cat.sales,
                                cat.id, NOW)  # fmt: skip
        assert out.segment_buyers == want.segment_buyers
        assert out.segment_suppressed == want.segment_suppressed
        assert out.internal == want.internal
        assert out.summary.endswith("(11월 15일 기준)")
        checked += not want.segment_suppressed
    assert checked


@pytest.mark.parametrize(("kind", "code"), [("genre", None), ("category", None), ("product", None)])
def test_profile_reads_yesterday_snapshot(world, cat, kind, code, monkeypatch) -> None:  # noqa: ANN001
    agg = AggregateStore(read.db_path())
    rows = [r for r in agg.buyer_rows(TENANT, AS_OF)
            if r["period"] == "30d" and r["dim_kind"] == kind and r["attr_cell"] == ""
            and r["dim_code"] and not r["buyers_hidden"]]  # fmt: skip
    agg.close()
    assert rows
    for target in [rows[0]["dim_code"], "nothing"]:
        if kind != "product" and target == "nothing":
            continue
        with monkeypatch.context() as m:
            end = _same_window(m, "30d")
            want = seg.profile(world, TENANT, kind, target, [], "30d", cat.sales, cat.id, end)
        with monkeypatch.context() as m:
            m.setenv(read.READ_ENV, read.READ_AGGREGATE)
            out = seg.profile(world, TENANT, kind, target, [], "30d", cat.sales, cat.id, NOW)
        assert want.status == "ok"
        for name in ("target_buyers", "target_suppressed", "overall_buyers", "attributes"):
            assert getattr(out, name) == getattr(want, name), (target, name)
        assert out.summary.endswith("(11월 15일 기준)")


def test_coverage_reads_yesterday_snapshot(world, cat, monkeypatch) -> None:  # noqa: ANN001
    pops = cat.sales.population
    assert pops
    for pop in pops:
        keys = list(pop.attribute_keys)
        with monkeypatch.context() as m:
            end = _same_window(m, "365d")
            want = seg.coverage_buyers(world, TENANT, keys, cat.sales, cat.id, end)
        with monkeypatch.context() as m:
            m.setenv(read.READ_ENV, read.READ_AGGREGATE)
            out = seg.coverage_buyers(world, TENANT, keys, cat.sales, cat.id, NOW)
        assert want.status == "ok" and any(not r.suppressed for r in want.rows)
        assert out.rows == want.rows
        assert out.summary.endswith("(11월 15일 기준)")


def test_event_base_reads_yesterday_snapshot(world, cat, monkeypatch) -> None:  # noqa: ANN001
    """이벤트 제안 후보는 어제 기준 스냅샷.
    같은 기간(90일, 대상 고객 끝 = 기준일 24시) 원본과 같다."""
    with monkeypatch.context() as m:
        end = _same_window(m, "90d")
        want = ep.read_base(world, TENANT, cat.sales, cat.id, end)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    got = ep.read_base(world, TENANT, cat.sales, cat.id, NOW)
    assert want.candidates and got.as_of == AS_OF
    assert got.candidates == want.candidates
    assert got.basis.period_to == end
    later = ep.read_base(world, TENANT, cat.sales, cat.id, NOW + timedelta(days=2))
    assert later.as_of is None  # 그 기준일 스냅샷이 없으면 원본


def test_cache_key_follows_the_last_aggregate_run(world, cat, monkeypatch) -> None:  # noqa: ANN001
    """스위치가 켜지면 하루 계산이 새로 끝날 때 캐시를 다시 계산한다 (3-2 ⑧).
    꺼지면 키 조각은 빈 값."""
    assert rb.run_key(TENANT) == ""
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    first = rb.run_key(TENANT)
    assert first == "aggregate:d1"
    box = ResultCache()
    calls: list[int] = []
    ask = lambda: cached("t", world, TENANT, {}, cat.id, "v", NOW, lambda: calls.append(1),  # noqa: E731
                         box, stale_ok=False)  # fmt: skip
    ask()
    ask()
    assert len(calls) == 1
    agg = AggregateStore(read.db_path())
    run = daily.run_daily(world, agg, TENANT, cat.sales, NOW + timedelta(days=1), run_id="d2")
    agg.close()
    assert run is not None and run.status == "done" and rb.run_key(TENANT) == "aggregate:d2"
    ask()
    assert len(calls) == 2


def test_gate_uses_the_aggregate_when_a_snapshot_is_ready(world, cat, monkeypatch) -> None:  # noqa: ANN001
    """수집이 멈춰도(마지막 수집이 오래됨) 어제 기준 스냅샷이 있으면 고객 분석은 막지 않는다.
    (3-2 ⑨)"""
    late = NOW + timedelta(hours=3)  # 마지막 수집(NOW)에서 3시간 뒤, 기준일은 그대로 11/15
    stale = cv.channel_value(world, TENANT, "365d", cat.sales, cat.id, late)[0]
    assert stale.status == "insufficient"
    assert any("마지막 수집" in r for r in stale.insufficient_reasons)
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    out = cv.channel_value(world, TENANT, "365d", cat.sales, cat.id, late)[0]
    assert out.status == "ok" and out.summary.endswith("(11월 15일 기준)")
    gone = cv.channel_value(world, TENANT, "365d", cat.sales, cat.id, late + timedelta(days=2))[0]
    assert gone.status == "insufficient"  # 그 기준일 스냅샷이 없으면 원래 관문 그대로


def test_segment_options_reads_the_source_from_the_aggregate_run(world, cat, monkeypatch) -> None:  # noqa: ANN001
    """고객 분석 선택지의 "가짜 데이터" 표시는 집계 계산 기록의 출처로 정한다 (3-2 ②)."""
    empty = SalesStore(":memory:")  # 원본이 없어도(3-3 뒤) 표시가 같다
    assert seg.read_options(world, TENANT, cat.sales, cat.id).data_source == seg.FAKE_DATA
    assert seg.read_options(empty, TENANT, cat.sales, cat.id).data_source is None  # 스위치 꺼짐
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    assert rb.run_source(TENANT) == (True, "fake")
    got = seg.read_options(empty, TENANT, cat.sales, cat.id)
    assert got == seg.read_options(world, TENANT, cat.sales, cat.id)
    assert got.data_source == seg.FAKE_DATA
    assert rb.run_source("other-co") == (False, None)  # 계산 기록이 없으면 원본 경로


def test_old_runs_table_gets_the_source_column(tmp_path) -> None:  # noqa: ANN001
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE agg_runs (run_id TEXT NOT NULL PRIMARY KEY, tenant_id TEXT NOT NULL, "
        "as_of TEXT NOT NULL, input TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, "
        "status TEXT NOT NULL, reason TEXT, source_cursor TEXT, rows TEXT NOT NULL DEFAULT '{}')"
    )
    conn.execute(
        "INSERT INTO agg_runs VALUES ('r0', 'shop-demo', '2026-11-20', 'sales_db', "
        "'2026-11-21T00:00:00Z', '2026-11-21T00:01:00Z', 'done', NULL, NULL, '{}')"
    )
    conn.commit()
    conn.close()
    agg = AggregateStore(path)
    assert "source_name" in agg.columns("agg_runs")
    assert agg.runs("shop-demo")[0]["source_name"] is None  # 옛 기록은 비어 있다
    agg.close()

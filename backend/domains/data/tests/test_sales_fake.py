"""가짜 판매 출처 (데모 전용 `DATA_SALES_SOURCE=fake`): 같은 seed 같은 결과, 속성 분포, 묶음 선호,
하루치 주문의 안정성, 커서, 수집기 출처 이름, 분석 근거의 "가짜 데이터(데모용)", 회사 카탈로그."""

import asyncio
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from ax_contracts.schemas.data import CatalogSales
from data_agent import catalog, sales_collect
from data_agent.sales_collect import MockSalesSource, collect_once, get_source
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import SalesStore
from data_agent.skills.sales_analysis import FAKE_DATA, analyze, evidence

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00
CONFIG = load_config(FAKE_DIR / "catbooks.toml")


def fake(at: datetime = NOW, **over) -> FakeSalesSource:
    cfg = CONFIG.model_copy(update=over) if over else CONFIG
    return FakeSalesSource(cfg, clock=lambda: at)


def run(coro):
    return asyncio.run(coro)


def test_same_seed_same_orders_and_customers() -> None:
    a, b = fake(), fake()
    assert [o.model_dump() for o in a.orders()] == [o.model_dump() for o in b.orders()]
    assert a.customers() == b.customers()
    other = fake(seed=CONFIG.seed + 1)
    assert [o.buyer_id for o in other.orders()] != [o.buyer_id for o in a.orders()]
    assert other.customers() != a.customers()


def test_day_orders_are_the_same_whatever_the_run_date() -> None:
    day = date(2026, 10, 1)
    early = fake(datetime(2026, 10, 2, 3, 0, tzinfo=UTC))
    late = fake(datetime(2026, 12, 20, 3, 0, tzinfo=UTC))
    assert early.day_orders(day) == late.day_orders(day)
    in_early = [o for o in early.orders() if o.order_id.startswith("fk_o20261001_")]
    in_late = [o for o in late.orders() if o.order_id.startswith("fk_o20261001_")]
    assert in_early == in_late and len(in_early) > 0
    assert all(o.order_id.startswith("fk_o20261001_") for o in early.day_orders(day))


def test_window_is_period_days_up_to_today_and_no_future_rows() -> None:
    src = fake()
    days = src.window_days()
    assert len(days) == CONFIG.period_days and days[-1] == date(2026, 11, 16)
    rows = src.orders()
    assert rows and all(o.updated_at <= NOW for o in rows)
    created_days = {o.order_id[4:12] for o in rows}
    assert min(created_days) == days[0].strftime("%Y%m%d")
    assert all(c.updated_at <= NOW for c in src.customers())


def test_attribute_distribution_within_tolerance_and_some_unknown() -> None:
    members = [m.row for m in fake().members()]
    assert len(members) == CONFIG.members == 3000
    for key, spec in CONFIG.attributes.items():
        present = [m.attributes[key] for m in members if key in m.attributes]
        missing = 1 - len(present) / len(members)
        assert abs(missing - spec.missing) < 0.03, key  # 일부는 "모름"
        total = sum(spec.values.values())
        counts = Counter(present)
        for code, weight in spec.values.items():
            assert abs(counts[code] / len(present) - weight / total) < 0.04, (key, code)
    assert any(len(m.attributes) < len(CONFIG.attributes) for m in members)


def _genre_share(
    src: FakeSalesSource, when: dict[str, str], genres: set[str]
) -> tuple[float, float]:
    attrs = {m.row.buyer_id: m.row.attributes for m in src.members()}

    def match(buyer: str) -> bool:
        return all(attrs[buyer].get(k) == v for k, v in when.items())

    inside = Counter[bool]()
    outside = Counter[bool]()
    for o in src.orders():
        for line in o.lines:
            (inside if match(o.buyer_id) else outside)[line.genre_code in genres] += 1
    return inside[True] / sum(inside.values()), outside[True] / sum(outside.values())


def test_group_preferences_show_in_orders() -> None:
    src = fake()
    for when, genres in (
        ({"age_band": "30s", "occupation": "employee"}, {"ai_it", "business"}),
        ({"age_band": "20s", "gender": "female"}, {"essay", "novel"}),
        ({"age_band": "10s", "occupation": "student"}, {"comics"}),
    ):
        group, rest = _genre_share(src, when, genres)
        assert group > rest * 1.4, (when, group, rest)


def test_products_use_catalog_categories_and_genres() -> None:
    sales = catalog.load_catalog("catbooks").sales
    assert sales is not None
    for p in CONFIG.products:
        assert p.category_code in sales.categories and p.genre_code in sales.genres
    for key, spec in CONFIG.attributes.items():
        defined = next(a for a in sales.attributes if a.key == key)
        assert set(spec.values) <= set(defined.values)


def test_export_cursor_semantics_like_the_real_server() -> None:
    src = fake()
    total = len(src.orders())
    first = run(src.sales_export(None, 1000))
    assert first.has_more and len(first.orders) == 1000
    rest = run(src.sales_export(first.next_cursor, 100000))
    assert not rest.has_more and len(first.orders) + len(rest.orders) == total
    assert rest.orders[0].order_id != first.orders[-1].order_id
    empty = run(src.sales_export(rest.next_cursor, 10))
    assert empty.orders == [] and empty.next_cursor == rest.next_cursor and not empty.has_more
    people = run(src.customers_export(None, 3000))
    keys = [(c.updated_at, c.buyer_id) for c in people.customers]
    assert keys == sorted(keys) and not people.has_more


def test_collector_uses_fake_source_name_and_continues() -> None:
    store = SalesStore(":memory:")
    morning = datetime(2026, 11, 15, 23, 0, tzinfo=UTC)  # 한국 11/16 08:00
    first = run(collect_once(fake(morning), store, source_name="fake", now=morning))
    assert first.errors == [] and first.notes == [] and first.customers_saved == 3000
    before = store.order_count(TENANT)
    second = run(collect_once(fake(NOW), store, source_name="fake", now=NOW))
    assert second.customers_saved == 0  # 새로 가입한 회원 없음 (커서 다음부터)
    added = [o for o in fake(NOW).orders() if o.updated_at > morning]
    assert added and second.saved == len(added)  # 아침 뒤에 나온 주문만 이어 받았다
    assert store.order_count(TENANT) == before + len(added)
    assert store.sync_state(TENANT, "fake").last_synced_at == NOW
    assert store.sync_state(TENANT, "fake:customers").last_error is None
    assert store.sync_state(TENANT, "shop-api").cursor is None  # 실제 서버 커서와 섞이지 않음


def test_source_switch_is_env_only() -> None:
    assert isinstance(get_source({"DATA_SALES_SOURCE": "fake"}), FakeSalesSource)
    assert sales_collect.source_name({"DATA_SALES_SOURCE": "fake"}) == "fake"
    assert sales_collect.source_name({}) == "shop-api"  # 기본은 실제 판매 서버
    assert not sales_collect.uses_fake({})
    # 목 모드(테스트 전용)가 켜져 있으면 목 표본이 이긴다
    mocked = {"DATA_SALES_SOURCE": "fake", "DATA_MOCK": "1"}
    assert isinstance(get_source(mocked), MockSalesSource)
    assert sales_collect.source_name(mocked) == "shop-api"


def test_analysis_basis_shows_fake_data() -> None:
    store = SalesStore(":memory:")
    run(collect_once(fake(), store, source_name="fake", now=NOW))
    cfg = catalog.load_catalog("catbooks").sales
    out = analyze(store, TENANT, "30d", cfg, "catbooks", NOW)
    assert out.basis.data_source == FAKE_DATA == "가짜 데이터(데모용)"
    assert out.status == "ok" and out.summary.startswith("[가짜 데이터(데모용)]")
    assert "가짜 데이터(데모용)" in evidence(out, NOW).data_summary
    assert out.quality.unknown_genres == [] and out.quality.unmapped_attribute_values == {}
    real = SalesStore(":memory:")
    real.finish_sync(TENANT, "shop-api", NOW, None)
    plain = analyze(real, TENANT, "today", CatalogSales(), "catbooks", NOW)
    assert plain.basis.data_source is None and not plain.summary.startswith("[")


def test_window_moves_but_old_days_do_not_change() -> None:
    day = date(2026, 11, 1)
    a = fake(datetime(2026, 11, 2, tzinfo=UTC)).day_orders(day)
    b = fake(datetime(2026, 11, 2, tzinfo=UTC) + timedelta(days=60)).day_orders(day)
    assert a == b

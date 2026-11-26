"""고객·시장 분석 — 회사 설정이 없는 새 회사(사이트 변환기, 11/26).

공통 카탈로그(`catalogs/common.toml`)의 속성(연령대·성별)과 그 회사 판매 기록의 사이트 분류 값으로
화면이 돈다. 사이트가 속성을 주지 않으면 "이 회사 사이트에서 받은 고객 속성이 없어요"로 멈추고,
다른 회사(캣북스) 값은 섞이지 않는다.
"""

from datetime import UTC, datetime, timedelta

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from data_agent import catalog
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import segment_analysis as seg

NUBI = "c-cf2f0d"
CATBOOKS = "shop-demo"
NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)
DAY = NOW - timedelta(days=2)
COMMON = catalog.load_catalog("common").sales


def _order(n: int, buyer: str, category: str) -> SalesOrderRow:
    return SalesOrderRow(
        order_id=f"o{n}", buyer_id=buyer, status="paid", created_at=DAY, updated_at=DAY,
        items_amount=10000, payment_status="succeeded",
        lines=[SalesOrderLine(product_id=f"t_{category}", category_code=category,
                              title=f"투어 {category}", qty=1, unit_price=10000)],
    )  # fmt: skip


def _store(nubi_attrs: bool) -> SalesStore:
    store = SalesStore(":memory:")
    nubi = [(f"n{i:02d}", "JP" if i < 15 else "EU") for i in range(30)]
    store.upsert(NUBI, [_order(i, b, c) for i, (b, c) in enumerate(nubi)], NOW)
    store.finish_sync(NUBI, "site", NOW - timedelta(minutes=1), None)
    store.upsert_customers(NUBI, [
        CustomerRow(buyer_id=b, joined_at=DAY, updated_at=DAY,
                    attributes=({"age_band": "30s" if i % 2 else "20s",
                                 "gender": "female" if i % 3 else "male"} if nubi_attrs else {}))
        for i, (b, _) in enumerate(nubi)
    ], NOW)  # fmt: skip
    store.finish_sync(NUBI, customers_source("site"), NOW, None)
    # 같은 저장소의 캣북스: 분류 book, 속성 있음 — 누비투어 화면에 나오면 안 된다
    cat = [_order(100 + i, f"c{i}", "book") for i in range(10)]
    store.upsert(CATBOOKS, cat, NOW)
    store.upsert_customers(CATBOOKS, [
        CustomerRow(buyer_id=f"c{i}", joined_at=DAY, updated_at=DAY,
                    attributes={"age_band": "40s", "gender": "female"}) for i in range(10)
    ], NOW)  # fmt: skip
    return store


def test_common_catalog_has_attributes_but_no_category_table() -> None:
    assert COMMON is not None
    assert [a.key for a in COMMON.attributes] == ["age_band", "gender"]
    assert COMMON.categories == {}


def test_options_use_this_company_site_categories_only() -> None:
    out = seg.read_options(_store(True), NUBI, COMMON, "common")
    assert [(c.code, c.label) for c in out.categories] == [("EU", "EU"), ("JP", "JP")]
    assert [a.key for a in out.attributes] == ["age_band", "gender"]


def test_profile_by_site_category_with_site_attributes() -> None:
    out = seg.profile(_store(True), NUBI, "category", "JP", [], "30d", COMMON, "common", NOW)
    assert out.status == "ok", out.insufficient_reasons
    assert out.target_label == "JP"
    assert (out.target_buyers, out.overall_buyers) == (15, 30)  # 캣북스 구매자 10명 안 섞임
    ages = next(a for a in out.attributes if a.key == "age_band")
    forties = next(v for v in ages.values if v.value_code == "40s")
    assert not forties.buyers  # 캣북스 고객은 모두 40대 — 누비투어 칸에 없다


def test_no_site_attributes_stops_with_reason() -> None:
    out = seg.profile(_store(False), NUBI, "category", "JP", [], "30d", COMMON, "common", NOW)
    assert out.status == "insufficient"
    assert seg.NO_ATTRIBUTES in out.insufficient_reasons
    assert "이 회사 사이트에서 받은 고객 속성이 없어요" in out.summary


def test_catalog_with_category_table_keeps_its_own_list() -> None:
    cfg = COMMON.model_copy(update={"categories": {"JP": "일본"}})
    out = seg.read_options(_store(True), NUBI, cfg, "x")
    assert [(c.code, c.label) for c in out.categories] == [("JP", "일본")]

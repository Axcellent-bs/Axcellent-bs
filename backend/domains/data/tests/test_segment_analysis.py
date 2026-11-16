"""고객·시장 분석 (11/16): segment_options · segment_profile · segment_favorites · market_coverage.

표준 판매 저장소만 읽는다(`:memory:`). 바깥 출처는 httpx MockTransport와 tmp_path의 record/replay
파일로만 돈다(네트워크 없음). 응답은 테스트 전용 가짜 응답이다.
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    AttributeDef,
    CatalogSales,
    ExternalFilter,
    GenreExternal,
    MarketCoverageOutput,
    PopulationSource,
    SegmentFavoritesOutput,
    SegmentOptionsOutput,
    SegmentProfileOutput,
)
from data_agent import catalog, handlers
from data_agent.connectors.data4library import Data4LibraryConnector
from data_agent.connectors.kosis import KosisConnector
from data_agent.connectors.naver_shopping import DayCache
from data_agent.sales_collect import NO_CUSTOMERS_EXPORT, collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import FAKE_DATA

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00
DAY = NOW - timedelta(days=3)
D4L_KEY = {"DATA4LIBRARY_API_KEY": "test-d4l-key-0000"}
KOSIS_KEY = {"KOSIS_API_KEY": "test-kosis-key-0000"}

CFG = CatalogSales(
    min_period_orders=1,
    categories={"book": "도서", "stationery": "문구"},
    genres={"g1": "장르1", "g2": "장르2"},
    attributes=[
        AttributeDef(key="age_band", label="연령대", values={"10s": "10대", "20s": "20대"}),
        AttributeDef(key="gender", label="성별", values={"female": "여성", "male": "남성"}),
    ],
    external_filters=[
        ExternalFilter(source_id="data4library", attribute_key="age_band", value_code="20s",
                       params={"from_age": 20, "to_age": 29}),
    ],  # fmt: skip
    genre_external=[GenreExternal(source_id="data4library", genre_code="g1", params={"kdc": "8"})],
    population=[PopulationSource(attribute_keys=["age_band", "gender"], source_id="kosis")],
)


def run(coro):
    return asyncio.run(coro)


def order(n: int, buyer: str, genre: str, status: str = "paid") -> SalesOrderRow:
    return SalesOrderRow(
        order_id=f"o{n}", buyer_id=buyer, status=status, created_at=DAY, updated_at=DAY,
        items_amount=10000, payment_status="succeeded" if status == "paid" else "failed",
        lines=[SalesOrderLine(product_id=f"p_{genre}", category_code="book", title=f"책 {genre}",
                              qty=1, unit_price=10000, genre_code=genre)],
    )  # fmt: skip


def people() -> list[tuple[str, dict[str, str], str]]:
    """(가명 buyer_id, 속성, 산 장르). 20대 10명(8명 g1), 10대 10명(2명 g1), 모름 6명(g1)."""
    rows = []
    for i in range(10):
        rows.append((f"pseud_a{i:02d}", {"age_band": "20s", "gender": "female"},
                     "g1" if i < 8 else "g2"))  # fmt: skip
    for i in range(10):
        rows.append((f"pseud_b{i:02d}", {"age_band": "10s", "gender": "female"},
                     "g1" if i < 2 else "g2"))  # fmt: skip
    for i in range(6):
        rows.append((f"pseud_c{i:02d}", {}, "g1"))  # 속성 없음 = 모름
    return rows


def small_store(customers_error: str | None = None, with_customers: bool = True) -> SalesStore:
    store = SalesStore(":memory:")
    rows = people()
    orders = [order(n, buyer, genre) for n, (buyer, _, genre) in enumerate(rows)]
    orders.append(order(999, "pseud_a00", "g2", status="cancelled"))  # 결제 안 된 주문은 빼고 센다
    store.upsert(TENANT, orders, NOW)
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    if with_customers:
        store.upsert_customers(
            TENANT,
            [CustomerRow(buyer_id=b, joined_at=DAY, updated_at=DAY, attributes=a)
             for b, a, _ in rows],
            NOW,
        )  # fmt: skip
    store.finish_sync(TENANT, customers_source("shop-api"), NOW, customers_error)
    return store


def profile(store: SalesStore, cfg: CatalogSales | None = CFG, **over: Any) -> SegmentProfileOutput:
    args: dict[str, Any] = {
        "target_kind": "genre", "target_code": "g1", "attribute_keys": [], "period": "30d",
    }  # fmt: skip
    args.update(over)
    return seg.profile(store, TENANT, cfg=cfg, catalog_id="test", now=NOW, **args)


def share(out: SegmentProfileOutput, key: str, code: str):
    attr = next(a for a in out.attributes if a.key == key)
    return next(v for v in attr.values if v.value_code == code)


# ---------------------------------------------------------------- segment_profile


def test_profile_lift_and_known_denominator() -> None:
    out = profile(small_store())
    assert out.status == "ok" and out.target_label == "장르1"
    assert out.target_buyers == 16 and out.overall_buyers == 26  # 취소 주문은 세지 않음
    twenties = share(out, "age_band", "20s")
    # 대상 중 아는 사람 10명 중 8명, 전체 중 아는 사람 20명 중 10명 → 0.8 ÷ 0.5 = 1.6배
    assert twenties.buyers == 8 and twenties.target_share == 0.8
    assert twenties.overall_share == 0.5 and twenties.lift == 1.6
    assert [a.key for a in out.attributes] == ["age_band", "gender"]  # 카탈로그 순서
    assert [v.value_code for v in out.attributes[0].values] == ["10s", "20s"]
    assert "1.60배" in out.summary and out.basis and out.basis.data_source is None


def test_profile_suppresses_small_groups() -> None:
    out = profile(small_store())
    teens = share(out, "age_band", "10s")  # 대상 중 2명 < 5
    assert teens.suppressed and teens.buyers is None
    assert teens.target_share is None and teens.overall_share is None and teens.lift is None
    male = share(out, "gender", "male")  # 0명
    assert male.suppressed and male.buyers is None
    dump = json.dumps(out.model_dump(mode="json"), ensure_ascii=False)
    assert "pseud_" not in dump  # 구매자 ID는 내지 않는다


def test_profile_counts_unknown_attributes() -> None:
    out = profile(small_store())
    age = next(a for a in out.attributes if a.key == "age_band")
    assert age.unknown_buyers == 6  # 속성 없는 6명 = 모름
    # 카탈로그에 없는 값도 모름이다
    store = small_store()
    store.upsert_customers(
        TENANT,
        [CustomerRow(buyer_id="pseud_a00", joined_at=DAY, updated_at=DAY,
                     attributes={"age_band": "70s", "gender": "female"})],
        NOW,
    )  # fmt: skip
    again = profile(store)
    assert next(a for a in again.attributes if a.key == "age_band").unknown_buyers == 7
    assert share(again, "age_band", "20s").buyers == 7
    assert again.quality.unmapped_attribute_values == {"age_band": ["70s"]}


def test_profile_hides_everything_when_target_is_small() -> None:
    out = profile(small_store(), target_kind="product", target_code="p_none")
    assert out.status == "ok" and out.target_suppressed and out.target_buyers is None
    assert all(v.suppressed for a in out.attributes for v in a.values)
    assert all(a.unknown_buyers is None for a in out.attributes)
    assert "미만이라 숫자를 숨김" in out.summary


def test_profile_rejects_unknown_attribute_and_target() -> None:
    out = profile(small_store(), attribute_keys=["age_band", "hobby"], target_code="g9")
    assert out.status == "insufficient" and out.attributes == []
    assert any("설정에 없는 속성: hobby" in r for r in out.insufficient_reasons)
    assert any("설정에 없는 장르: g9" in r for r in out.insufficient_reasons)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"customers_error": NO_CUSTOMERS_EXPORT}, "판매 서버에 고객 내보내기 없음"),
        ({"with_customers": False}, "저장된 고객 기록이 없음"),
    ],
)
def test_gate_reasons_for_customer_records(kwargs: dict, expected: str) -> None:
    store = small_store(**kwargs)
    for out in (
        profile(store),
        seg.favorites(store, TENANT, {"age_band": "20s"}, "30d", 10, CFG, "test", NOW),
        seg.coverage_buyers(store, TENANT, ["age_band", "gender"], CFG, "test", NOW),
    ):
        assert out.status == "insufficient"
        assert any(expected in r for r in out.insufficient_reasons)
        assert expected in out.summary


def test_gate_uses_sales_quality_gate() -> None:
    store = small_store()
    stale = profile(store, cfg=CFG.model_copy(update={"min_period_orders": 100}))
    assert stale.status == "insufficient"
    assert any("결제 완료 주문 26건" in r for r in stale.insufficient_reasons)
    none = profile(store, cfg=None)
    assert none.status == "insufficient" and "설정([sales])이 없음" in none.insufficient_reasons[0]


# ---------------------------------------------------------------- segment_favorites


def test_favorites_internal_ranking() -> None:
    out = seg.favorites(small_store(), TENANT, {"age_band": "20s"}, "30d", 10, CFG, "test", NOW)
    assert out.status == "ok" and out.segment_buyers == 10 and out.filter_label == "20대"
    genres = [i for i in out.internal if i.kind == "genre"]
    assert genres[0].code == "g1" and genres[0].buyers == 8 and genres[0].share == 0.8
    assert genres[0].lift == round(0.8 / round(16 / 26, 4), 4)
    assert genres[1].code == "g2" and genres[1].suppressed and genres[1].share is None  # 2명
    category = next(i for i in out.internal if i.kind == "category")
    assert category.label == "도서" and category.share == 1.0
    products = [(i.label, i.suppressed) for i in out.internal if i.kind == "product"]
    assert products == [("책 g1", False), ("책 g2", True)]  # 숨김은 뒤에, 숫자 없이
    assert "pseud_" not in json.dumps(out.model_dump(mode="json"), ensure_ascii=False)


def test_favorites_small_segment_and_bad_filters() -> None:
    store = small_store()
    tiny = seg.favorites(store, TENANT, {"age_band": "20s", "gender": "male"}, "30d", 10, CFG,
                         "test", NOW)  # fmt: skip
    assert tiny.status == "ok" and tiny.segment_suppressed and tiny.segment_buyers is None
    assert tiny.internal == []
    bad = seg.favorites(store, TENANT, {"age_band": "90s", "job": "x"}, "30d", 10, CFG, "t", NOW)
    assert bad.status == "insufficient"
    assert "속성 age_band에 없는 값: 90s" in bad.insufficient_reasons
    assert any("설정에 없는 속성: job" in r for r in bad.insufficient_reasons)


LOANS = {
    "response": {
        "numFound": 2,
        "docs": [
            {"doc": {"ranking": "2", "bookname": "둘째 책", "loan_count": "1,200"}},
            {"doc": {"ranking": "1", "bookname": "첫째 책", "loan_count": "1500"}},
        ],
    }
}


def _d4l(env: dict[str, str], handler) -> tuple[Data4LibraryConnector, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    cache = DayCache(today=lambda: date(2026, 11, 16))
    return Data4LibraryConnector(env, client=client, cache=cache), seen


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError("네트워크를 부르면 안 된다")


def _favorites(store: SalesStore, conn: Data4LibraryConnector) -> SegmentFavoritesOutput:
    return run(
        seg.favorites_with_external(store, TENANT, {"age_band": "20s"}, "30d", 10, CFG, "test",
                                    NOW, d4l=conn)
    )  # fmt: skip


def test_external_record_then_replay(tmp_path: Path) -> None:
    store = small_store()
    record = {**D4L_KEY, "DATA_EXTERNAL_MODE": "record", "DATA_REPLAY_DIR": str(tmp_path)}
    conn, seen = _d4l(record, lambda r: httpx.Response(200, json=LOANS))
    first = _favorites(store, conn)
    assert len(seen) == 2  # 연령 조건 1번 + 많이 산 장르(g1, KDC 8) 1번
    params = [dict(r.url.params) for r in seen]
    assert params[0]["from_age"] == "20" and "kdc" not in params[0]
    assert params[1]["kdc"] == "8" and params[1]["to_age"] == "29"
    saved = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "test-d4l-key" not in saved

    replay = {"DATA_EXTERNAL_MODE": "replay", "DATA_REPLAY_DIR": str(tmp_path)}
    conn2, seen2 = _d4l(replay, _no_network)
    out = _favorites(store, conn2)
    assert seen2 == [] and out.external == first.external
    base, genre = out.external
    assert base.status == "ok" and base.source_id == "data4library"
    assert [(i.rank, i.name, i.value, i.unit) for i in base.items] == [
        (1, "첫째 책", 1500.0, "회"),
        (2, "둘째 책", 1200.0, "회"),
    ]
    assert base.condition_label == "20대" and base.period_label.startswith("최근 30일 (")
    assert genre.label.endswith("장르1") and "KDC 8" in genre.condition_label
    assert "바깥 출처 2곳 중 2곳 확인" in out.summary


def test_external_key_missing_and_rate_limit() -> None:
    store = small_store()
    conn, seen = _d4l({}, _no_network)  # 키 없음: 부르지 않는다
    out = _favorites(store, conn)
    assert seen == [] and [e.status for e in out.external] == ["key_missing", "key_missing"]
    assert all(e.items == [] and "UNAUTHORIZED" in e.reason for e in out.external)

    limited = {**D4L_KEY, "DATA4LIBRARY_DAILY_LIMIT": "1"}
    conn2, seen2 = _d4l(limited, lambda r: httpx.Response(200, json=LOANS))
    out2 = _favorites(store, conn2)
    assert len(seen2) == 1 and [e.status for e in out2.external] == ["ok", "limit"]
    assert out2.external[1].items == [] and "RATE_LIMITED" in out2.external[1].reason

    conn3, _ = _d4l(D4L_KEY, lambda r: httpx.Response(500, json={}))
    out3 = _favorites(store, conn3)
    assert {e.status for e in out3.external} == {"error"}


def test_external_not_configured_for_filter() -> None:
    store = small_store()
    conn, seen = _d4l(D4L_KEY, lambda r: httpx.Response(200, json=LOANS))
    out = run(
        seg.favorites_with_external(store, TENANT, {"age_band": "10s"}, "30d", 10, CFG, "test",
                                    NOW, d4l=conn)
    )  # fmt: skip
    base = out.external[0]
    assert base.status == "error" and base.reason.startswith("설정 필요")
    assert base.items == [] and len(seen) == 0  # 10대는 g1이 2명이라 숨김 → 장르 호출도 없음


# ---------------------------------------------------------------- market_coverage


def kosis_rows() -> list[dict[str, Any]]:
    rows = []
    for c1, age in (("A10", "10대"), ("A20", "20대")):
        for c2, sex, people_n in (("F", "여자", 1000), ("M", "남자", 900)):
            for prd, scale in (("2024", 0.9), ("2025", 1.0)):
                rows.append({"ORG_ID": "101", "TBL_ID": "T_AGE_SEX", "TBL_NM": "연령 성별 인구",
                             "C1": c1, "C1_NM": age, "C2": c2, "C2_NM": sex, "ITM_ID": "T1",
                             "UNIT_NM": "명", "PRD_SE": "Y", "PRD_DE": prd,
                             "DT": str(int(people_n * scale))})  # fmt: skip
    return rows


TABLE_REF = {
    "org_id": "101", "tbl_id": "T_AGE_SEX", "itm_id": "T1", "prd_se": "Y",
    "codes": {"age_band": {"10s": "A10", "20s": ["A20"]}, "gender": {"female": "F", "male": "M"}},
}  # fmt: skip


def _kosis(env: dict[str, str], handler) -> tuple[KosisConnector, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return KosisConnector(env, client=client, cache=DayCache(lambda: date(2026, 11, 16))), seen


def coverage(cfg: CatalogSales, conn: KosisConnector, keys=("gender", "age_band")):
    return run(
        seg.coverage_with_population(small_store(), TENANT, list(keys), cfg, "test", NOW,
                                     kosis=conn)
    )  # fmt: skip


def test_coverage_population_missing_when_table_not_set() -> None:
    conn, seen = _kosis(KOSIS_KEY, _no_network)
    out = coverage(CFG, conn)
    assert out.status == "ok" and seen == []
    assert out.attribute_keys == ["age_band", "gender"]  # 카탈로그 순서
    labels = ["10대 · 여성", "10대 · 남성", "20대 · 여성", "20대 · 남성"]
    assert [r.label for r in out.rows] == labels
    assert all(r.status == "population_missing" and r.reason == "인구 통계표 설정 필요"
               and r.population is None and r.coverage is None for r in out.rows)  # fmt: skip
    by = {r.label: r for r in out.rows}
    assert by["20대 · 여성"].buyers == 10 and by["10대 · 여성"].buyers == 10
    assert by["20대 · 남성"].suppressed and by["20대 · 남성"].buyers is None
    assert "인구 비교는 못 함" in out.summary


def test_coverage_with_kosis_age_by_gender_table() -> None:
    pop = PopulationSource(attribute_keys=["age_band", "gender"], source_id="kosis",
                           table_ref=TABLE_REF)  # fmt: skip
    cfg = CFG.model_copy(update={"population": [pop]})
    conn, seen = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=kosis_rows()))
    out = coverage(cfg, conn)
    params = dict(seen[0].url.params)
    assert params["objL1"] == "ALL" and params["objL2"] == "ALL"
    assert params["newEstPrdCnt"] == "1" and params["tblId"] == "T_AGE_SEX"
    by = {r.label: r for r in out.rows}
    row = by["20대 · 여성"]
    assert row.status == "ok" and row.population == 1000 and row.base_year == 2025  # 최근 시점
    assert row.coverage == 0.01 and row.population_source == "연령 성별 인구"
    male = by["20대 · 남성"]
    assert male.population == 900 and male.suppressed and male.coverage is None
    assert "커버 비율을 계산" in out.summary


def test_coverage_kosis_errors_are_reasons_not_numbers() -> None:
    pop = PopulationSource(attribute_keys=["age_band", "gender"], source_id="kosis",
                           table_ref=TABLE_REF)  # fmt: skip
    cfg = CFG.model_copy(update={"population": [pop]})
    conn, _ = _kosis({}, _no_network)
    out = coverage(cfg, conn)
    assert all(r.status == "error" and "UNAUTHORIZED" in r.reason and r.population is None
               for r in out.rows)  # fmt: skip


def test_coverage_rejects_combination_not_in_config() -> None:
    conn, seen = _kosis(KOSIS_KEY, _no_network)
    out = coverage(CFG, conn, keys=("age_band",))
    assert out.status == "insufficient" and out.rows == [] and seen == []
    assert "설정에 없는 속성 조합: age_band" in out.insufficient_reasons[0]
    assert "age_band·gender" in out.insufficient_reasons[0]


def test_kosis_obj_l2_only_when_given() -> None:
    conn, seen = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=kosis_rows()))
    old = run(conn.statistics_data("101", "T_AGE_SEX", "T1", "ALL", "Y"))
    assert "objL2" not in dict(seen[0].url.params)  # 예전 호출은 그대로
    assert old[0].c1 == "A10" and old[0].c2 == "F"  # 두 번째 분류도 읽는다 (없으면 빈 문자열)
    run(conn.statistics_data("101", "T_AGE_SEX", "T1", "ALL", "Y", obj_l2="F"))
    assert dict(seen[1].url.params)["objL2"] == "F"


# ---------------------------------------------------------------- segment_options


def test_options_follow_catalog_order_and_contract() -> None:
    cat = catalog.load_catalog("catbooks")
    sales = cat.sales
    assert sales is not None
    out = seg.options(sales, cat.id, None)
    SegmentOptionsOutput.model_validate(out.model_dump(mode="json"))
    assert [a.key for a in out.attributes] == [a.key for a in sales.attributes]
    for got, want in zip(out.attributes, sales.attributes, strict=True):
        assert [(v.code, v.label) for v in got.values] == list(want.values.items())
    assert [(g.code, g.label) for g in out.genres] == list(sales.genres.items())
    assert [c.code for c in out.categories] == list(sales.categories)
    assert [q.label for q in out.quick_questions] == [q.label for q in sales.quick_questions]
    assert [e.code for e in out.event_kinds] == [e.code for e in sales.event_kinds]
    assert out.population_keys == [p.attribute_keys for p in sales.population]
    assert out.periods == ["30d", "90d", "365d"] and out.min_group_size == sales.min_group_size
    assert out.catalog_id == cat.id and out.catalog_version == sales.version
    empty = seg.options(None, "x", None)
    assert empty.attributes == [] and empty.catalog_id == "x"


# ---------------------------------------------------------------- 가짜 출처로 끝까지


@pytest.fixture(scope="module")
def fake_store() -> SalesStore:
    store = SalesStore(":memory:")
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    result = run(collect_once(src, store, source_name="fake", now=NOW))
    assert result.errors == []
    return store


def test_end_to_end_with_fake_source(fake_store: SalesStore) -> None:
    cat = catalog.load_catalog("catbooks")
    sales = cat.sales
    opts = seg.read_options(fake_store, TENANT, sales, cat.id)
    assert opts.data_source == FAKE_DATA

    prof = seg.profile(fake_store, TENANT, "genre", "novel", [], "90d", sales, cat.id, NOW)
    assert prof.status == "ok" and not prof.target_suppressed and prof.target_buyers
    assert prof.basis and prof.basis.data_source == FAKE_DATA
    assert prof.summary.startswith(f"[{FAKE_DATA}]")
    assert [a.key for a in prof.attributes] == ["age_band", "gender", "occupation"]
    assert any(a.unknown_buyers for a in prof.attributes)  # 가짜 회원 일부는 모름

    conn, seen = _d4l({}, _no_network)  # 키 없음 → 이유만
    fav = run(
        seg.favorites_with_external(fake_store, TENANT, {"age_band": "20s", "gender": "female"},
                                    "90d", 5, sales, cat.id, NOW, d4l=conn)
    )  # fmt: skip
    assert fav.status == "ok" and fav.segment_buyers and fav.filter_label == "20대 · 여성"
    assert fav.basis and fav.basis.data_source == FAKE_DATA
    top_genre = next(i for i in fav.internal if i.kind == "genre")
    assert top_genre.code in {"essay", "novel"} and (top_genre.lift or 0) > 1  # 가짜 선호가 보인다
    assert fav.external and {e.status for e in fav.external} == {"key_missing"} and seen == []

    kconn, kseen = _kosis({}, _no_network)
    cov = run(
        seg.coverage_with_population(fake_store, TENANT, ["age_band", "gender"], sales, cat.id,
                                     NOW, kosis=kconn)
    )  # fmt: skip
    assert cov.status == "ok" and cov.basis and cov.basis.data_source == FAKE_DATA
    assert len(cov.rows) == 5 * 3 and any(r.buyers for r in cov.rows) and kseen == []
    assert {r.status for r in cov.rows} == {"population_missing"}  # 캣북스 표 id 미확인

    for out in (prof, fav, cov):
        ev = seg.evidence(out.basis, NOW)
        assert FAKE_DATA in ev.data_summary and f"판 {sales.version}" in ev.data_summary
        assert "sales_store.sales_customers" in ev.queries


# ---------------------------------------------------------------- 핸들러


def _request(skill: str, **inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input=inp)


def test_handlers_registered_and_check_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    for skill in ("segment_options", "segment_profile", "segment_favorites", "market_coverage"):
        assert skill in handlers.HANDLERS
    other = run(handlers.segment_profile(
        _request("segment_profile", target_kind="genre", target_code="novel", tenant_id="other")
    ))  # fmt: skip
    assert other.status == TaskStatus.FAILED and other.outcome == "invalid_input"
    bad = run(handlers.market_coverage(_request("market_coverage", attribute_keys=[])))
    assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"


def test_handlers_read_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA4LIBRARY_API_KEY", raising=False)
    monkeypatch.delenv("KOSIS_API_KEY", raising=False)
    monkeypatch.setenv("DATA_EXTERNAL_MODE", "live")
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    store = small_store()
    store.finish_sync(TENANT, "shop-api", datetime(2026, 1, 1, tzinfo=UTC), None)  # 오래된 수집
    handlers.use_sales_store(store)
    try:
        opts = run(handlers.segment_options(_request("segment_options")))
        prof = run(handlers.segment_profile(
            _request("segment_profile", target_kind="genre", target_code="novel")
        ))  # fmt: skip
        fav = run(handlers.segment_favorites(
            _request("segment_favorites", filters={"age_band": "20s"})
        ))  # fmt: skip
        cov = run(handlers.market_coverage(
            _request("market_coverage", attribute_keys=["age_band", "gender"])
        ))  # fmt: skip
    finally:
        handlers.use_sales_store(None)
        catalog.use_catalog(None)
    assert opts.status == TaskStatus.COMPLETED and opts.evidence
    options = SegmentOptionsOutput.model_validate(opts.output)
    assert options.population_keys == [["age_band", "gender"]]
    for result, model in (
        (prof, SegmentProfileOutput),
        (fav, SegmentFavoritesOutput),
        (cov, MarketCoverageOutput),
    ):
        assert result.status == TaskStatus.COMPLETED and result.evidence
        out = model.model_validate(result.output)
        assert out.status == "insufficient"  # 마지막 수집이 오래됨 (지금 시각 기준)
        assert "카탈로그 catbooks" in result.evidence.data_summary

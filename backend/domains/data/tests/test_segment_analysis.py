"""고객·시장 분석 (11/16): segment_options · segment_profile · segment_favorites · market_coverage.

표준 판매 저장소만 읽는다(`:memory:`). 바깥 출처는 httpx MockTransport와 tmp_path의 record/replay
파일로만 돈다(네트워크 없음). 응답은 테스트 전용 가짜 응답이다.
"""

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
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
    CoverageRow,
    ExternalFilter,
    FavoriteItem,
    GenreExternal,
    MarketCoverageOutput,
    PopulationSource,
    SegmentAttributeBreakdown,
    SegmentFavoritesOutput,
    SegmentOptionsOutput,
    SegmentPeriod,
    SegmentProfileOutput,
    SegmentShare,
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


def people(teens_g1: int = 2) -> list[tuple[str, dict[str, str], str]]:
    """(가명 buyer_id, 속성, 산 장르). 20대 10명(8명 g1), 10대 10명(teens_g1명 g1, 기본 2),
    모름 6명(g1). 모두 여성."""
    rows = []
    for i in range(10):
        rows.append((f"pseud_a{i:02d}", {"age_band": "20s", "gender": "female"},
                     "g1" if i < 8 else "g2"))  # fmt: skip
    for i in range(10):
        rows.append((f"pseud_b{i:02d}", {"age_band": "10s", "gender": "female"},
                     "g1" if i < teens_g1 else "g2"))  # fmt: skip
    for i in range(6):
        rows.append((f"pseud_c{i:02d}", {}, "g1"))  # 속성 없음 = 모름
    return rows


def small_store(
    customers_error: str | None = None,
    with_customers: bool = True,
    teens_g1: int = 2,
    extra: Sequence[tuple[str, dict[str, str], str]] = (),
    base: Sequence[tuple[str, dict[str, str], str]] | None = None,
) -> SalesStore:
    store = SalesStore(":memory:")
    rows = [*(people(teens_g1) if base is None else base), *extra]
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
    # 10대 g1 5명: 연령 값 칸이 모두 기준 이상이라 숨김 없이 비율을 본다
    out = profile(small_store(teens_g1=5))
    assert out.status == "ok" and out.target_label == "장르1"
    assert out.target_buyers == 19 and out.overall_buyers == 26  # 취소 주문은 세지 않음
    twenties = share(out, "age_band", "20s")
    # 대상 중 아는 사람 13명 중 8명, 전체 중 아는 사람 20명 중 10명 → 0.6154 ÷ 0.5 = 1.2308배
    assert twenties.buyers == 8 and twenties.target_share == 0.6154
    assert twenties.overall_share == 0.5 and twenties.lift == 1.2308
    teens = share(out, "age_band", "10s")
    assert teens.buyers == 5 and teens.target_share == 0.3846 and teens.lift == 0.7692
    assert [a.key for a in out.attributes] == ["age_band", "gender"]  # 카탈로그 순서
    assert [v.value_code for v in out.attributes[0].values] == ["10s", "20s"]
    assert "1.23배" in out.summary and out.basis and out.basis.data_source is None


def test_profile_suppresses_small_groups() -> None:
    out = profile(small_store())
    teens = share(out, "age_band", "10s")  # 대상 중 2명 < 5
    assert teens.suppressed and teens.buyers is None
    assert teens.target_share is None and teens.overall_share is None and teens.lift is None
    male = share(out, "gender", "male")  # 0명
    assert male.suppressed and male.buyers is None
    # 2차 숨김: 값이 둘뿐이라 남은 칸(20대 8명, 여성 10명)을 내면 숨긴 칸 = 합계 − 그 칸이 된다
    for twin in (share(out, "age_band", "20s"), share(out, "gender", "female")):
        assert twin.suppressed and twin.buyers is None and twin.target_share is None
        assert twin.overall_share is None and twin.lift is None
    dump = json.dumps(out.model_dump(mode="json"), ensure_ascii=False)
    assert "pseud_" not in dump  # 구매자 ID는 내지 않는다


def test_profile_counts_unknown_attributes() -> None:
    out = profile(small_store())
    age = next(a for a in out.attributes if a.key == "age_band")
    assert age.unknown_buyers == 6  # 속성 없는 6명 = 모름
    # 카탈로그에 없는 값도 모름이다 (10대 g1 5명: 20대 칸이 2차 숨김에 걸리지 않게)
    store = small_store(teens_g1=5)
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
    # 요약: 첫 문장 = 배수 1위 장르, 둘째 문장 = 구매 비율 1위 장르
    lifted = f"특히 많이 사는 장르는 장르1 (전체 대비 {genres[0].lift:.1f}배, 구매자 중 80.0%)."
    assert lifted in out.summary
    assert out.summary.endswith("가장 많이 산 장르는 장르1 (구매자 중 80.0%).")
    assert out.summary.index("특히") < out.summary.index("가장 많이 산")


def test_favorites_summary_lift_first_then_share() -> None:
    out = SegmentFavoritesOutput(
        filters={"age_band": "20s"}, filter_label="20대", period="90d", segment_buyers=40,
        internal=[
            FavoriteItem(kind="genre", code="a", label="가", share=0.5, lift=1.1, buyers=20),
            FavoriteItem(kind="genre", code="b", label="나", share=0.2, lift=2.0, buyers=8),
            FavoriteItem(kind="category", code="c", label="다", share=0.9, lift=3.0, buyers=36),
        ],
    )  # fmt: skip
    text = seg.favorites_summary(out, 5)
    assert "특히 많이 사는 장르는 나 (전체 대비 2.0배, 구매자 중 20.0%)." in text
    assert text.endswith("가장 많이 산 장르는 가 (구매자 중 50.0%).")  # 분류(다)는 장르가 아님


def test_options_item_noun() -> None:
    assert seg.options(CFG, "test", None).item_noun == "상품"
    game = CFG.model_copy(update={"item_noun": "게임"})
    assert seg.options(game, "test", None).item_noun == "게임"


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


# 남성 구매자 (10대 7명, 20대 6명): 조합 표 네 칸이 모두 기준 이상
MALES = [
    *((f"pseud_m1{i}", {"age_band": "10s", "gender": "male"}, "g2") for i in range(7)),
    *((f"pseud_m2{i}", {"age_band": "20s", "gender": "male"}, "g2") for i in range(6)),
]


def coverage(
    cfg: CatalogSales, conn: KosisConnector, keys=("gender", "age_band"), store=None
) -> MarketCoverageOutput:
    return run(
        seg.coverage_with_population(store or small_store(extra=MALES), TENANT, list(keys), cfg,
                                     "test", NOW, kosis=conn)
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
    assert by["10대 · 남성"].buyers == 7 and by["20대 · 남성"].buyers == 6
    assert "인구 비교는 못 함" in out.summary
    # 남성이 0명이면 남성 칸을 숨기고, 2×2 표라 여성 칸도 숨긴다 (줄 합계로 계산되지 않게)
    alone = coverage(CFG, conn, store=small_store())
    assert all(r.suppressed and r.buyers is None for r in alone.rows)


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
    assert row.coverage == 0.01 and row.population_source == "연령 성별 인구 · 2025년"
    male = by["20대 · 남성"]
    assert male.population == 900 and male.buyers == 6 and male.coverage == round(6 / 900, 8)
    # 고객 구성 지수 (11/17): 구매자 10·7·10·6명(33명), 인구 1000·900·1000·900명(3800명)
    bs, ps = round(10 / 33, 4), round(1000 / 3800, 6)
    assert (row.buyer_share, row.population_share) == (bs, ps)
    assert row.index == round(bs / ps * 100, 1)
    assert male.index == round(round(6 / 33, 4) / round(900 / 3800, 6) * 100, 1)
    top = max(out.rows, key=lambda r: r.index or 0)
    low = min(out.rows, key=lambda r: r.index or 0)
    assert out.summary.startswith("최근 365일 구매자 기준 고객 구성 지수: ")
    assert f"{top.label}은 인구 비중보다 우리 고객 비중이 {top.index / 100:.1f}배" in out.summary
    assert f"{low.label}은 {low.index / 100:.1f}배(지수 {low.index:.0f})로 가장 적음" in out.summary
    assert (seg.topic("20대 · 여성"), seg.topic("청년 플레이어"), seg.topic("PC")) == (
        "20대 · 여성은", "청년 플레이어는", "PC은(는)")  # fmt: skip
    hidden = coverage(cfg, conn, store=small_store())  # 남성 0명 → 네 칸 모두 숨김
    row = {r.label: r for r in hidden.rows}["20대 · 남성"]
    assert row.population == 900 and row.suppressed and row.coverage is None  # 인구는 공공 통계
    assert row.buyer_share is None and row.population_share is None and row.index is None


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


# 행정구역 × 5세 연령 표 모양 (성별이 항목: 남자·여자 인구수, 월별). 코드는 시험용 값
MONTHLY_REF = {
    "org_id": "101", "tbl_id": "T_REGION_AGE5", "itm_id": "IM+IF", "prd_se": "M",
    "obj_l1": "R00", "dims": {"age_band": "c2", "gender": "itm"},
    "codes": {"age_band": {"10s": ["A10", "A15"], "20s": ["A20", "A25"]},
              "gender": {"female": "IF", "male": "IM", "no_answer": []}},
}  # fmt: skip
FIVE = {"A10": 100, "A15": 110, "A20": 120, "A25": 130, "A30": 999}  # 여자 기준, 남자는 +5


def monthly_rows() -> list[dict]:
    rows = []
    for prd, bump in (("202609", 0), ("202610", 1)):
        for age, n in FIVE.items():
            for itm, sex, extra in (("IF", "여자인구수", 0), ("IM", "남자인구수", 5)):
                rows.append({"ORG_ID": "101", "TBL_ID": "T_REGION_AGE5", "TBL_NM": "5세별 인구",
                             "C1": "R00", "C1_NM": "전국", "C2": age, "C2_NM": age,
                             "ITM_ID": itm, "ITM_NM": sex, "UNIT_NM": "명", "PRD_SE": "M",
                             "PRD_DE": prd, "DT": str(n + extra + bump)})  # fmt: skip
    return rows


def test_coverage_five_year_ages_and_gender_items() -> None:
    """5세 코드를 연령대로 묶어 더하고, 성별은 항목(ITM_ID)으로 고른다. 기준은 가장 최근 달."""
    sexes = {"female": "여성", "male": "남성", "no_answer": "응답 안 함"}
    gender = AttributeDef(key="gender", label="성별", values=sexes)
    pop = PopulationSource(attribute_keys=["age_band", "gender"], source_id="kosis",
                           table_ref=MONTHLY_REF)  # fmt: skip
    cfg = CFG.model_copy(update={"population": [pop],
                                 "attributes": [CFG.attributes[0], gender]})  # fmt: skip
    conn, seen = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=monthly_rows()))
    out = coverage(cfg, conn)
    params = dict(seen[0].url.params)
    assert params["objL1"] == "R00" and params["objL2"] == "ALL" and params["itmId"] == "IM+IF"
    assert params["prdSe"] == "M" and params["newEstPrdCnt"] == "1"
    by = {r.label: r for r in out.rows}
    # 202610(최근 달): 20대 여자 = A20 121 + A25 131, 10대 남자 = A10 106 + A15 116
    women = by["20대 · 여성"]
    assert women.population == 252 and women.coverage == round(10 / 252, 8)
    assert by["10대 · 남성"].population == 222 and by["20대 · 남성"].population == 262
    assert by["10대 · 여성"].population == 212  # A30은 어느 연령대에도 들지 않는다
    assert by["20대 · 여성"].population_source == "5세별 인구 · 2026년 10월 말"
    assert by["20대 · 여성"].base_year == 2026
    no_answer = by["20대 · 응답 안 함"]  # 인구 통계에 없는 값 → 비율 없음
    assert no_answer.status == "population_missing" and no_answer.population is None
    assert no_answer.coverage is None
    assert no_answer.reason == "인구 통계에 없는 값이라 비율을 내지 않음"
    # 응답 안 함은 비중·지수도 없다 (구매자 비중의 분모에서도 빠진다)
    assert no_answer.buyer_share is None and no_answer.population_share is None
    assert no_answer.index is None and no_answer.population_note == ""
    assert "2026년 10월 말" in seg.population_note(out)


def test_coverage_bad_dims_or_doubled_cells_are_reasons() -> None:
    bad = PopulationSource(attribute_keys=["age_band", "gender"], source_id="kosis",
                           table_ref={**MONTHLY_REF, "dims": {"age_band": "c2", "gender": "c2"}})
    conn, seen = _kosis(KOSIS_KEY, _no_network)
    out = coverage(CFG.model_copy(update={"population": [bad]}), conn)
    assert seen == [] and all("dims 설정 확인" in r.reason for r in out.rows)
    # 지역을 한 곳으로 고르지 않아 같은 칸이 여러 번 오면 숫자를 내지 않는다
    two_regions = [*monthly_rows(), *({**r, "C1": "R11"} for r in monthly_rows())]
    loose = PopulationSource(attribute_keys=["age_band", "gender"], source_id="kosis",
                             table_ref={**MONTHLY_REF, "obj_l1": "ALL"})  # fmt: skip
    conn2, _ = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=two_regions))
    out2 = coverage(CFG.model_copy(update={"population": [loose]}), conn2)
    assert all(r.population is None and "같은 칸 값이 여러 개" in r.reason for r in out2.rows)


def test_catbooks_population_table_uses_national_five_year_rows() -> None:
    """catbooks 설정(DT_1B04005N): 전국·5세 코드 묶음·성별 항목. 응답은 실제 표 모양의 가짜 값."""
    sales = catalog.load_catalog("catbooks").sales
    assert sales is not None
    pop = sales.population[0]
    ref = pop.table_ref
    assert (ref["org_id"], ref["tbl_id"], ref["obj_l1"]) == ("101", "DT_1B04005N", "00")
    assert ref["codes"]["gender"]["no_answer"] == []
    ages = ["5", "10", "15", "20", "25", "30", "35", "40", "45", "50", "55", "60", "65", "70",
            "75", "80", "85", "90", "95", "100", "105", "0"]  # fmt: skip

    def age_name(code: str) -> str:  # KOSIS 분류 이름 모양 (메타: 15 = 10 - 14세 … 105 = 100+)
        n = int(code)
        return "계" if n == 0 else "100+" if n == 105 else f"{n - 5} - {n - 1}세"

    rows = [
        {"ORG_ID": "101", "TBL_ID": "DT_1B04005N", "TBL_NM": "주민등록인구", "C1": "00",
         "C1_NM": "전국", "C2": age, "C2_NM": age_name(age), "ITM_ID": itm, "ITM_NM": itm,
         "UNIT_NM": "명", "PRD_SE": "M", "PRD_DE": "202610",
         "DT": str(1000 * int(age) + {"T2": 0, "T3": 1, "T4": 2}[itm])}
        for age in ages for itm in ("T2", "T3", "T4")
    ]  # fmt: skip
    conn, seen = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=rows))
    rows_out = run(seg.population_for(pop, [
        CoverageRow(values={"age_band": "20s", "gender": "female"}, label="20대 · 여성", buyers=10),
        CoverageRow(values={"age_band": "50s_plus", "gender": "male"}, label="50대+ · 남성",
                    buyers=10),
        CoverageRow(values={"age_band": "10s", "gender": "no_answer"}, label="10대 · 응답 안 함",
                    buyers=10),
    ], conn))  # fmt: skip
    assert dict(seen[0].url.params)["objL1"] == "00"
    women, older, no_answer = rows_out
    assert women.population == (25000 + 2) + (30000 + 2)  # 20~24세 + 25~29세 여자
    fifty_plus = [55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 105]
    assert older.population == sum(1000 * a + 1 for a in fifty_plus)  # 50세 이상 전부, 남자
    assert women.population_source == "행정안전부 주민등록인구통계(KOSIS) · 2026년 10월 말"
    assert "기준" not in women.population_source and women.population_source.count("년") == 1
    assert no_answer.population is None and no_answer.coverage is None
    # 합산한 5세 구간은 KOSIS 분류 이름에서 (catbooks.toml에 나이 범위를 적지 않는다)
    assert women.population_note == "20~24세 + 25~29세"
    assert older.population_note == "50세 이상 합산"
    assert no_answer.population_note == ""
    assert "세" not in str(pop.table_ref)  # 설정에는 코드만


def test_band_note_from_kosis_names() -> None:
    assert seg.band_note(["10 - 14세", "15 - 19세"]) == "10~14세 + 15~19세"
    assert seg.band_note(["50 - 54세", "95 - 99세", "100세 이상"]) == "50세 이상 합산"
    assert seg.band_note(["55 - 59세", "100+"]) == "55세 이상 합산"
    assert seg.band_note(["20 - 24세", ""]) == ""  # 이름이 빠지면 쓰지 않는다
    assert seg.band_note(["남자"]) == "남자"


def test_coverage_index_hidden_share_cannot_be_solved_from_visible_shares() -> None:
    """보이는 비중들의 합으로 숨긴 묶음 하나의 값을 알아낼 수 없음.

    연령 하나 (10대 10명, 20대 3명, 30대 9명, 응답 안 함 2명, 기타 1명). 인구에 없는 값이 둘이다.
    1차로 20대·응답 안 함·기타를 숨긴다. 전체 합으로는 셋의 합만 나오지만, 비중의 분모(답한
    구매자 22명)가 buyers ÷ buyer_share로 드러나 20대 = 22 − 10 − 9가 된다 → "답한 묶음 전체
    합"도 공개 합계로 보고 하나를 더 숨긴다."""
    ages = {"10s": "10대", "20s": "20대", "30s": "30대", "na": "응답 안 함", "etc": "기타"}
    ref = {"org_id": "101", "tbl_id": "T_AGE", "itm_id": "T1", "prd_se": "Y",
           "codes": {"age_band": {"10s": "A10", "20s": "A20", "30s": "A30", "na": [],
                                  "etc": []}}}  # fmt: skip
    cfg = CFG.model_copy(update={
        "attributes": [AttributeDef(key="age_band", label="연령대", values=ages),
                       CFG.attributes[1]],
        "population": [PopulationSource(attribute_keys=["age_band"], source_id="kosis",
                                        table_ref=ref)],
    })  # fmt: skip
    table = [{"ORG_ID": "101", "TBL_ID": "T_AGE", "TBL_NM": "연령 인구", "C1": c, "C1_NM": c,
              "ITM_ID": "T1", "UNIT_NM": "명", "PRD_SE": "Y", "PRD_DE": "2025", "DT": "1000"}
             for c in ("A10", "A20", "A30")]  # fmt: skip
    conn, _ = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=table))
    f = {"gender": "female"}
    sizes = {"a": ("10s", 10), "b": ("20s", 3), "c": ("30s", 9), "n": ("na", 2), "e": ("etc", 1)}
    base = [b for p, (age, n) in sizes.items() for b in buyers_of(p, n, {"age_band": age, **f})]
    # 보완 숨김 전 모양이면 풀린다: 답한 묶음 합 22 − 보이는 10 − 9 = 3 (확인용)
    groups = seg.coverage_groups([5])
    assert seg.is_determined(1, {1, 3, 4}, [*groups, [0, 1, 2]])
    assert not seg.is_determined(1, {1, 3, 4}, groups)
    out = coverage(cfg, conn, keys=("age_band",), store=small_store(base=base))
    by = {r.label: r for r in out.rows}
    answered = [by[n] for n in ("10대", "20대", "30대")]
    hidden = [r for r in answered if r.suppressed]
    assert len(hidden) >= 2  # 답한 묶음 안에서 숨긴 칸이 하나뿐이면 풀린다
    assert by["30대"].suppressed and not by["10대"].suppressed  # 보이는 칸 중 작은 30대를 더 숨김
    for r in hidden:
        assert r.buyers is None and r.buyer_share is None and r.population_share is None
        assert r.index is None
    visible = [r for r in answered if not r.suppressed]
    # 보이는 비중 합이 1에서 빠지는 몫은 숨긴 묶음 둘 이상에 나뉜다
    assert 1 - sum(r.buyer_share or 0 for r in visible) > 0
    assert by["10대"].buyer_share == round(10 / 22, 4) and by["10대"].index is not None
    assert by["응답 안 함"].index is None and by["응답 안 함"].buyer_share is None


def test_kosis_table_meta() -> None:
    meta = [
        {"OBJ_ID": "A", "OBJ_NM": "행정구역", "ITM_ID": "R00", "ITM_NM": "전국", "UP_ITM_ID": None},
        {"OBJ_ID": "ITEM", "OBJ_NM": "항목", "ITM_ID": "IF", "ITM_NM": "여자인구수"},
    ]
    conn, seen = _kosis(KOSIS_KEY, lambda r: httpx.Response(200, json=meta))
    got = run(conn.table_meta("101", "T_REGION_AGE5"))
    params = dict(seen[0].url.params)
    asked = (params["method"], params["type"], params["tblId"])
    assert asked == ("getMeta", "ITM", "T_REGION_AGE5")
    assert [(m.obj_nm, m.itm_id, m.itm_nm, m.up_itm_id) for m in got] == [
        ("행정구역", "R00", "전국", ""), ("항목", "IF", "여자인구수", "")]
    assert "test-kosis-key" not in repr(conn) and "test-kosis-key" not in repr(got)


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
    # 화면 기간: 1일·7일·30일·3개월 (365d는 시장 커버만 쓴다)
    assert out.periods == ["1d", "7d", "30d", "90d"] and out.min_group_size == sales.min_group_size
    assert out.catalog_id == cat.id and out.catalog_version == sales.version
    empty = seg.options(None, "x", None)
    assert empty.attributes == [] and empty.catalog_id == "x"
    assert empty.periods == ["1d", "7d", "30d", "90d"]


@pytest.mark.parametrize(
    ("period", "start"),
    [
        ("1d", datetime(2026, 11, 15, 15, 0, tzinfo=UTC)),  # 한국 11/16 0시 (오늘)
        ("7d", datetime(2026, 11, 9, 15, 0, tzinfo=UTC)),  # 한국 11/10 0시
        ("30d", datetime(2026, 10, 17, 15, 0, tzinfo=UTC)),
        ("90d", datetime(2026, 8, 18, 15, 0, tzinfo=UTC)),
    ],
)
def test_screen_periods_cut_from_korean_midnight(period: SegmentPeriod, start: datetime) -> None:
    w = seg.segment_window(period, NOW)
    assert (w.start, w.end) == (start, NOW)
    assert w.previous_start == w.previous_end == start  # 직전 비교 없음
    assert period in seg.SEGMENT_LABEL


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
    # 키가 없으면 KOSIS를 부르지 않고 이유만 (인구를 지어내지 않는다)
    assert {r.status for r in cov.rows} == {"error"}
    assert all("UNAUTHORIZED" in r.reason and r.population is None for r in cov.rows)

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


# ---------------------------------------------------------------- 숨긴 값 계산 불가
# 숨긴 값 하나를 다른 숫자로 계산할 수 없음.
# 공개된 숫자만으로 "숨긴 칸들의 합 = 공개 합계 − 보이는 칸 합" 방정식을 세우고(비율 × 합계로
# 계산되는 합계 포함), 숨긴 칸 하나라도 값이 하나로 풀리면 실패한다. 풀이는 seg와 따로 짠다.


def _rank(rows: list[list[Fraction]]) -> int:
    m = [list(r) for r in rows]
    rank = 0
    for col in range(len(m[0]) if m else 0):
        pivot = next((i for i in range(rank, len(m)) if m[i][col]), None)
        if pivot is None:
            continue
        m[rank], m[pivot] = m[pivot], m[rank]
        for i in range(len(m)):
            if i != rank and m[i][col]:
                f = m[i][col] / m[rank][col]
                m[i] = [a - f * b for a, b in zip(m[i], m[rank], strict=True)]
        rank += 1
    return rank


def solved(unknowns: list[str], equations: list[set[str]]) -> set[str]:
    """방정식(각각 "이 미지수들의 합 = 아는 값")으로 값이 하나로 정해지는 미지수."""
    rows = [[Fraction(int(u in eq)) for u in unknowns] for eq in equations if eq & set(unknowns)]
    if not rows:
        return set()
    base = _rank(rows)
    return {
        u for k, u in enumerate(unknowns)
        if _rank([*rows, [Fraction(int(j == k)) for j in range(len(unknowns))]]) == base
    }  # fmt: skip


def profile_leaks(out: SegmentProfileOutput) -> dict[str, set[str]]:
    """속성마다 공개된 숫자로 계산되는 숨긴 칸 (target_buyers, 보이는 buyers·비율, 모름 수)."""
    leaks: dict[str, set[str]] = {}
    for attr in out.attributes:
        hidden = [v.value_code for v in attr.values if v.buyers is None]
        unknowns = hidden + (["모름"] if attr.unknown_buyers is None else [])
        equations: list[set[str]] = []
        if out.target_buyers is not None:  # 값 칸 + 모름 = 대상 구매자
            equations.append(set(unknowns))
        shown = [v for v in attr.values if v.buyers is not None and v.target_share]
        if shown:  # 그 속성을 아는 대상 구매자 = buyers ÷ target_share (비율로 계산)
            known = {round((v.buyers or 0) / (v.target_share or 1)) for v in shown}
            assert len(known) == 1  # 공개 숫자로 정말 계산된다
            equations.append(set(hidden))
            # 보이는 overall_share 합이 1에서 빠지는 몫도 숨긴 값 칸들의 합이다 (같은 방정식 꼴)
        leaks[attr.key] = solved(unknowns, equations)
    return leaks


def coverage_leaks(out: MarketCoverageOutput) -> set[str]:
    """조합 표의 줄 합계·전체 합계를 공개됐다고 보고(가장 나쁜 경우) 계산되는 숨긴 줄."""
    unknowns = [r.label for r in out.rows if r.buyers is None]
    keys = out.attribute_keys
    equations = [set(unknowns)]
    for k in keys:  # 속성 k만 바뀌고 나머지 값이 같은 줄들 = 한 줄 합계
        lines: dict[tuple, set[str]] = {}
        for r in out.rows:
            rest = tuple(r.values[o] for o in keys if o != k)
            lines.setdefault(rest, set()).add(r.label)
        equations += list(lines.values())
    return solved(unknowns, equations)


def test_leak_checker_catches_a_single_hidden_cell() -> None:
    # 2차 숨김이 없을 때의 모양: 20대 8명·모름 6명·대상 16명이면 10대 = 16 − 8 − 6
    values = [
        SegmentShare(value_code="10s", label="10대", suppressed=True),
        SegmentShare(value_code="20s", label="20대", buyers=8, target_share=0.8,
                     overall_share=0.5, lift=1.6),
    ]  # fmt: skip
    age = SegmentAttributeBreakdown(key="age_band", label="연령대", unknown_buyers=6, values=values)
    leaky = SegmentProfileOutput(target_kind="genre", target_code="g1", period="30d",
                                 target_buyers=16, attributes=[age])  # fmt: skip
    assert profile_leaks(leaky) == {"age_band": {"10s"}}
    rows = [CoverageRow(values={"a": a, "b": b}, label=a + b, buyers=None if a + b == "xm" else 9)
            for a in ("x", "y") for b in ("f", "m")]  # fmt: skip
    assert coverage_leaks(MarketCoverageOutput(attribute_keys=["a", "b"], rows=rows)) == {"xm"}


CFG3 = CFG.model_copy(update={"attributes": [
    AttributeDef(key="age_band", label="연령대",
                 values={"10s": "10대", "20s": "20대", "30s": "30대"}),
    CFG.attributes[1],
]})  # fmt: skip


def buyers_of(prefix: str, n: int, attrs: dict[str, str], genre: str = "g1") -> list:
    return [(f"pseud_{prefix}{i:02d}", attrs, genre) for i in range(n)]


def test_profile_hidden_cell_cannot_be_computed_from_other_numbers() -> None:
    # 기본: 10대 2명(1차) → 20대도 숨김 (모름 6명은 보임)
    out = profile(small_store())
    assert profile_leaks(out) == {"age_band": set(), "gender": set()}
    age = next(a for a in out.attributes if a.key == "age_band")
    assert age.unknown_buyers == 6 and all(v.suppressed for v in age.values)

    # 값 셋 중 하나만 작다 → 보이는 칸 중 가장 작은 30대(6명)를 더 숨기고 20대는 낸다
    f = {"gender": "female"}
    three = buyers_of("t", 2, {"age_band": "10s", **f}) + buyers_of(
        "w", 8, {"age_band": "20s", **f}) + buyers_of("h", 6, {"age_band": "30s", **f})
    out3 = profile(small_store(base=three), cfg=CFG3)
    assert profile_leaks(out3) == {"age_band": set(), "gender": set()}
    assert [v.suppressed for v in out3.attributes[0].values] == [True, False, True]
    assert share(out3, "age_band", "20s").buyers == 8
    assert out3.attributes[0].unknown_buyers == 0

    # 모름이 3명(1차 숨김)이면 값 칸이 하나라도 보일 때 모름 = 대상 − buyers ÷ target_share
    # → 그 속성의 값 칸을 모두 숨긴다. 다른 속성(성별)은 그대로 낸다
    unknown3 = buyers_of("t", 7, {"age_band": "10s", **f}) + buyers_of(
        "w", 8, {"age_band": "20s", **f}) + buyers_of("u", 3, f)
    out_u = profile(small_store(base=unknown3))
    assert profile_leaks(out_u) == {"age_band": set(), "gender": set()}
    age_u = next(a for a in out_u.attributes if a.key == "age_band")
    assert age_u.unknown_buyers is None and all(v.suppressed for v in age_u.values)
    assert all(v.target_share is None and v.overall_share is None and v.lift is None
               for v in age_u.values)  # fmt: skip
    assert out_u.target_buyers == 18 and share(out_u, "gender", "female").buyers is None  # 남 0명


def test_coverage_hidden_cell_cannot_be_computed_from_other_numbers() -> None:
    conn, _ = _kosis(KOSIS_KEY, _no_network)
    cfg3 = CFG3.model_copy(update={"population": CFG.population})
    rows = (
        buyers_of("af", 10, {"age_band": "10s", "gender": "female"})
        + buyers_of("am", 7, {"age_band": "10s", "gender": "male"})
        + buyers_of("bf", 10, {"age_band": "20s", "gender": "female"})
        + buyers_of("bm", 3, {"age_band": "20s", "gender": "male"})  # 1차 숨김
        + buyers_of("cf", 8, {"age_band": "30s", "gender": "female"})
        + buyers_of("cm", 9, {"age_band": "30s", "gender": "male"})
    )
    out = coverage(cfg3, conn, store=small_store(base=rows))
    assert coverage_leaks(out) == set()
    by = {r.label: r for r in out.rows}
    # 20대 남성을 감추려면 직사각형(10대·20대 × 여성·남성)이 필요. 30대 줄은 그대로 낸다
    assert [r.label for r in out.rows if r.suppressed] == [
        "10대 · 여성", "10대 · 남성", "20대 · 여성", "20대 · 남성"
    ]
    assert by["30대 · 여성"].buyers == 8 and by["30대 · 남성"].buyers == 9
    assert all(r.coverage is None for r in out.rows if r.suppressed)
    assert coverage_leaks(coverage(CFG, conn, store=small_store())) == set()  # 2×2 모두 숨김


def test_no_hidden_cell_is_computable_on_the_fake_source(fake_store: SalesStore) -> None:
    cat = catalog.load_catalog("catbooks")
    sales = cat.sales
    assert sales is not None
    tight = sales.model_copy(update={"min_group_size": 40})  # 숨김이 많이 생기게
    for cfg in (sales, tight):
        for kind, code in (("genre", "novel"), ("genre", "kids"), ("product", "fk_p016")):
            for period in ("30d", "90d"):
                out = seg.profile(fake_store, TENANT, kind, code, [], period, cfg, cat.id, NOW)
                assert out.status == "ok"
                assert all(not v for v in profile_leaks(out).values()), (kind, code, period)
        kconn, _ = _kosis({}, _no_network)
        cov = run(seg.coverage_with_population(fake_store, TENANT, ["age_band", "gender"], cfg,
                                               cat.id, NOW, kosis=kconn))  # fmt: skip
        assert cov.status == "ok" and coverage_leaks(cov) == set()
    hidden = [r for r in cov.rows if r.suppressed]
    assert hidden and len(hidden) < len(cov.rows)  # 기준 40이면 일부만 숨긴다

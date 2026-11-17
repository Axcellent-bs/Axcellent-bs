"""다른 회사(가짜 게임사)로 바꿔 끼워도 매출·고객·시장 분석이 돌아가는지 (11/16).

카탈로그(`tests/fixtures/game_company.toml`)만 바꾸고, 표준 판매 저장소에는 계약 모양(SalesOrderRow·
CustomerRow)으로 게임사 주문·플레이어를 넣는다. 핸들러 6개(sales_analysis·segment_options·
segment_profile·segment_favorites·market_coverage·event_proposal)가 캣북스 이름 없이 결과를 내고,
이벤트 제안을 보낸 뒤 캠페인 성과가 생기면 다음 제안의 past_results에 예상 vs 실제가 보인다.
바깥 출처·LLM·기획·고객지원은 부르지 않는다(바꿔 끼움).
"""

from __future__ import annotations

import asyncio
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    BasketPairsOutput,
    CampaignResultOutput,
    ChannelValueOutput,
    EventProposalOutput,
    EventProposalSendOutput,
    MarketCoverageOutput,
    MetricCatalog,
    RetentionOutput,
    SalesAnalysisOutput,
    SegmentFavoritesOutput,
    SegmentOptionsOutput,
    SegmentProfileOutput,
)
from ax_contracts.schemas.execute import ExecuteDecision
from data_agent import catalog, handlers
from data_agent.clients import customer_reaction
from data_agent.event_proposal_store import EventProposalStore
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import event_proposal as ep

GAME = Path(__file__).resolve().parent / "fixtures" / "game_company.toml"
CATBOOKS = Path(catalog.CATALOG_DIR) / "catbooks.toml"
TENANT = "shop-demo"  # 요청 meta의 기본 고객사
# (속성, 주로 사는 장르, 가끔 사는 장르, 유입 출처)
PLAYERS = [
    ({"age_band": "young", "platform": "ios"}, "rpg", "puzzle", "appstore"),
    ({"age_band": "teen", "platform": "android"}, "puzzle", "casual", "discord"),
    ({"age_band": "adult", "platform": "pc"}, "casual", "rpg", None),
]
PRICES = {"rpg": 33000, "puzzle": 5500, "casual": 9900}


def run(coro):
    return asyncio.run(coro)


def game_catalog() -> MetricCatalog:
    return catalog.load_catalog(str(GAME))


def game_store(now: datetime) -> SalesStore:
    """플레이어 30명(묶음마다 10명, 8명은 주 장르), 한 사람이 1~3일 전에 한 번 결제."""
    store = SalesStore(":memory:")
    orders: list[SalesOrderRow] = []
    customers: list[CustomerRow] = []
    for g, (attrs, main, side, source) in enumerate(PLAYERS):
        for i in range(10):
            buyer = f"pl{g}_{i}"
            genre = main if i < 8 else side
            when = now - timedelta(days=1 + i % 3)
            price = PRICES[genre]
            orders.append(SalesOrderRow(
                order_id=f"go{g}_{i}", buyer_id=buyer, status="paid", created_at=when,
                updated_at=when, items_amount=price, payment_status="succeeded",
                src_source=source, src_medium="push" if source == "discord" else None,
                lines=[SalesOrderLine(product_id=f"{genre}_pack", category_code="game_item",
                                      title=f"{genre} pack", qty=1, unit_price=price,
                                      genre_code=genre)],
            ))  # fmt: skip
            customers.append(CustomerRow(buyer_id=buyer, joined_at=now - timedelta(days=200),
                                         updated_at=now - timedelta(days=5),
                                         attributes=dict(attrs)))  # fmt: skip
    store.upsert(TENANT, orders, now)
    store.finish_sync(TENANT, "memory", now - timedelta(minutes=1), None)
    store.upsert_customers(TENANT, customers, now)
    store.finish_sync(TENANT, customers_source("memory"), now - timedelta(minutes=1), None)
    return store


def _request(skill: str, **inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input=inp)


def catbooks_words() -> set[str]:
    """캣북스 [sales]의 장르·속성·속성 값 이름 (게임사 결과에 하나도 나오면 안 된다)."""
    with CATBOOKS.open("rb") as f:
        sales = tomllib.load(f)["sales"]
    words = set(sales["genres"].values())
    for attr in sales["attributes"]:
        words.add(attr["label"])
        words.update(attr["values"].values())
    return words


def test_game_catalog_is_a_valid_catalog_with_sales() -> None:
    cat = game_catalog()
    assert cat.id == "pixelforge" and cat.sales is not None
    assert list(cat.sales.genres) == ["rpg", "puzzle", "casual"]
    assert [a.key for a in cat.sales.attributes] == ["age_band", "platform"]
    assert set(cat.sales.attributes[1].values) == {"ios", "android", "pc"}
    assert cat.sales.population[0].table_ref == {}
    assert {e.code for e in cat.sales.event_kinds} == {"item_pack", "login_bonus"}
    assert cat.sales.quick_questions and cat.sales.categories
    # 게임사 설정에는 캣북스 이름이 없다 (있으면 아래 "캣북스 이름이 안 나온다" 확인이 무의미)
    names = {*cat.sales.genres.values(), *(a.label for a in cat.sales.attributes)}
    names |= {v for a in cat.sales.attributes for v in a.values.values()}
    assert not names & catbooks_words()


@pytest.fixture
def game(monkeypatch: pytest.MonkeyPatch):
    """게임사 카탈로그 + 저장소를 핸들러에 끼우고, 바깥 호출은 모두 가짜로."""
    now = datetime.now(UTC)
    planning: list[AgentRequest] = []
    result_keys: list[set[str]] = []
    results: list[CampaignResultOutput] = []

    async def no_past() -> tuple[list[ep.PastCampaign], str | None]:
        return [], None

    async def support(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED,
                           output={"items": [], "sales_read": True})  # fmt: skip

    async def gate(action, params, meta, evidence):  # noqa: ANN001, ANN202, ARG001
        return ExecuteDecision(execution_id="exe_game", grade="A", status="executed")

    async def send(target: str, req: AgentRequest) -> AgentResult:
        planning.append(req)
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, outcome="created",
                           output={"outcome": "created", "proposal_id": "PROP-41"})  # fmt: skip

    async def proposal_results(keys: set[str]) -> list[CampaignResultOutput]:
        result_keys.append(set(keys))
        return list(results)

    customer_reaction.clear_cache()
    monkeypatch.delenv("DATA4LIBRARY_API_KEY", raising=False)
    monkeypatch.delenv("KOSIS_API_KEY", raising=False)
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: None)
    monkeypatch.setattr(handlers, "get_llm", lambda: None)
    monkeypatch.setattr(handlers, "get_past_campaigns", no_past)
    monkeypatch.setattr(handlers, "get_reaction_sender", lambda: support)
    monkeypatch.setattr(handlers, "get_event_proposal_sender", lambda: send)
    monkeypatch.setattr(handlers, "get_event_proposal_gate", lambda: gate)
    monkeypatch.setattr(handlers, "get_proposal_results", proposal_results)
    catalog.use_catalog(game_catalog())
    handlers.use_sales_store(game_store(now))
    handlers.use_event_proposal_store(EventProposalStore(":memory:"))
    try:
        yield {"now": now, "planning": planning, "result_keys": result_keys,
               "results": results}  # fmt: skip
    finally:
        handlers.use_sales_store(None)
        handlers.use_event_proposal_store(None)
        catalog.use_catalog(None)
        customer_reaction.clear_cache()


def _ok(result: AgentResult) -> dict:
    assert result.status == TaskStatus.COMPLETED and result.evidence, result
    assert isinstance(result.output, dict)
    return result.output


def _no_catbooks_words(*outputs: Any) -> None:
    text = repr(outputs)
    leaked = sorted(w for w in catbooks_words() if w in text)
    assert not leaked, f"게임사 결과에 캣북스 이름이 나옴: {leaked}"


def test_sales_analysis_with_game_company(game: dict) -> None:
    out = SalesAnalysisOutput.model_validate(_ok(run(handlers.sales_analysis(
        _request("sales_analysis", period="7d")))))  # fmt: skip
    assert out.status == "ok", out.insufficient_reasons
    assert out.definition.startswith("매출 = 결제 완료 인앱 구매")
    assert out.basis.catalog_id == "pixelforge" and out.basis.catalog_version == "game-1"
    kpis = {k.key: k.value for k in out.kpis}
    assert kpis["order_count"] == 30
    young = 8 * PRICES["rpg"] + 2 * PRICES["puzzle"]
    teen = 8 * PRICES["puzzle"] + 2 * PRICES["casual"]
    adult = 8 * PRICES["casual"] + 2 * PRICES["rpg"]
    assert kpis["revenue"] == young + teen + adult
    assert [c.label for c in out.categories] == ["게임 아이템"]
    channels = {c.label: c.revenue for c in out.channels}
    assert channels == {"앱스토어 검색": young, "디스코드 · 푸시 알림": teen, "직접 유입": adult}
    _no_catbooks_words(out.model_dump(mode="json"))


def test_segment_options_profile_favorites_coverage_with_game_company(game: dict) -> None:
    opts = SegmentOptionsOutput.model_validate(_ok(run(handlers.segment_options(
        _request("segment_options")))))  # fmt: skip
    assert [a.key for a in opts.attributes] == ["age_band", "platform"]
    assert [g.code for g in opts.genres] == ["rpg", "puzzle", "casual"]
    assert [q.label for q in opts.quick_questions] == ["청년 아이폰", "십대 안드로이드"]
    assert {e.code for e in opts.event_kinds} == {"item_pack", "login_bonus"}
    assert opts.population_keys == [["age_band"]] and opts.catalog_id == "pixelforge"
    assert opts.min_group_size == 3

    prof = SegmentProfileOutput.model_validate(_ok(run(handlers.segment_profile(
        _request("segment_profile", target_kind="genre", target_code="rpg", period="30d")))))
    assert prof.status == "ok", prof.insufficient_reasons
    assert prof.target_label == "롤플레잉" and prof.target_buyers == 10
    platform = next(a for a in prof.attributes if a.key == "platform")
    ios = next(v for v in platform.values if v.value_code == "ios")
    assert (ios.label, ios.buyers, ios.target_share) == ("아이폰", 8, 0.8)
    assert ios.lift == pytest.approx(0.8 / (10 / 30), abs=0.01)

    fav = SegmentFavoritesOutput.model_validate(_ok(run(handlers.segment_favorites(
        _request("segment_favorites", filters={"age_band": "teen", "platform": "android"})))))
    assert fav.status == "ok", fav.insufficient_reasons
    assert fav.filter_label == "십대 플레이어 · 안드로이드" and fav.segment_buyers == 10
    top = fav.internal[0]
    assert (top.kind, top.code, top.label, top.buyers) == ("genre", "puzzle", "퍼즐", 8)
    assert fav.external == []  # 게임사 설정에 바깥 출처가 없으면 부르지 않는다

    cov = MarketCoverageOutput.model_validate(_ok(run(handlers.market_coverage(
        _request("market_coverage", attribute_keys=["age_band"])))))  # fmt: skip
    assert cov.status == "ok", cov.insufficient_reasons
    assert [r.label for r in cov.rows] == ["십대 플레이어", "청년 플레이어", "성인 플레이어"]
    assert [r.buyers for r in cov.rows] == [10, 10, 10]
    assert {r.status for r in cov.rows} == {"population_missing"}
    assert all(r.population is None and r.coverage is None for r in cov.rows)
    # 인구가 없으면 비중·지수도 없다 (11/17 고객 구성 지수)
    assert all(r.buyer_share is None and r.population_share is None and r.index is None
               for r in cov.rows)  # fmt: skip
    _no_catbooks_words(*(o.model_dump(mode="json") for o in (opts, prof, fav, cov)))


def test_coverage_index_with_game_company_and_fake_kosis(game: dict) -> None:
    """게임사 설정에 인구표(나이대 하나)를 붙이면 지수·합산 구간이 나온다. 이름은 KOSIS 응답에서."""
    import httpx

    from data_agent.connectors.kosis import KosisConnector
    from data_agent.connectors.naver_shopping import DayCache
    from data_agent.skills import segment_analysis as seg

    ref = {"org_id": "999", "tbl_id": "T_GAME_AGE", "itm_id": "T1", "prd_se": "M",
           "label": "가짜 연령 인구",
           "codes": {"age_band": {"teen": ["K1", "K2"], "young": "K3", "adult": ["K4", "K5"]}}}
    names = {"K1": "10 - 14세", "K2": "15 - 19세", "K3": "20 - 29세", "K4": "30 - 34세",
             "K5": "35세 이상"}
    people = {"K1": 100, "K2": 100, "K3": 200, "K4": 300, "K5": 400}  # 합 1,100명
    table = [{"ORG_ID": "999", "TBL_ID": "T_GAME_AGE", "TBL_NM": "연령 인구", "C1": c,
              "C1_NM": names[c], "ITM_ID": "T1", "UNIT_NM": "명", "PRD_SE": "M",
              "PRD_DE": "202610", "DT": str(n)} for c, n in people.items()]  # fmt: skip
    sales = game_catalog().sales
    assert sales is not None
    pop = sales.population[0].model_copy(update={"table_ref": ref})
    cfg = sales.model_copy(update={"population": [pop]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=table)))  # fmt: skip
    conn = KosisConnector({"KOSIS_API_KEY": "test-kosis-key-0000"}, client=client,
                          cache=DayCache(lambda: game["now"].date()))  # fmt: skip
    out = run(seg.coverage_with_population(handlers.get_sales_store(), TENANT, ["age_band"],
                                           cfg, "pixelforge", game["now"], kosis=conn))  # fmt: skip
    by = {r.label: r for r in out.rows}
    teen, young, adult = by["십대 플레이어"], by["청년 플레이어"], by["성인 플레이어"]
    third = round(10 / 30, 4)  # 묶음마다 10명
    assert [r.buyer_share for r in (teen, young, adult)] == [third] * 3
    assert teen.population_share == round(200 / 1100, 6)
    assert teen.index == round(third / round(200 / 1100, 6) * 100, 1)
    assert adult.index == round(third / round(700 / 1100, 6) * 100, 1)
    assert teen.population_note == "10~14세 + 15~19세"
    assert young.population_note == ""  # 구간 하나는 따로 적지 않는다
    assert adult.population_note == "30세 이상 합산"
    assert teen.population_source == "가짜 연령 인구 · 2026년 10월 말"
    assert out.summary.startswith("최근 365일 구매자 기준 고객 구성 지수: 십대 플레이어는 ")
    assert "성인 플레이어는 0.5배(지수 52)로 가장 적음" in out.summary
    _no_catbooks_words(out.model_dump(mode="json"))


def test_basket_pairs_with_game_company(game: dict) -> None:
    """십대·안드로이드 6명이 퍼즐 말고 롤플레잉도 한 번 더 산다 → (롤플레잉, 퍼즐) 쌍 하나."""
    now: datetime = game["now"]
    when = now - timedelta(days=1)
    extra = [SalesOrderRow(
        order_id=f"gx{i}", buyer_id=f"pl1_{i}", status="paid", created_at=when, updated_at=when,
        items_amount=PRICES["rpg"], payment_status="succeeded",
        lines=[SalesOrderLine(product_id="rpg_pack", category_code="game_item", title="rpg pack",
                              qty=1, unit_price=PRICES["rpg"], genre_code="rpg")],
    ) for i in range(6)]  # fmt: skip
    handlers.get_sales_store().upsert(TENANT, extra, now)

    out = BasketPairsOutput.model_validate(_ok(run(handlers.basket_pairs_skill(
        _request("basket_pairs", period="7d")))))  # fmt: skip
    assert out.status == "ok", out.insufficient_reasons
    (pair,) = out.pairs
    # 롤플레잉 16명(청년 8 + 성인 2 + 십대 6) > 퍼즐 10명 → a = 롤플레잉
    assert (pair.a_label, pair.b_label, pair.together_buyers) == ("롤플레잉", "퍼즐", 6)
    assert pair.lift == round(6 * 30 / (16 * 10), 4) and pair.confidence == round(6 / 16, 4)
    assert "함께 사는 장르" in out.summary

    teens = BasketPairsOutput.model_validate(_ok(run(handlers.basket_pairs_skill(
        _request("basket_pairs", period="7d", filters={"platform": "android"})))))  # fmt: skip
    (pair,) = teens.pairs  # 안드로이드 10명 안: 퍼즐 8명 > 롤플레잉 6명
    assert (pair.a_label, pair.b_label) == ("퍼즐", "롤플레잉")
    assert pair.lift == round(6 * 10 / (8 * 6), 4)

    items = BasketPairsOutput.model_validate(_ok(run(handlers.basket_pairs_skill(
        _request("basket_pairs", level="product", period="7d")))))  # fmt: skip
    assert [(p.a_label, p.b_label) for p in items.pairs] == [("rpg pack", "puzzle pack")]
    assert "함께 사는 게임" in items.summary  # 게임사 카탈로그 item_noun
    _no_catbooks_words(*(o.model_dump(mode="json") for o in (out, teens, items)))


def _propose(month: str) -> EventProposalOutput:
    return EventProposalOutput.model_validate(_ok(run(handlers.event_proposal_skill(
        _request("event_proposal", month=month)))))  # fmt: skip


def test_event_proposal_with_game_company_and_feedback_loop(game: dict) -> None:
    now: datetime = game["now"]
    month = f"{now.year}-{now.month:02d}"
    nxt = f"{now.year + now.month // 12}-{now.month % 12 + 1:02d}"
    out = _propose(month)
    assert out.status == "ok" and out.proposals, out.insufficient_reasons
    genres = {p.genre_code for p in out.proposals}
    assert genres <= {"rpg", "puzzle", "casual"} and len(genres) == len(out.proposals)
    first = out.proposals[0]
    assert first.event_kind == "item_pack" and first.event_label == "아이템 패키지 할인"
    assert first.estimate.assumption and first.estimate.target_customers >= 3
    assert first.past_results == [] and game["result_keys"] == []  # 보낸 제안이 없으면 안 읽는다
    _no_catbooks_words(out.model_dump(mode="json"))

    sent = run(handlers.event_proposal_send(
        _request("event_proposal_send", proposal_id=first.proposal_id)))  # fmt: skip
    assert EventProposalSendOutput.model_validate(_ok(sent)).proposal_ref == "PROP-41"
    assert len(game["planning"]) == 1

    # 캠페인이 아직 없으면: 같은 달 다시 부르면 자기 자신은 past_results에 없다 (sent에 있음)
    again = _propose(month)
    mine = next(p for p in again.proposals if p.proposal_id == first.proposal_id)
    assert mine.sent is not None and mine.past_results == []
    assert game["result_keys"][-1] == {first.proposal_id, "PROP-41"}

    # 기획 PROP-41로 캠페인이 돌고 성과가 생기면: 예상 범위 vs 실제 매출 증분
    start = now - timedelta(days=8)
    game["results"].append(CampaignResultOutput.model_validate({
        "status": "known", "campaign_id": "cmp_game_1", "proposal_id": "PROP-41",
        "window_from": start.isoformat(), "window_to": (start + timedelta(days=7)).isoformat(),
        "sales": {"status": "known", "verdict": {"status": "known", "verdict": "more",
                                                 "label": "더 팔림",
                                                 "incremental_gross_krw": 420_000}},
    }))  # fmt: skip
    again = _propose(month)
    mine = next(p for p in again.proposals if p.proposal_id == first.proposal_id)
    (row,) = mine.past_results
    assert (row.proposal_id, row.proposal_ref, row.campaign_id, row.actual) == (
        first.proposal_id, "PROP-41", "cmp_game_1", 420_000)
    assert (row.expected_low, row.expected_high) == (
        first.estimate.revenue_low, first.estimate.revenue_high)
    assert row.period_from == start and row.period_to == start + timedelta(days=7)

    # 다음 달 같은 조건의 제안에도 지난 제안의 예상 vs 실제가 붙는다
    later = _propose(nxt)
    same = next(p for p in later.proposals if p.genre_code == first.genre_code
                and p.target_filters == first.target_filters)  # fmt: skip
    assert same.proposal_id != first.proposal_id
    assert [(r.proposal_id, r.actual) for r in same.past_results] == [
        (first.proposal_id, 420_000)]


def test_retention_with_game_company(game: dict) -> None:
    """게임사 이름만으로: 오래 전에 두 번 결제하고 그 뒤 안 온 성인 PC 플레이어 6명이 떠날 위험."""
    now: datetime = game["now"]
    store = handlers.get_sales_store()
    old = [now - timedelta(days=120), now - timedelta(days=110)]
    rows = [
        SalesOrderRow(
            order_id=f"old{i}_{k}", buyer_id=f"old{i}", status="paid", created_at=when,
            updated_at=when, items_amount=33000, payment_status="succeeded",
            lines=[SalesOrderLine(product_id="rpg_pack", category_code="game_item",
                                  title="rpg pack", qty=1, unit_price=33000, genre_code="rpg")],
        )
        for i in range(6) for k, when in enumerate(old)
    ]  # fmt: skip
    store.upsert(TENANT, rows, now)
    store.upsert_customers(TENANT, [
        CustomerRow(buyer_id=f"old{i}", joined_at=now - timedelta(days=200),
                    updated_at=now - timedelta(days=200),
                    attributes={"age_band": "adult", "platform": "pc"})
        for i in range(6)
    ], now)  # fmt: skip
    out = RetentionOutput.model_validate(_ok(run(handlers.retention_skill(
        _request("retention", cohorts=6)))))  # fmt: skip
    assert out.status == "ok", out.insufficient_reasons
    assert out.churn_days == 60
    cohort = next(c for c in out.cohorts if c.customers)  # 120일 전 달에 6명 첫 구매
    assert cohort.customers == 6 and cohort.r30 == 1.0  # 10일 뒤 다시 결제
    top = out.at_risk[0]
    assert (top.label, top.regulars, top.at_risk, top.top_item_label) == (
        "성인 플레이어 · PC", 6, 6, "롤플레잉")
    _no_catbooks_words(out.model_dump(mode="json"))


def test_channel_value_with_game_company(game: dict) -> None:
    """게임사 유입 출처 이름으로: 120일 전 앱스토어로 처음 결제한 6명의 90일 가치."""
    now: datetime = game["now"]
    store = handlers.get_sales_store()
    rows = [
        SalesOrderRow(
            order_id=f"cv{i}_{k}", buyer_id=f"cv{i}", status="paid", created_at=when,
            updated_at=when, items_amount=33000, payment_status="succeeded",
            src_source="appstore" if k == 0 else None,
            lines=[SalesOrderLine(product_id="rpg_pack", category_code="game_item",
                                  title="rpg pack", qty=1, unit_price=33000, genre_code="rpg")],
        )
        for i in range(6)
        for k, when in enumerate([now - timedelta(days=120), now - timedelta(days=100)])
    ]  # fmt: skip
    store.upsert(TENANT, rows, now)
    store.upsert_customers(TENANT, [
        CustomerRow(buyer_id=f"cv{i}", joined_at=now - timedelta(days=200),
                    updated_at=now - timedelta(days=200), attributes={})
        for i in range(6)
    ], now)  # fmt: skip
    result = run(handlers.channel_value_skill(_request("channel_value")))
    out = ChannelValueOutput.model_validate(_ok(result))
    assert out.status == "ok", out.insufficient_reasons
    shown = [r for r in out.rows if not r.suppressed]
    assert [(r.label, r.new_customers, r.revenue_per_customer, r.repeat_rate) for r in shown] == [
        ("앱스토어 검색", 6, 66000, 1.0)]
    assert result.evidence and "뺀 새 고객 30명" in result.evidence.data_summary  # 최근 첫 결제
    _no_catbooks_words(out.model_dump(mode="json"))

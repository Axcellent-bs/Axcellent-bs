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
    CampaignResultOutput,
    EventProposalOutput,
    EventProposalSendOutput,
    MarketCoverageOutput,
    MetricCatalog,
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
    _no_catbooks_words(*(o.model_dump(mode="json") for o in (opts, prof, fav, cov)))


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

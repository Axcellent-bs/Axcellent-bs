"""함께 사는 장르·상품 (`basket_pairs`) · 묶음 할인 제안 (11/17).

표준 판매 저장소는 `:memory:`, 숫자는 테스트 전용 가짜 주문에서 나온다(네트워크·LLM 없음).
SQL과 예전 파이썬 방식의 비교·30만 행 성능은 `test_segment_sql.py`에 있다.

시험 자료 (최근 3일, 한 사람당 주문 1건, 줄마다 10,000원)
- x 10명 (20대·여성): g1 + g2
- y 10명 (30대·남성): g3
- z 5명 (20대·남성): g1
- w 3명 (30대·여성): g2 + g3  → 함께 산 사람 3명 (기준 5명 미만이라 숨김)
전체 구매자 28명, g1 15명, g2 13명, g3 13명
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    AttributeDef,
    BasketPairsOutput,
    CatalogSales,
    EventKind,
    EventProposalOutput,
    ProposalSent,
)
from data_agent import handlers
from data_agent.event_proposal_store import EventProposalStore, StoredProposal
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import basket_pairs as bp
from data_agent.skills import event_proposal as ep
from data_agent.skills import segment_analysis as seg

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
COUPON = EventKind(code="genre_coupon", label="장르 쿠폰", discount_rate=0.15,
                   conversion_low=0.03, conversion_high=0.07)  # fmt: skip
BUNDLE = EventKind(code=ep.BUNDLE_KIND, label="묶음 할인", discount_rate=0.10,
                   conversion_low=0.02, conversion_high=0.05)  # fmt: skip
CFG = CatalogSales(
    min_period_orders=1,
    categories={"book": "도서"},
    genres={"g1": "장르1", "g2": "장르2", "g3": "장르3"},
    attributes=[
        AttributeDef(key="age_band", label="연령대", values={"20s": "20대", "30s": "30대"}),
        AttributeDef(key="gender", label="성별", values={"female": "여성", "male": "남성"}),
    ],
    event_kinds=[COUPON, BUNDLE],
    item_noun="책",
    min_orders_low=0,
)
PEOPLE = [
    ("x", 10, {"age_band": "20s", "gender": "female"}, ["g1", "g2"]),
    ("y", 10, {"age_band": "30s", "gender": "male"}, ["g3"]),
    ("z", 5, {"age_band": "20s", "gender": "male"}, ["g1"]),
    ("w", 3, {"age_band": "30s", "gender": "female"}, ["g2", "g3"]),
]


def run(coro):
    return asyncio.run(coro)


def rows(
    extra: list[tuple[str, int, dict[str, str], list[str]]] = (),
) -> tuple[list[SalesOrderRow], list[CustomerRow]]:
    day = NOW - timedelta(days=3)
    orders, customers = [], []
    for name, count, attrs, genres in [*PEOPLE, *extra]:
        for i in range(count):
            buyer = f"{name}{i}"
            lines = [SalesOrderLine(product_id=f"p_{g}", category_code="book", title=f"책 {g}",
                                    qty=1, unit_price=10000, genre_code=g)
                     for g in genres]  # fmt: skip
            orders.append(SalesOrderRow(
                order_id=f"o_{buyer}", buyer_id=buyer, status="paid", created_at=day,
                updated_at=day, items_amount=10000 * len(genres), payment_status="succeeded",
                lines=lines,
            ))  # fmt: skip
            customers.append(CustomerRow(buyer_id=buyer, joined_at=day, updated_at=day,
                                         attributes=dict(attrs)))  # fmt: skip
    return orders, customers


def make_store(extra: list[tuple[str, int, dict[str, str], list[str]]] = ()) -> SalesStore:
    orders, customers = rows(extra)
    store = SalesStore(":memory:")
    store.upsert(TENANT, orders, NOW)
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    store.upsert_customers(TENANT, customers, NOW)
    store.finish_sync(TENANT, customers_source("shop-api"), NOW, None)
    return store


def basket(store: SalesStore, level: str = "genre", filters: dict | None = None,
           max_pairs: int = 10, cfg: CatalogSales = CFG) -> BasketPairsOutput:  # fmt: skip
    return bp.basket(store, TENANT, level, "90d", filters or {}, max_pairs, cfg, "test", NOW)


# ---------------------------------------------------------------- 쌍·배수·숨김


def test_pairs_lift_confidence_direction_and_order() -> None:
    out = basket(make_store())
    assert out.status == "ok" and out.basis is not None
    first, hidden = out.pairs
    # a = 산 사람이 더 많은 쪽 (g1 15명 > g2 13명)
    assert (first.a_code, first.b_code) == ("g1", "g2")
    assert (first.a_label, first.b_label) == ("장르1", "장르2")
    assert (first.together_buyers, first.a_buyers, first.b_buyers) == (10, 15, 13)
    assert first.confidence == round(10 / 15, 4)
    assert first.lift == round(10 * 28 / (15 * 13), 4)  # 함께 × 전체 ÷ (a × b)
    assert not first.suppressed
    # 함께 산 사람 3명 < 5명: 숫자 칸 모두 비움, 숨기지 않은 쌍 뒤
    assert hidden.suppressed and (hidden.a_code, hidden.b_code) == ("g2", "g3")
    assert hidden.together_buyers is hidden.a_buyers is hidden.confidence is hidden.lift is None
    assert "장르1 + 장르2" in out.summary and "1.2배 이상인 쌍 1개" in out.summary
    assert basket(make_store(), max_pairs=1).pairs == [first]


def test_filters_count_only_inside_the_group() -> None:
    store = make_store()
    women = basket(store, filters={"gender": "female"})  # x 10명 + w 3명
    (pair, _) = women.pairs
    assert (pair.a_code, pair.b_code) == ("g2", "g1")  # 여성 안에서는 g2 13명 > g1 10명
    assert (pair.together_buyers, pair.a_buyers, pair.b_buyers) == (10, 13, 10)
    assert pair.lift == 1.0 and pair.confidence == round(10 / 13, 4)
    assert women.summary.startswith("최근 90일 여성 구매자 13명")
    none = basket(store, filters={"age_band": "30s", "gender": "male"})
    assert none.status == "ok" and none.pairs == [] and "쌍이 없음" in none.summary
    tiny = basket(store, filters={"age_band": "30s", "gender": "female"})  # 3명
    assert tiny.pairs == [] and "5명 미만이라 숫자를 숨김" in tiny.summary


def test_product_level_and_category_is_not_a_pair() -> None:
    out = basket(make_store(), level="product")
    first = out.pairs[0]
    assert (first.a_code, first.b_code) == ("p_g1", "p_g2")
    assert (first.a_label, first.b_label) == ("책 g1", "책 g2")
    assert first.lift == round(10 * 28 / (15 * 13), 4)
    assert "함께 사는 책" in out.summary  # 카탈로그 item_noun
    codes = {c for p in out.pairs for c in (p.a_code, p.b_code)}
    assert "book" not in codes  # 대분류는 쌍에 없다


def test_unknown_genres_are_left_out_of_pairs() -> None:
    store = make_store([("v", 6, {"age_band": "20s", "gender": "female"}, ["g1", "ghost"])])
    out = basket(store)
    codes = {c for p in out.pairs for c in (p.a_code, p.b_code)}
    assert "ghost" not in codes and out.quality.unknown_genres == ["ghost"]


def test_bad_input_and_quality_gates_are_insufficient() -> None:
    bad = basket(make_store(), filters={"job": "x"})
    assert bad.status == "insufficient" and bad.pairs == []
    assert any("설정에 없는 속성: job" in r for r in bad.insufficient_reasons)
    assert bad.summary.startswith("최근 90일") and "숫자를 내지 않음" in bad.summary
    no_genres = basket(make_store(), cfg=CFG.model_copy(update={"genres": {}}))
    assert "설정에 장르(genres)가 없음" in no_genres.insufficient_reasons


class NoOrderReads(SalesStore):
    """고객 기록 관문에 걸리면 주문 집계를 부르면 안 된다."""

    def segment_session(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("관문에 걸렸는데 집계를 부르면 안 된다")


def test_no_customers_stops_before_reading_orders() -> None:
    store = NoOrderReads(":memory:")
    store.upsert(TENANT, rows()[0], NOW)
    store.finish_sync(TENANT, "shop-api", NOW, None)
    out = basket(store)
    assert out.status == "insufficient" and seg.NO_CUSTOMERS in out.insufficient_reasons
    assert out.quality.rows_in_period == 28


def _request(skill: str, **inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input=inp)


def test_handler_rejects_other_tenant_and_bad_input() -> None:
    other = run(handlers.basket_pairs_skill(_request("basket_pairs", tenant_id="other")))
    assert other.status == TaskStatus.FAILED and other.outcome == "invalid_input"
    big = run(handlers.basket_pairs_skill(_request("basket_pairs", max_pairs=21)))
    assert big.status == TaskStatus.FAILED and big.outcome == "invalid_input"
    assert handlers.HANDLERS["basket_pairs"] is handlers.basket_pairs_skill


# ---------------------------------------------------------------- 묶음 할인 제안


def propose(store: SalesStore, cfg: CatalogSales = CFG, max_items: int = 5) -> EventProposalOutput:
    return run(ep.propose(store, EventProposalStore(":memory:"), TENANT, "2026-12", max_items,
                          cfg, "test", NOW)).output  # fmt: skip


def test_bundle_candidate_from_pairs() -> None:
    out = propose(make_store())
    assert out.status == "ok"
    bundles = [p for p in out.proposals if p.bundle is not None]
    (item,) = bundles  # g2+g3는 숨긴 쌍이라 후보가 아니다
    assert item.event_kind == ep.BUNDLE_KIND and item.event_label == "묶음 할인"
    assert item.item_label == "장르1 + 장르2" and item.genre_code is None
    b = item.bundle
    assert (b.a_code, b.b_code, b.lift, b.together_buyers) == ("g1", "g2", round(280 / 195, 4), 10)
    # 누구 = 둘 다 산 사람이 가장 많은 묶음 (20대 10명 = 여성 10명 = 20대·여성 10명 → 카탈로그 순서)
    assert item.target_filters == {"age_band": "20s"} and item.target_label == "20대"
    est = item.estimate
    assert est.target_customers == 15  # 20대의 g1 구매자 (x 10명 + z 5명)
    assert est.aov == 20000  # g1 객단가 + g2 객단가
    assert est.orders_low == round(15 * 0.02) and est.revenue_high == round(15 * 0.05) * 20000
    assert item.score == round(round(280 / 195, 4) * 1.0 * 10 / 30, 4)
    assert len(item.reasons) == 3 and item.reasons[0].kind == "segment"
    assert item.reasons[0].text.startswith("장르1 → 장르2 함께 산 배수 1.44배")
    assert item.reasons[0].value == 1.44
    assert any("같은 사람이 두 장르를 모두 산" in line for line in item.limits)
    assert "장르1와 장르2는" in (item.summary or "")
    # 장르 후보는 묶음 할인 종류를 쓰지 않는다
    genre = [p for p in out.proposals if p.bundle is None]
    assert genre and {p.event_kind for p in genre} == {"genre_coupon"}
    assert all(p.genre_code in CFG.genres for p in genre)
    EventProposalOutput.model_validate(out.model_dump(mode="json"))


def test_bundle_proposal_id_is_stable_and_send_facts_validate() -> None:
    first = [p for p in propose(make_store()).proposals if p.bundle][0]
    again = [p for p in propose(make_store()).proposals if p.bundle][0]
    assert first.proposal_id == again.proposal_id
    assert first.proposal_id != ep.proposal_id(TENANT, "2026-12", first.target_filters, "g1",
                                               ep.BUNDLE_KIND)  # fmt: skip
    insight = ep.build_insight(StoredProposal(first, [("p_g1", "책 g1")], "k", False), NOW)
    labels = [f.label for f in insight.facts]
    assert labels[0].startswith(ep.BUNDLE_REASON_LABEL)
    assert "대상 고객 (장르1 구매자)" in labels and "객단가 (두 장르 합)" in labels


def test_no_bundle_kind_no_bundle_and_bundle_only_has_no_genre_candidates() -> None:
    plain = propose(make_store(), CFG.model_copy(update={"event_kinds": [COUPON]}))
    assert plain.proposals and all(p.bundle is None for p in plain.proposals)
    only = propose(make_store(), CFG.model_copy(update={"event_kinds": [BUNDLE]}))
    assert [p.event_kind for p in only.proposals] == [ep.BUNDLE_KIND]


def test_weak_pairs_are_not_bundle_candidates() -> None:
    # 모두가 g1·g2를 같이 사면 배수 = 1 (< 1.2)
    store = make_store()
    base = ep.read_base(store, TENANT, CFG, "test", NOW)
    assert [c.key for c in base.candidates if c.bundle] == ["g1+g2"]
    flat = CFG.model_copy(update={"min_group_size": 11})  # 함께 산 10명도 숨김 → 후보 없음
    assert not [c for c in ep.read_base(store, TENANT, flat, "test", NOW).candidates if c.bundle]


@pytest.mark.parametrize("bundle", [True, False])
def test_past_results_match_bundle_pair(bundle: bool) -> None:
    item = [p for p in propose(make_store()).proposals if p.bundle][0]
    sent = item.model_copy(update={
        "proposal_id": "evp-old", "timing_basis": item.timing_basis.model_copy(
            update={"month": "2026-11"}),
        "sent": ProposalSent(sent_at=NOW, outcome="created", proposal_ref="PROP-1"),
    })  # fmt: skip
    if not bundle:  # 쌍이 다르면 같은 조건이 아니다
        sent = sent.model_copy(update={"bundle": sent.bundle.model_copy(update={"b_code": "g3"})})
    rows = ep.past_results([StoredProposal(sent, [], "k", False)], item)
    assert [r.proposal_id for r in rows] == (["evp-old"] if bundle else [])

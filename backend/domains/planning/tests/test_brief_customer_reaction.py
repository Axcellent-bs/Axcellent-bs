"""데이터 브리프 고객 반응(customer_reaction) → 기획 근거 줄, 상품 ID(product_ids) 옮기기."""

from __future__ import annotations

import pytest

from ax_contracts.schemas.data import TrendInsight, TrendInsightBriefOutput
from ax_contracts.schemas.planning import CampaignBriefInput, ReceiveInsightInput
from planning_agent.proposals import brief_facts, from_brief

SRC = "trend_insight_brief:book_issue_stats"


def _reaction(pid: str, name: str, rate: float | None = 0.025, note: str | None = None) -> dict:
    return {"product_id": pid, "product_name": name, "period": "month", "claim_count": 3,
            "defect_count": 1, "sold_qty": 120, "claim_rate": rate, "note": note}  # fmt: skip


def _insight(evidence: dict, products: list[tuple[str, str]] | None = None) -> TrendInsight:
    return TrendInsight.model_validate({
        "insight_id": "i1", "rank": 1, "topic": "고양이", "line": "고양이 검색이 평소의 2배",
        "evidence": evidence,
        "products": [{"product_id": p, "name": n, "reason": "r"} for p, n in (products or [])],
    })  # fmt: skip


@pytest.mark.role("planning")
def test_reaction_line_per_product() -> None:
    over = _reaction("p2", "냥이 달력", rate=1.5, note="접수 기간 기준")
    first = _reaction("p1", "고양이 그림책")
    t = _insight({"naver_ratio": 2.0, "customer_reaction": [first, over]})
    facts = brief_facts(t)
    got = [(f.label, f.value) for f in facts if f.source == SRC]
    assert got == [
        ("고객 반응 · 고양이 그림책 (최근 한 달)", "클레임 3건 · 판매 120권 중 2.5%"),
        ("고객 반응 · 냥이 달력 (최근 한 달)", "클레임 3건 · 판매 120권 중 150% (접수 기간 기준)"),
    ]
    assert facts[0].value == "2배"  # 트렌드 근거가 앞, 고객 반응은 뒤


@pytest.mark.role("planning")
def test_reaction_without_rate() -> None:
    t = _insight({"naver_ratio": 2.0, "customer_reaction": [_reaction("p1", "책", rate=None)]})
    (line,) = [f for f in brief_facts(t) if f.source == SRC]
    assert line.value == "클레임 3건 · 판매 120권"


@pytest.mark.role("planning")
def test_cap_twelve_drops_reaction_lines_first() -> None:
    # 트렌드 근거 7줄 + 고객 반응 7줄 → 12줄, 잘리는 것은 고객 반응 뒤쪽 2줄
    t = _insight({
        "naver_ratio": 2.0, "google_rank": 3, "youtube_videos": 4, "youtube_views_per_hour": 900,
        "season_keyword": "겨울", "season_ratio": 1.4,
        "customer_reaction": [_reaction(f"p{n}", f"책{n}") for n in range(7)],
    }).model_copy(update={
        "target_age": "20대", "target_age_ratio": 1.8,
        "shorts_format": "챌린지", "shorts_format_median_views": 15000,
    })  # fmt: skip
    facts = brief_facts(t)
    assert len(facts) == 12
    trend = [f for f in facts if f.source != SRC]
    assert len(trend) == 7
    assert [f.label for f in facts if f.source == SRC] == [
        f"고객 반응 · 책{n} (최근 한 달)" for n in range(5)
    ]


@pytest.mark.role("planning")
def test_reaction_alone_does_not_make_insight() -> None:
    t = _insight({"customer_reaction": [_reaction("p1", "책")]})
    assert brief_facts(t) == []


@pytest.mark.role("planning")
def test_from_brief_fills_product_ids_in_order() -> None:
    out = TrendInsightBriefOutput(insights=[
        _insight({"naver_ratio": 2.0}, [(f"p{n}", f"책{n}") for n in range(12)]),
    ])  # fmt: skip
    (inp,) = from_brief(out)
    assert inp.products == [f"책{n}" for n in range(10)]
    assert inp.product_ids == [f"p{n}" for n in range(10)]


@pytest.mark.role("planning")
def test_old_brief_without_new_fields_converts_the_same() -> None:
    old = {"insights": [{"insight_id": "i1", "rank": 1, "topic": "고양이", "line": "x",
                         "evidence": {"naver_ratio": 2.0}}]}  # fmt: skip
    (inp,) = from_brief(TrendInsightBriefOutput.model_validate(old))
    assert [(f.label, f.value) for f in inp.facts] == [('"고양이" 네이버 검색 평소 대비', "2배")]
    assert inp.products == [] and inp.product_ids == []


@pytest.mark.role("planning")
def test_contracts_accept_old_payloads() -> None:
    fact = {"label": "a", "value": "b", "source": "c"}
    inp = ReceiveInsightInput.model_validate(
        {"insight_id": "i", "title": "t", "summary": "s", "skill": "x", "facts": [fact]}
    )
    assert inp.product_ids == []
    brief = CampaignBriefInput.model_validate(
        {"proposal_id": "PROP-1", "title": "t", "idea": "i", "why": "w", "approved_by": "사람"}
    )
    assert brief.product_ids == []
    with pytest.raises(ValueError):
        ReceiveInsightInput.model_validate(
            {"insight_id": "i", "title": "t", "summary": "s", "skill": "x", "facts": [fact],
             "product_ids": [f"p{n}" for n in range(11)]}  # fmt: skip
        )


@pytest.mark.role("planning")
def test_from_brief_carries_content_category_and_format() -> None:
    """데이터분석이 채운 영상 카테고리·형식(#1333)을 그대로 옮긴다. 없으면 None (11/18)."""
    t = _insight({"naver_ratio": 2.0})
    out = TrendInsightBriefOutput(insights=[
        t.model_copy(update={"content_category": "챌린지", "content_format": "shorts"}),
        t.model_copy(update={"insight_id": "i2"}),
    ])  # fmt: skip
    got = [(i.content_category, i.content_format) for i in from_brief(out)]
    assert got == [("챌린지", "shorts"), (None, None)]

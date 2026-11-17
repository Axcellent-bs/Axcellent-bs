"""콘텐츠 카테고리별 제작비 (11/17, 멘토 피드백: 캠페인 예산을 부서가 같이 정한다).

예: 아재개그·몸개그 쇼츠를 만들었더니 아재개그 조회가 잘 나온다 → 재무가 조회당 제작비를 보고
다음 회차 제작비를 옮기자는 의견을 낸다(CMP-007, INS-005). 재무는 정하지 않고 의견만 낸다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ax_contracts.schemas.finance import CampaignBudgetReviewInput, CampaignResultInput
from finance_agent import campaign as cp
from finance_agent.handlers import reset_state, state
from finance_agent.main import app
from finance_agent.sources import MockSources


class _AnyStarted(dict):
    """모든 캠페인이 시작된 것으로 둔다 (test_campaign_finance와 같은 방식)."""

    def __contains__(self, key: object) -> bool:
        return True

    def __missing__(self, key: str) -> dict:
        return {"campaign_id": key, "total_krw": 10**12, "active_krw": 0, "month": "2026-11"}


def _sender(state_: Any) -> list[dict]:
    got: list[dict] = []

    async def send(opinion: dict, meta: Any) -> str | None:
        got.append(opinion)
        return None

    state_.opinion_sender = send
    return got


def _body(skill: str, data: dict, token: str) -> dict:
    from datetime import UTC, datetime, timedelta

    return {
        "meta": {
            "context_id": "ctx_cmp_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": data,
        "untrusted": {},
    }


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    reset_state(MockSources())
    state().budget_override = {"amount_krw": 1_000_000, "basis": "시험", "provisional": True}
    state().started_override = _AnyStarted()


def _shorts(
    cid: str,
    category: str,
    production: int | None,
    views: int | None,
    point: str | None = "7d",
    gmv: int | None = None,
    period_to: str = "2026-11-10",
) -> dict[str, Any]:
    return {
        "campaign_id": cid,
        "campaign_name": f"{category} 쇼츠 {cid}",
        "channel": "유튜브",
        "period_from": "2026-11-03",
        "period_to": period_to,
        "spend_krw": 0,
        "gmv_krw": gmv,
        "source": "data.video_performance",
        "content_category": category,
        "format": "shorts",
        "production_cost_krw": production,
        "views": views,
        "views_point": point,
    }


def _record(*rows: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        r["campaign_id"]: cp.compute_result(CampaignResultInput(**r).model_dump()) for r in rows
    }


# ------------------------------------------------------------------ 계약 (추가만)
def test_old_requests_still_parse_and_new_fields_are_optional() -> None:
    old = CampaignResultInput(**{**_shorts("c1", "아재개그", 1, 1), **_strip()})
    assert old.content_category is None and old.production_cost_krw is None and old.views is None
    ask = CampaignBudgetReviewInput(
        campaign_id="c", campaign_name="n", channel="유튜브", requested_krw=1
    )
    assert ask.content_category is None and ask.format is None
    with pytest.raises(ValidationError):  # 측정 시점은 데이터 video_performance와 같은 4개뿐
        CampaignResultInput(**_shorts("c1", "아재개그", 1, 1, point="2d"))
    with pytest.raises(ValidationError):
        CampaignResultInput(**_shorts("c1", "아재개그", -1, 1))


def _strip() -> dict[str, Any]:
    return dict.fromkeys(
        ("content_category", "format", "production_cost_krw", "views", "views_point")
    )


# ------------------------------------------------------------------ CMP-007 조회당 제작비
@pytest.mark.parametrize(
    ("production", "views", "point", "status"),
    [
        (100_000, 10_000, "7d", "measured"),
        (100_000, 10_000, "1d", "measuring"),  # 7일 전 조회수로는 판단하지 않는다
        (100_000, None, None, "no_views"),
        (None, 10_000, "7d", "no_production_cost"),
        (100_000, 0, "7d", "zero_views"),
    ],
)
def test_cost_per_view_only_from_the_seven_day_count(
    production: int | None, views: int | None, point: str | None, status: str
) -> None:
    got = cp.view_cost(_shorts("c1", "아재개그", production, views, point))
    assert got["view_cost_status"] == status
    assert got["cost_per_view"] == (10.0 if status == "measured" else None)


def test_production_cost_is_a_campaign_cost_next_to_ad_spend() -> None:
    row = {**_shorts("c1", "아재개그", 200_000, 10_000, gmv=10_000_000), "spend_krw": 100_000}
    record = cp.compute_result(CampaignResultInput(**row).model_dump())
    # 수수료 10% 1,000,000 − (광고비 100,000 + 제작비 200,000 + PG 3.1% 310,000)
    assert record["cost_krw"] == 100_000 + 200_000 + record["pg_fee_krw"]
    assert record["profit_krw"] == record["revenue_krw"] - record["cost_krw"]
    assert record["expense_classes"]["production_cost_krw"] == "광고선전비(제작비)"
    assert "제작비" in record["applied_rules"][1]["detail"]
    # 제작비가 없는 옛 결과는 비용 식이 그대로다
    old = cp.compute_result(CampaignResultInput(**{**row, **_strip()}).model_dump())
    assert old["cost_krw"] == 100_000 + old["pg_fee_krw"]
    assert "제작비" not in old["applied_rules"][1]["detail"]


def test_categories_sum_cost_and_views_and_count_measuring() -> None:
    campaigns = _record(
        _shorts("a1", "아재개그", 100_000, 20_000, period_to="2026-11-01"),
        _shorts("a2", "아재개그", 150_000, 30_000, period_to="2026-11-08"),
        _shorts("b1", "몸개그", 100_000, 2_500),
        _shorts("b2", "몸개그", 100_000, 900, point="3d"),  # 측정 중
        {**_shorts("x", "", 1, 1), "content_category": None},  # 카테고리 없는 캠페인은 빠진다
    )
    rows = {r["content_category"]: r for r in cp.category_view_costs(campaigns)}
    assert set(rows) == {"아재개그", "몸개그"}
    assert rows["아재개그"]["cost_per_view"] == 5.0  # 250,000 ÷ 50,000 (평균의 평균이 아님)
    assert rows["아재개그"]["latest_production_krw"] == 150_000
    assert rows["아재개그"]["format"] == "shorts"
    assert (rows["몸개그"]["cost_per_view"], rows["몸개그"]["measuring"]) == (40.0, 1)


# ------------------------------------------------------------------ INS-005 재배분 의견
def test_budget_shift_moves_production_cost_to_the_cheaper_category() -> None:
    campaigns = _record(
        _shorts("a1", "아재개그", 100_000, 20_000),  # 조회당 5원
        _shorts("b1", "몸개그", 100_000, 2_500),  # 조회당 40원
        _shorts("c1", "말장난", 100_000, 10_000),  # 조회당 10원
    )
    shift = cp.budget_shift(campaigns)
    assert shift is not None and shift["kind"] == "budget_shift"
    # 싼 쪽 +30%, 그만큼 비싼 쪽 −. 총 제작비는 그대로다
    assert shift["shift"] == {
        "from": "몸개그",
        "to": "아재개그",
        "amount_krw": 30_000,
        "ratio": 8.0,
    }
    assert "130,000원" in shift["recommendation"] and "70,000원" in shift["recommendation"]
    assert [r["content_category"] for r in shift["categories"]] == ["아재개그", "말장난", "몸개그"]
    assert {r["rule_id"] for r in shift["applied_rules"]} == {"CMP-007", "INS-005"}
    assert shift in cp.insights(campaigns, [], None, cp.date(2026, 11, 17))


@pytest.mark.parametrize(
    "rows",
    [
        # 한 카테고리만 있으면 비교하지 않는다
        [_shorts("a1", "아재개그", 100_000, 20_000)],
        # 차이가 2배보다 작으면 옮기자고 하지 않는다
        [_shorts("a1", "아재개그", 100_000, 20_000), _shorts("b1", "몸개그", 100_000, 12_000)],
        # 7일이 안 된 조회수로는 비교하지 않는다
        [_shorts("a1", "아재개그", 100_000, 20_000), _shorts("b1", "몸개그", 100_000, 100, "1d")],
    ],
)
def test_no_budget_shift_without_enough_measured_difference(rows: list[dict[str, Any]]) -> None:
    assert cp.budget_shift(_record(*rows)) is None


def test_shift_never_takes_more_than_the_expensive_category_has() -> None:
    campaigns = _record(
        _shorts("a1", "아재개그", 1_000_000, 1_000_000),  # 1원
        _shorts("b1", "몸개그", 50_000, 1_000),  # 50원, 옮길 300,000원보다 적다
    )
    shift = cp.budget_shift(campaigns)
    assert shift is not None and shift["shift"]["amount_krw"] == 50_000


# ------------------------------------------------------------------ A2A 흐름과 화면
def test_seven_day_views_send_the_shift_to_orchestrator_even_without_sales() -> None:
    client = TestClient(app)
    got = _sender(state())
    for row in (_shorts("a1", "아재개그", 100_000, 1_000, point="1d"),):
        out = client.post("/a2a", json=_body("record_campaign_result", row, "mock:marketing"))
        assert out.json()["output"]["view_cost_status"] == "measuring"
    assert got == []  # 측정 중에는 아무것도 내지 않는다

    for row in (
        _shorts("a1", "아재개그", 100_000, 20_000),  # 같은 캠페인 7일 값으로 다시 기록
        _shorts("b1", "몸개그", 100_000, 2_500),
    ):
        out = client.post("/a2a", json=_body("record_campaign_result", row, "mock:marketing"))
        assert out.json()["output"]["status"] == "waiting_revenue"  # 판매액은 아직 모른다
    shifts = [o for o in got if o["verdict"] == "budget_shift"]
    assert len(shifts) == 1 and shifts[0]["kind"] == "insight"
    assert any("조회당 5" in r for r in shifts[0]["reasons"])

    view = client.get("/api/finance/campaigns").json()["data"]
    assert [c["content_category"] for c in view["categories"]] == ["아재개그", "몸개그"]
    insights = client.get("/api/finance/insights").json()["data"]
    assert any(i["kind"] == "budget_shift" for i in insights)


# ------------------------------------------------------------- 기획으로 보내기 (receive_insight)
def _planning_sender(state_: Any, reply: dict[str, Any]) -> list[dict]:
    got: list[dict] = []

    async def send(payload: dict, meta: Any) -> dict[str, Any]:
        got.append(payload)
        return reply

    state_.planning_insight_sender = send
    return got


def _two_categories(client: TestClient) -> None:
    for row in (
        _shorts("a1", "아재개그", 100_000, 20_000),
        _shorts("b1", "몸개그", 100_000, 2_500),
    ):
        client.post("/a2a", json=_body("record_campaign_result", row, "mock:marketing"))


def test_budget_shift_goes_to_planning_once_with_finance_facts() -> None:
    from ax_contracts.schemas.planning import ReceiveInsightInput

    _sender(state())
    sent = _planning_sender(
        state(), {"problem": None, "outcome": "created", "proposal_id": "PROP-7"}
    )
    client = TestClient(app)
    _two_categories(client)
    assert len(sent) == 1
    payload = ReceiveInsightInput.model_validate(sent[0])  # 기획 계약 그대로
    assert payload.skill == "finance_insights" and payload.insight_id.startswith("fin_budget_shift")
    values = {f.label: f.value for f in payload.facts}
    assert (
        values["아재개그 조회당 제작비"] == "5.0원" and values["몸개그 조회당 제작비"] == "40.0원"
    )
    assert all(f.source == "finance_insights" for f in payload.facts)
    # 제작비를 올릴 쪽 카테고리와 그 형식이 기획 제안 칸으로 간다 (기획 #1212)
    assert (payload.content_category, payload.content_format) == ("아재개그", "shorts")
    # 같은 인사이트를 다시 내도 기획에는 한 번만 간다
    client.post(
        "/a2a",
        json=_body("record_campaign_result", _shorts("c1", "말장난", 1, None), "mock:marketing"),
    )
    assert len(sent) == 1
    view = client.get("/api/finance/campaigns").json()["data"]
    [row] = view["planning_insights"]
    assert (row["status"], row["proposal_id"]) == ("delivered", "PROP-7")


def test_planning_down_keeps_it_pending_and_retries() -> None:
    import asyncio

    from finance_agent import handlers

    _sender(state())
    _planning_sender(state(), {"problem": "unavailable"})
    _two_categories(TestClient(app))
    [row] = state().records.planning_insights
    assert (row["status"], row["last_problem"]) == ("pending", "unavailable")
    _planning_sender(state(), {"problem": None, "outcome": "created", "proposal_id": "PROP-8"})
    assert asyncio.run(handlers.retry_opinions()) >= 1
    assert (row["status"], row["proposal_id"]) == ("delivered", "PROP-8")


def test_safety_lets_finance_send_insights_to_planning() -> None:
    from ax_safety.rules import load_registry

    action = next(a for a in load_registry().actions if a.action == "receive_insight")
    assert "finance" in action.requesters and action.owner == "planning"


def test_resending_without_views_keeps_the_views_already_recorded() -> None:
    _sender(state())
    client = TestClient(app)
    first = _shorts("a1", "아재개그", 100_000, 20_000)
    client.post("/a2a", json=_body("record_campaign_result", first, "mock:marketing"))
    # 마케팅이 나중에 판매액만 채워 다시 보낸다(카테고리·조회수 칸은 비움)
    later = {**first, **_strip(), "gmv_krw": 1_000_000}
    out = client.post("/a2a", json=_body("record_campaign_result", later, "mock:marketing"))
    got = out.json()["output"]
    assert (got["views"], got["views_point"], got["cost_per_view"]) == (20_000, "7d", 5.0)
    assert got["content_category"] == "아재개그" and got["status"] == "recorded"
    # 새 값이 오면 새 값을 쓴다
    newer = {**first, "views": 25_000}
    got = client.post("/a2a", json=_body("record_campaign_result", newer, "mock:marketing")).json()[
        "output"
    ]
    assert got["views"] == 25_000


def test_result_without_category_takes_it_from_the_budget_request() -> None:
    """화면에서 시작할 때 적은 카테고리·형식을, 결과에 비어 오면 재무가 잇는다."""
    _sender(state())
    client = TestClient(app)
    state().cash_override = 10_000_000
    ask = {
        "campaign_id": "a1",
        "campaign_name": "아재개그 쇼츠",
        "channel": "유튜브",
        "requested_krw": 100_000,
        "content_category": "아재개그",
        "format": "shorts",
    }
    review = client.post("/a2a", json=_body("review_campaign_budget", ask, "mock:marketing"))
    assert review.json()["status"] == "completed"
    assert state().records.budget_reviews[-1]["content_category"] == "아재개그"
    row = {**_shorts("a1", "x", 100_000, 20_000), "content_category": None, "format": None}
    got = client.post("/a2a", json=_body("record_campaign_result", row, "mock:marketing")).json()[
        "output"
    ]
    assert (got["content_category"], got["format"]) == ("아재개그", "shorts")
    # 결과에 카테고리를 적으면 결과 값이 이긴다
    row2 = {**_shorts("a1", "몸개그", 100_000, 20_000)}
    got = client.post("/a2a", json=_body("record_campaign_result", row2, "mock:marketing")).json()[
        "output"
    ]
    assert got["content_category"] == "몸개그"

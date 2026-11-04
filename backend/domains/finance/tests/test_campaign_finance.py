"""캠페인 손익, 재무 브레이크, 실패 교훈, 인사이트, 시뮬레이션."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas.finance import CampaignResultInput
from finance_agent import campaign as cp
from finance_agent import handlers
from finance_agent.handlers import reset_state, state
from finance_agent.main import app
from finance_agent.simulation.engine import funnel, load_scenario, render_report, run_scenario
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 15)
RULE_IDS = {"BRK-001", "BRK-002", "BRK-003", "BRK-004", "BRK-005"}


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    reset_state(MockSources())
    # 예산 계산 자체는 아래 전용 시험에서 본다. 나머지는 장부에 흔들리지 않게 고정한다
    state().budget_override = {"amount_krw": 1_000_000, "basis": "시험", "provisional": True}


def _result(cid: str = "c1", gmv: int | None = 1_000_000, spend: int = 100_000, **kw: Any) -> dict:
    return {
        "campaign_id": cid,
        "campaign_name": f"캠페인 {cid}",
        "channel": kw.pop("channel", "릴스"),
        "period_from": "2026-10-01",
        "period_to": kw.pop("period_to", "2026-10-05"),
        "spend_krw": spend,
        "coupon_krw": kw.pop("coupon", 0),
        "gmv_krw": gmv,
        "source": "test",
        **kw,
    }


def _ask(requested: int = 100_000, cid: str = "new", channel: str = "릴스") -> dict:
    return {
        "campaign_id": cid,
        "campaign_name": f"요청 {cid}",
        "channel": channel,
        "requested_krw": requested,
    }


def _decide(
    ask: dict | None = None,
    campaigns: dict | None = None,
    reviews: list | None = None,
    lessons: list | None = None,
    cash: int | None = 10_000_000,
    monthly_budget: dict | None = None,
) -> dict:
    return cp.decide(
        ask or _ask(),
        campaigns=campaigns or {},
        reviews=reviews or [],
        lessons=lessons or [],
        cash_net_krw=cash,
        today=TODAY,
        monthly_budget=monthly_budget,
    )


def _bad(cid: str, day: str = "2026-10-05", spend: int = 100_000) -> dict:
    return cp.compute_result(_result(cid, gmv=0, spend=spend, period_to=day))


# ---------------------------------------------------------------- 손익
def test_profit_uses_commission_not_sales() -> None:
    """판매액 500만 원, 광고비 40만 원. ROAS 12.5배여도 수수료 기준으로는 적자다."""
    r = cp.compute_result(_result(gmv=5_000_000, spend=400_000, coupon=10_000))
    assert r["revenue_krw"] == 500_000  # 10%
    assert r["pg_fee_krw"] == 155_000  # 3.1%
    assert r["cost_krw"] == 565_000
    assert r["profit_krw"] == -65_000
    assert r["roas"] == 12.5 and r["verdict"] == "bad"
    assert r["break_even_roas"] == 14.49
    assert {x["rule_id"] for x in r["applied_rules"]} >= {
        "CMP-001",
        "CMP-002",
        "CMP-003",
        "CMP-004",
    }
    assert all(x["basis"] for x in r["applied_rules"])


@pytest.mark.parametrize(
    ("gmv", "expected"),
    [(1_600_000, "neutral"), (1_800_000, "good"), (1_000_000, "bad")],
)
def test_verdict_thresholds(gmv: int, expected: str) -> None:
    assert cp.compute_result(_result(gmv=gmv))["verdict"] == expected


def test_unknown_sales_are_not_judged_or_zero_filled() -> None:
    r = cp.compute_result(_result(gmv=None))
    assert (r["status"], r["profit_krw"], r["verdict"]) == ("waiting_revenue", None, None)


def test_opinion_has_the_agreed_fields() -> None:
    r = cp.compute_result(_result(gmv=1_800_000))
    from datetime import UTC, datetime

    op = cp.opinion_of(r, datetime(2026, 10, 15, tzinfo=UTC))
    for key in ("campaign_name", "spend_krw", "revenue_krw", "profit_krw", "roi", "verdict"):
        assert op[key] is not None, key
    assert op["reasons"] and op["recommendation"] and op["applied_rules"]


# ---------------------------------------------------------------- 브레이크
def test_clean_request_is_approved_with_all_rules_shown() -> None:
    d = _decide()
    assert (d["decision"], d["approved_limit_krw"]) == ("approved", 100_000)
    assert {r["rule_id"] for r in d["applied_rules"]} == RULE_IDS


def test_over_budget_is_conditional_down_to_remaining() -> None:
    d = _decide(_ask(1_200_000))
    assert (d["decision"], d["approved_limit_krw"]) == ("conditional", 1_000_000)
    assert "남은 금액" in d["reasons"][0]


def test_budget_counts_spent_and_approved_but_unfinished() -> None:
    done = {"a": cp.compute_result(_result("a", gmv=1_800_000, spend=600_000))}
    pending = [
        {
            **_decide(_ask(300_000, "b")),
            "reviewed_at": "2026-10-10T00:00:00Z",
            "started_krw": 300_000,
        }
    ]
    d = _decide(_ask(200_000), campaigns=done, reviews=pending)
    assert d["remaining_budget_krw"] == 100_000
    assert (d["decision"], d["approved_limit_krw"]) == ("conditional", 100_000)


def test_re_review_of_same_campaign_does_not_count_itself() -> None:
    earlier = [{**_decide(_ask(900_000)), "reviewed_at": "2026-10-10T00:00:00Z"}]
    assert _decide(_ask(900_000), reviews=earlier)["decision"] == "approved"


def test_almost_no_budget_is_rejected() -> None:
    done = {"a": cp.compute_result(_result("a", gmv=1_800_000, spend=960_000))}
    assert _decide(campaigns=done)["decision"] == "rejected"


@pytest.mark.parametrize("cash", [0, -5])
def test_no_cash_is_rejected(cash: int) -> None:
    assert _decide(cash=cash)["decision"] == "rejected"


def test_unknown_cash_allows_only_the_minimum() -> None:
    """실제 모드에서 이력이 짧으면 현금을 모른다. 전부 거절하지 않고 최소 금액까지만 승인한다."""
    d = _decide(_ask(300_000), cash=None)
    assert (d["decision"], d["approved_limit_krw"]) == ("conditional", 50_000)
    assert "확인할 수 없음" in d["reasons"][0]
    small = _decide(_ask(50_000), cash=None)
    assert (small["decision"], small["approved_limit_krw"]) == ("approved", 50_000)


def test_low_cash_limits_the_request() -> None:
    d = _decide(_ask(300_000), cash=120_000)
    assert (d["decision"], d["approved_limit_krw"]) == ("conditional", 120_000)


def test_loss_streak_rejects_but_other_channel_passes() -> None:
    bad = {"a": _bad("a", "2026-09-01", 10_000), "b": _bad("b", "2026-09-05", 10_000)}
    assert _decide(campaigns=bad)["decision"] == "rejected"
    assert _decide(_ask(channel="검색"), campaigns=bad)["decision"] == "approved"


def test_good_result_breaks_the_streak() -> None:
    rows = {
        "a": _bad("a", "2026-09-01", 10_000),
        "b": cp.compute_result(_result("b", gmv=1_800_000, period_to="2026-09-03")),
        "c": _bad("c", "2026-09-05", 10_000),
    }
    assert _decide(campaigns=rows)["decision"] == "approved"


def test_monthly_loss_limit_rejects() -> None:
    rows = {"a": _bad("a", spend=500_000)}
    d = _decide(_ask(channel="검색"), campaigns=rows)
    assert d["decision"] == "rejected" and "손실" in d["reasons"][0]


def test_marketing_request_never_touches_coupon_or_refund() -> None:
    """재무 브레이크는 캠페인 예산만 본다. 쿠폰·환불 판단 skill은 재무에 없다."""
    assert "issue_coupon" not in handlers.HANDLERS and "refund_request" not in handlers.HANDLERS


# ---------------------------------------------------------------- 교훈
def test_lesson_cuts_limit_then_good_result_restores_it() -> None:
    lessons: list[dict] = []
    bad = _bad("a", "2026-09-01", 100_000)
    from datetime import UTC, datetime

    at = datetime(2026, 10, 1, tzinfo=UTC)
    assert cp.apply_lessons(bad, lessons, at) == "added"
    assert cp.apply_lessons(bad, lessons, at) is None  # 같은 캠페인은 한 번만
    cut = _decide(_ask(100_000), campaigns={"a": bad}, lessons=lessons)
    assert (cut["decision"], cut["approved_limit_krw"]) == ("conditional", 50_000)
    assert "LRN-001" in {r["rule_id"] for r in cut["applied_rules"]}

    good = cp.compute_result(_result("b", gmv=1_800_000, period_to="2026-09-10"))
    assert cp.apply_lessons(good, lessons, at) == "closed"
    back = _decide(_ask(100_000), campaigns={"a": bad, "b": good}, lessons=lessons)
    assert (back["decision"], back["approved_limit_krw"]) == ("approved", 100_000)


def test_lesson_floor() -> None:
    lessons = [
        {"channel": "릴스", "status": "open", "campaign_name": f"c{i}", "spend_krw": 200_000}
        for i in range(5)
    ]
    assert _decide(_ask(200_000), lessons=lessons)["approved_limit_krw"] == 50_000


def test_compare_shows_decision_changed_by_history() -> None:
    bad = _bad("a", "2026-09-01", 100_000)
    lessons: list[dict] = []
    from datetime import UTC, datetime

    cp.apply_lessons(bad, lessons, datetime(2026, 10, 1, tzinfo=UTC))
    out = cp.compare_with_history(
        _ask(100_000),
        campaigns={"a": bad},
        reviews=[],
        lessons=lessons,
        cash_net_krw=10_000_000,
        today=TODAY,
    )
    assert out["changed"] is True
    assert out["before"]["decision"] == "approved" and out["after"]["decision"] == "conditional"


# ---------------------------------------------------------------- 인사이트
def test_insight_channel_decline_even_when_all_are_profitable() -> None:
    rows = {
        c: cp.compute_result(_result(c, gmv=g, period_to=f"2026-10-0{i + 1}"))
        for i, (c, g) in enumerate([("a", 2_600_000), ("b", 2_200_000), ("c", 1_900_000)])
    }
    assert all(r["profit_krw"] > 0 for r in rows.values())
    found = cp.insights(rows, [], None, TODAY)
    decline = next(i for i in found if i["kind"] == "channel_decline")
    assert len(decline["evidence"]) == 3 and decline["applied_rules"][0]["rule_id"] == "INS-001"


def test_insight_below_break_even_has_numbers() -> None:
    rows = {"a": cp.compute_result(_result("a", gmv=1_000_000))}
    item = next(i for i in cp.insights(rows, [], None, TODAY) if i["kind"] == "below_break_even")
    assert {e["label"] for e in item["evidence"]} >= {"실제 ROAS", "손익분기 ROAS"}


def test_insight_repeat_overrun() -> None:
    reviews = [
        {
            "campaign_id": "a",
            "campaign_name": "a",
            "decision": "conditional",
            "approved_limit_krw": 50_000,
            "requested_krw": 200_000,
            "remaining_budget_krw": 100_000,
            "reviewed_at": "2026-10-01T00:00:00Z",
        }
    ]
    rows = {"a": cp.compute_result(_result("a", gmv=1_800_000, spend=100_000))}
    item = next(i for i in cp.insights(rows, reviews, None, TODAY) if i["kind"] == "repeat_overrun")
    assert len(item["evidence"]) == 2


@pytest.mark.parametrize(("due", "fires"), [("2026-10-25", True), ("2027-01-25", False)])
def test_insight_tax_deadline(due: str, fires: bool) -> None:
    pack = {"filing": {"kind": "예정 신고", "due": due}, "payable": 100, "checks": ["약관 확인"]}
    found = [i for i in cp.insights({}, [], pack, TODAY) if i["kind"] == "tax_deadline"]
    assert bool(found) is fires


def test_no_data_no_insights() -> None:
    assert cp.insights({}, [], None, TODAY) == []


# ---------------------------------------------------------------- skill과 의견 전달
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


def _sender(state_: Any, problem: str | None = None) -> list[dict]:
    got: list[dict] = []

    async def send(opinion: dict, meta: Any) -> str | None:
        got.append(opinion)
        return problem

    state_.opinion_sender = send
    return got


def test_skill_acl_and_record_flow() -> None:
    client = TestClient(app)
    state().cash_override = 10_000_000
    got = _sender(state())
    blocked = client.post("/a2a", json=_body("review_campaign_budget", _ask(), "mock:hr"))
    assert blocked.json()["status"] != "completed"
    reviewed = client.post("/a2a", json=_body("review_campaign_budget", _ask(), "mock:marketing"))
    assert reviewed.json()["output"]["decision"] == "approved"
    assert got == []  # 승인은 총괄에 알리지 않는다

    done = client.post(
        "/a2a", json=_body("record_campaign_result", _result("new", gmv=0), "mock:marketing")
    ).json()
    assert done["output"]["verdict"] == "bad" and done["output"]["lesson"] == "added"
    assert [o["kind"] for o in got][0] == "campaign_result"
    again = client.post(
        "/a2a", json=_body("record_campaign_result", _result("new", gmv=0), "mock:marketing")
    ).json()
    assert again["output"]["write"] == "already_recorded"
    assert len(state().records.lessons) == 1


def test_rejection_goes_to_orchestrator_as_opinion() -> None:
    state().cash_override = None
    got = _sender(state())

    async def no_cash() -> None:
        return None

    state().cash_override = 0
    asyncio.run(handlers.review_budget(handlers.CampaignBudgetReviewInput(**_ask()), "ctx"))
    assert got[0]["kind"] == "budget_decision" and got[0]["verdict"] == "거절"
    assert got[0]["reasons"] and got[0]["applied_rules"]


def test_undelivered_opinion_stays_pending_and_retries() -> None:
    got = _sender(state(), problem="unknown_skill")
    state().cash_override = 10_000_000
    data = CampaignResultInput(**_result("a", gmv=0))
    asyncio.run(handlers.record_result(data, "ctx"))
    row = state().records.opinions[0]
    assert (row["status"], row["last_problem"]) == ("pending", "unknown_skill")
    _sender(state())
    assert asyncio.run(handlers.retry_opinions()) >= 1
    assert row["status"] == "delivered" and got


def test_sender_crash_does_not_break_the_judgment() -> None:
    async def boom(opinion: dict, meta: Any) -> None:
        raise ConnectionError

    state().opinion_sender = boom
    record = asyncio.run(handlers.record_result(CampaignResultInput(**_result("a", gmv=0)), "ctx"))
    assert record["verdict"] == "bad"
    assert state().records.opinions[0]["last_problem"] == "ConnectionError"


def _verify(amount: int, params: dict) -> dict:
    client = TestClient(app)
    data = {
        "execution_id": "exe_1",
        "action": "start_campaign",
        "requester": "marketing",
        "params": params,
        "computed_amount": amount,
    }
    return client.post("/a2a", json=_body("verify_campaign_budget", data, "mock:safety")).json()


def test_gate_verification_uses_the_approved_limit() -> None:
    _sender(state())
    state().cash_override = 10_000_000
    params = {"campaign_id": "new", "campaign_name": "n", "channel": "릴스"}
    bad = cp.compute_result(_result("old", gmv=0, spend=100_000, period_to="2026-09-01"))
    state().records.campaigns["old"] = bad
    state().records.lessons.append(
        {
            "channel": "릴스",
            "status": "open",
            "campaign_name": "old",
            "campaign_id": "old",
            "spend_krw": 100_000,
        }
    )
    client = TestClient(app)
    client.post("/a2a", json=_body("review_campaign_budget", _ask(100_000), "mock:marketing"))
    assert _verify(50_000, params)["output"]["decision"] == "approved"
    over = _verify(100_000, params)["output"]
    assert over["decision"] == "rejected" and over["approved_limit_krw"] == 50_000
    assert _verify(1000, {"campaign_id": "x"})["output"]["reason_code"] == "invalid_params"


def test_campaign_api_is_honest_when_empty() -> None:
    data = TestClient(app).get("/api/finance/campaigns").json()["data"]
    assert data["campaigns"] == [] and data["budget"]["provisional"] is True
    assert data["break_even_roas"] == 14.49


# ---------------------------------------------------------------- 시뮬레이션 (완료 기준)
def test_funnel() -> None:
    r = {"cpc_krw": 150, "ctr": 0.06, "cvr": 0.09, "aov_krw": 32000}
    assert funnel(r, 300_000) == {
        "impressions": 33333,
        "clicks": 2000,
        "orders": 180,
        "gmv_krw": 5_760_000,
        "coupon_krw": 0,
    }


def test_scenario_files_can_be_swapped_and_load() -> None:
    assert load_scenario("a")["id"] == "a" and load_scenario("b")["id"] == "b"


def test_scenario_a_profits_without_brake() -> None:
    result = asyncio.run(run_scenario("a"))
    assert result["summary"]["total_profit_krw"] > 0 and result["summary"]["brake_count"] == 0
    assert [r["decision"] for r in result["rounds"]] == ["approved", "approved", "conditional"]
    assert result["summary"]["remaining_krw"] == 0
    assert "channel_decline" in {i["kind"] for i in result["insights"]}


def test_scenario_b_loses_and_brake_fires_and_history_changes_decision() -> None:
    result = asyncio.run(run_scenario("b"))
    rounds = result["rounds"]
    assert result["summary"]["total_profit_krw"] < 0 and result["summary"]["brake_count"] >= 1
    assert rounds[0]["decision"] == "approved"
    assert (rounds[1]["decision"], rounds[1]["approved_limit_krw"]) == ("conditional", 200_000)
    assert (
        rounds[1]["compare"]["changed"] and rounds[1]["compare"]["before"]["decision"] == "approved"
    )
    assert rounds[2]["decision"] == "rejected" and "spend_krw" not in rounds[2]
    assert "below_break_even" in {i["kind"] for i in result["insights"]}


@pytest.mark.parametrize("name", ["a", "b"])
def test_scenario_logs_full_flow_and_every_judgment_has_basis(name: str) -> None:
    result = asyncio.run(run_scenario(name))
    actors = [s["actor"] for s in result["log"]]
    for actor in ("마케팅", "데이터분석", "기획", "개발", "재무", "총괄", "인사"):
        assert actor in actors
    for step in result["log"]:
        assert step["kind"] == ("실제 코드" if step["actor"] == "재무" else "대역")
        assert step["received"] and step["decided"] and step["sent_to"] and step["sent"]
        if step["actor"] == "재무":
            assert step["applied_rules"]
    assert all(rule["basis"] for r in result["rounds"] for rule in r["rules"])
    assert "대역" in render_report([result])


def test_simulation_leaves_real_state_alone() -> None:
    state().records.campaigns["real"] = {"marker": True}
    before = state()
    asyncio.run(run_scenario("b"))
    assert state() is before and state().records.campaigns == {"real": {"marker": True}}


# ---------------------------------------------------------------- 재구매·월 예산
def _orders(kit: Any, buyers: int, repeat: bool, day: str = "2026-03-01") -> list[dict]:
    at, order = kit.at, kit.order
    rows = []
    for n in range(buyers):
        buyer = f"buy_{n}"
        rows.append(order(f"o{n}", at(day), [("s", "book", 1, 10_000)], buyer=buyer))
        if repeat:
            rows.append(order(f"r{n}", at("2026-03-20"), [("s", "book", 1, 5_000)], buyer=buyer))
    return rows


def _books(orders: list[dict]) -> Any:
    from finance_agent.books import Books

    return Books(source="shop-api", mock=False, orders=orders)


def test_repeat_multiple_comes_from_books(kit: Any) -> None:
    got = cp.repeat_estimate(_books(_orders(kit, 30, True)), TODAY)
    assert got is not None and got["multiple"] == 1.5 and got["repeat_buyers"] == 30


def test_repeat_is_not_invented_when_cohort_is_small_or_recent(kit: Any) -> None:
    assert cp.repeat_estimate(_books(_orders(kit, 29, True)), TODAY) is None
    assert cp.repeat_estimate(_books(_orders(kit, 30, False, "2026-10-01")), TODAY) is None


def test_repeat_outside_window_does_not_count(kit: Any) -> None:
    rows = _orders(kit, 30, False)
    rows += [
        kit.order(f"late{n}", kit.at("2026-09-01"), [("s", "book", 1, 5_000)], buyer=f"buy_{n}")
        for n in range(30)
    ]
    assert cp.repeat_estimate(_books(rows), TODAY)["multiple"] == 1.0


def test_ltv_is_reference_only_and_does_not_change_the_verdict() -> None:
    data = _result(gmv=1_000_000, spend=100_000)
    plain = cp.compute_result(data)
    with_repeat = cp.compute_result(data, {"multiple": 2.0})
    assert plain["ltv_profit_krw"] is None
    assert with_repeat["ltv_profit_krw"] == 200_000 - 62_000 - 100_000
    assert with_repeat["verdict"] == plain["verdict"] == "bad"
    assert with_repeat["profit_krw"] == plain["profit_krw"]
    assert "재구매" in cp.recommendation(with_repeat)
    assert "CMP-005" in {r["rule_id"] for r in with_repeat["applied_rules"]}


def test_budget_follows_last_month_result(kit: Any) -> None:
    rows = [kit.order("o1", kit.at("2026-09-10"), [("s", "book", 1, 1_000_000)], buyer="b")]
    got = cp.budget_from_last_month(_books(rows), TODAY)
    # 수수료 100,000 − PG 31,000 = 69,000의 30%
    assert (got["amount_krw"], got["provisional"]) == (20_700, False)
    assert "2026-09" in got["basis"]


def test_budget_falls_back_only_when_there_is_no_data() -> None:
    from finance_agent.books import Books

    empty = cp.budget_from_last_month(_books([]), TODAY)
    assert (empty["amount_krw"], empty["provisional"]) == (1_000_000, True)
    missing = Books(source="shop-api", mock=False, unavailable=["orders"])
    assert cp.budget_from_last_month(missing, TODAY)["provisional"] is True


def test_loss_last_month_means_no_budget() -> None:
    d = _decide(monthly_budget={"amount_krw": 0, "basis": "적자", "provisional": False})
    assert d["decision"] == "rejected"


def test_small_budget_limits_the_request() -> None:
    budget = {"amount_krw": 80_000, "basis": "직전 달", "provisional": False}
    d = _decide(_ask(100_000), monthly_budget=budget)
    assert (d["decision"], d["approved_limit_krw"]) == ("conditional", 80_000)


def test_scenario_reports_short_term_and_repeat_profit() -> None:
    a = asyncio.run(run_scenario("a"))["summary"]
    assert a["total_ltv_profit_krw"] > a["total_profit_krw"] > 0
    b = asyncio.run(run_scenario("b"))["summary"]
    assert b["total_ltv_profit_krw"] < 0


# ---------------------------------------------------------------- 검토에서 나온 문제
def test_lowered_request_after_failure_is_not_cut_again() -> None:
    """실패 이력 채널이 한도만큼 낮춰 내면 승인된다. 요청액 기준이면 끝없이 깎였다."""
    bad = _bad("a", "2026-09-01", 100_000)
    lessons = [{**bad, "status": "open", "lesson_id": "l"}]
    first = _decide(_ask(100_000), campaigns={"a": bad}, lessons=lessons)
    assert (first["decision"], first["approved_limit_krw"]) == ("conditional", 50_000)
    lowered = _decide(_ask(50_000), campaigns={"a": bad}, lessons=lessons)
    assert (lowered["decision"], lowered["approved_limit_krw"]) == ("approved", 50_000)


def test_reviews_that_never_started_do_not_hold_budget() -> None:
    """사람이 거절했거나 시작하지 않은 건이 이달 예산을 잡지 않는다."""
    unstarted = [{**_decide(_ask(800_000, "b")), "reviewed_at": "2026-10-10T00:00:00Z"}]
    d = _decide(_ask(400_000), reviews=unstarted)
    assert (d["decision"], d["remaining_budget_krw"]) == ("approved", 1_000_000)


def test_gate_verification_holds_budget_and_rechecks_at_start() -> None:
    """관문 검증을 통과하면 예산을 잡고, 그 뒤 다른 캠페인은 줄어든 예산으로 판단한다."""
    _sender(state())
    state().cash_override = 10_000_000
    one = {"campaign_id": "one", "campaign_name": "n", "channel": "검색"}
    two = {"campaign_id": "two", "campaign_name": "n", "channel": "검색"}
    ask = _ask(600_000, "two", "검색")
    TestClient(app).post("/a2a", json=_body("review_campaign_budget", ask, "mock:marketing"))
    assert _verify(600_000, one)["output"]["decision"] == "approved"
    # 먼저 60만 원 승인을 받아 뒀어도 시작 직전에 남은 40만 원으로 다시 본다
    late = _verify(600_000, two)["output"]
    assert (late["decision"], late["approved_limit_krw"]) == ("rejected", 400_000)
    assert _verify(400_000, two)["output"]["decision"] == "approved"

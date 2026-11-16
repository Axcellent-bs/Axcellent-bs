"""고객 반응(고객지원 책별 집계)으로 캠페인 예산 의견을 줄이는 규칙 `CRX-001`.

시작값: 판매 5권 이상, 판매 대비 클레임 비율 10% 이상이고 클레임 2건 이상 또는 불량 3건 이상이면
걸린다. 걸리면 요청 금액의 50%로 한도를 줄이되 걸린 책이 여럿이어도 요청당 한 번만 곱한다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.book_issue import BookIssueReport, BookIssueRow
from finance_agent import campaign as cp
from finance_agent import customer_reaction as cr
from finance_agent import sources as src
from finance_agent.handlers import reset_state, state
from finance_agent.main import app
from finance_agent.rules import load_rules
from finance_agent.sources import LiveSources, MockSources

TODAY = date(2026, 10, 15)
RULES = load_rules().customer_reaction
NOW = "2026-11-17T00:00:00Z"


def row(
    pid: str,
    *,
    sold: int | None = 100,
    claims: int = 0,
    defect: int = 0,
    title: str = "",
) -> BookIssueRow:
    rate = None if not sold else round(claims / sold, 3)
    return BookIssueRow(
        product_id=pid,
        title=title or f"책 {pid}",
        claim_count=claims,
        defect_claim_count=defect,
        claim_qty=claims,
        return_qty=0,
        refund_cash_total=0,
        sold_qty=sold,
        claim_rate=rate,
        reason="",
    )


def report(*rows: BookIssueRow) -> BookIssueReport:
    return BookIssueReport(
        period="month",
        period_start="2026-10-18T00:00:00Z",
        period_end=NOW,
        items=list(rows),
        total_claims=sum(r.claim_count for r in rows),
        claims_without_product=0,
        sales_read=True,
    )


def look(rows: list[BookIssueRow]) -> cr.Reaction:
    ids = [r.product_id for r in rows]
    return cr.evaluate(report(*rows), ids, RULES)


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    reset_state(MockSources())


# ------------------------------------------------------------ 어떤 책이 걸리는가
def test_a_book_with_a_high_claim_rate_and_enough_claims_is_flagged() -> None:
    got = look([row("prd_1", sold=100, claims=15)])
    assert got.status == cr.STATUS_FLAGGED
    assert got.flagged[0].why == "비율" and got.flagged[0].claim_rate == 0.15


def test_one_claim_in_five_sales_is_not_flagged_by_rate_alone() -> None:
    """5권 중 1건은 20%지만 클레임이 2건 미만이라 걸리지 않는다."""
    got = look([row("prd_1", sold=5, claims=1)])
    assert got.status == cr.STATUS_CLEAR and got.flagged == ()


def test_three_defect_claims_flag_a_book_even_with_a_low_rate() -> None:
    got = look([row("prd_1", sold=300, claims=3, defect=3)])
    assert got.status == cr.STATUS_FLAGGED and got.flagged[0].why == "불량"
    assert got.flagged[0].claim_rate == 0.01


def test_both_conditions_are_named_together() -> None:
    got = look([row("prd_1", sold=20, claims=5, defect=4)])
    assert got.flagged[0].why == "비율·불량"


def test_books_with_few_sales_are_only_noted_never_flagged() -> None:
    got = look([row("prd_1", sold=4, claims=4, defect=4, title="적게 팔린 책")])
    assert got.status == cr.STATUS_CLEAR and got.flagged == ()
    assert got.low_sales == ("적게 팔린 책",)


def test_an_unread_sales_quantity_is_not_guessed_and_not_called_low_sales() -> None:
    got = look([row("prd_1", sold=None, claims=9, defect=9)])
    assert got.status == cr.STATUS_CLEAR and got.flagged == ()
    assert got.sales_unread == ("책 prd_1",) and got.low_sales == ()  # "판매 적음"이 아니다


def test_requested_products_missing_from_the_report_are_listed() -> None:
    got = cr.evaluate(report(row("prd_1")), ["prd_1", "prd_9"], RULES)
    assert got.unread == ("prd_9",) and got.checked == 1


# ------------------------------------------------------------ 한도에 곱하기
def _ask(requested: int = 1_000_000) -> dict:
    return {
        "campaign_id": "cmp_x",
        "campaign_name": "가을 독서 주간",
        "channel": "릴스",
        "requested_krw": requested,
    }


def decide(reaction: cr.Reaction, requested: int = 1_000_000, cash: int = 100_000_000) -> dict:
    return cp.decide(
        _ask(requested),
        campaigns={},
        reviews=[],
        lessons=[],
        cash_net_krw=cash,
        today=TODAY,
        monthly_budget={"amount_krw": 100_000_000, "basis": "시험", "provisional": True},
        started={},
        reaction=reaction,
    )


def crx(result: dict) -> list[dict]:
    return [r for r in result["applied_rules"] if r["rule_id"] == "CRX-001"]


def test_a_flagged_book_halves_the_limit_and_the_opinion_becomes_reduce() -> None:
    got = decide(look([row("prd_1", sold=120, claims=18, defect=6, title="고양이 그림책")]))
    assert got["decision"] == "conditional" and got["approved_limit_krw"] == 500_000
    assert any("고양이 그림책" in r and "판매 120권" in r for r in got["reasons"])
    assert crx(got) and "요청 금액의 50%" in crx(got)[0]["detail"]
    assert cp.DECISION_KO[got["decision"]] == "줄여서 권장"


def test_many_flagged_books_still_cut_the_limit_only_once() -> None:
    books = [row(f"prd_{n}", sold=100, claims=20, title=f"책 {n}") for n in range(1, 11)]
    got = decide(look(books))
    assert got["approved_limit_krw"] == 500_000  # 10권이어도 0원이 되지 않는다
    detail = crx(got)[0]["detail"]
    assert all(
        f"책 {n}:" in detail or f"책 {n}'" in detail or f"'책 {n}" in detail for n in range(1, 11)
    )


def test_the_limit_is_not_cut_again_when_another_rule_already_limits_it_lower() -> None:
    got = decide(look([row("prd_1", claims=20)]), cash=300_000)
    assert got["approved_limit_krw"] == 300_000  # 현금 한도가 더 낮다


def test_when_another_rule_already_limits_lower_the_books_stay_in_the_rule_record_only() -> None:
    got = decide(look([row("prd_1", claims=20, title="걸린 책")]), cash=300_000)
    assert got["approved_limit_krw"] == 300_000
    assert not any("걸린 책" in r for r in got["reasons"])  # 이유에는 안 나온다(동작은 맞다)
    assert "걸린 책" in crx(got)[0]["detail"]  # CRX-001 기록에는 남는다


def test_a_cut_below_the_minimum_viable_amount_becomes_not_recommended_by_brk_005() -> None:
    minimum = load_rules().brake.min_viable_krw
    requested = minimum + 10_000  # 절반이 최소 금액 밑이 된다
    got = decide(look([row("prd_1", claims=20)]), requested=requested)
    assert got["decision"] == "rejected" and got["approved_limit_krw"] == 0
    assert any(r["rule_id"] == "BRK-005" for r in got["applied_rules"])


def test_a_book_that_is_fine_does_not_change_the_opinion() -> None:
    got = decide(look([row("prd_1", sold=100, claims=1)]))
    assert got["decision"] == "approved" and got["approved_limit_krw"] == 1_000_000
    assert crx(got) and "걸린 책 없음" in crx(got)[0]["detail"]


def test_few_sales_keep_the_opinion_and_say_so_in_the_reasons_and_the_rule_record() -> None:
    got = decide(look([row("prd_1", sold=4, claims=4, title="신간")]))
    assert got["decision"] == "approved"
    assert got["reasons"][-1].startswith("판매 적어 비율 참고만")
    assert "신간" in got["reasons"][-1] and "참고만" in crx(got)[0]["detail"]


def test_an_unread_sales_quantity_is_named_in_the_reasons_and_the_rule_record() -> None:
    got = decide(look([row("prd_1", sold=None, claims=9, defect=9, title="수량 모르는 책")]))
    assert got["decision"] == "approved" and got["approved_limit_krw"] == 1_000_000
    assert got["reasons"][-1] == "판매 수량을 못 읽어 비율 반영 안 함: 수량 모르는 책"
    assert "판매 수량을 못 읽어 비율 반영 안 함" in crx(got)[0]["detail"]
    assert all(
        "판매 적어" not in r for r in got["reasons"]
    )  # 못 읽은 것을 "판매 적음"으로 적지 않는다


def test_an_unreadable_report_keeps_the_limit_and_records_the_rule() -> None:
    got = decide(cr.unavailable("고객지원 책별 집계를 받지 못함"))
    assert got["decision"] == "approved" and got["approved_limit_krw"] == 1_000_000
    assert "고객 반응을 못 읽어 반영하지 않음" in got["reasons"]
    assert crx(got) and "못 읽어" in crx(got)[0]["detail"]
    assert got["customer_reaction"]["status"] == cr.STATUS_UNAVAILABLE


def test_requests_without_products_record_crx_001_as_not_applicable_and_change_nothing() -> None:
    """ "확인했는데 해당 없음"이 적용 규칙에 남아 "안 봄"과 구분된다. 한도·이유는 그대로다."""
    got = decide(cr.NOT_RELEVANT)
    assert len(crx(got)) == 1 and "요청에 상품이 없어 반영 안 함" in crx(got)[0]["detail"]
    assert got["customer_reaction"]["status"] == cr.STATUS_NOT_RELEVANT
    assert got["reasons"] == ["모든 기준을 통과함"]  # 이유에는 넣지 않는다
    assert got["decision"] == "approved" and got["approved_limit_krw"] == 1_000_000


def test_the_rule_is_in_the_rule_book_and_waits_for_review() -> None:
    book = load_rules()
    rule = book.rule("CRX-001")
    assert rule.review == "pending" and book.version >= 11
    assert (RULES.min_sold_qty, RULES.claim_rate, RULES.min_claims) == (5, 0.10, 2)
    assert (RULES.defect_claims, RULES.limit_factor, RULES.call_seconds) == (3, 0.5, 3.0)


# ------------------------------------------------------------ 읽기와 A2A
class _Reading(MockSources):
    def __init__(self, found: BookIssueReport | None) -> None:
        super().__init__()
        self.found = found
        self.asked: list[list[str]] = []

    async def book_issue_report(self, meta: Any, product_ids: list[str]) -> BookIssueReport | None:
        self.asked.append(product_ids)
        return self.found


async def _noop_sender(opinion: dict, meta: Any) -> str | None:
    return None


def _body(skill: str, data: dict, token: str) -> dict:
    return {
        "meta": {
            "context_id": "ctx_crx_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": data,
        "untrusted": {},
    }


def _review(sources: MockSources, ask: dict) -> dict:
    reset_state(sources)
    state().budget_override = {"amount_krw": 100_000_000, "basis": "시험", "provisional": True}
    state().cash_override = 100_000_000
    state().started_override = {}
    state().opinion_sender = _noop_sender
    res = TestClient(app).post("/a2a", json=_body("review_campaign_budget", ask, "mock:marketing"))
    assert res.status_code == 200, res.text
    return res.json()["output"]


def test_the_skill_reads_the_asked_products_and_lowers_the_opinion() -> None:
    sources = _Reading(report(row("prd_1", sold=100, claims=15, title="나쁜 책"), row("prd_2")))
    out = _review(sources, {**_ask(), "product_ids": ["prd_1", "prd_2"]})
    assert sources.asked == [["prd_1", "prd_2"]]
    assert out["opinion"] == "reduce" and out["suggested_krw"] == 500_000
    assert any(r["rule_id"] == "CRX-001" for r in out["applied_rules"])
    assert out["decision"] == "approved" and out["advisory"] is True  # 막지 않는다


def test_the_skill_does_not_read_anything_for_requests_without_products() -> None:
    sources = _Reading(report(row("prd_1", claims=20)))
    out = _review(sources, _ask())
    assert sources.asked == [] and out["opinion"] == "recommend"
    mine = [r for r in out["applied_rules"] if r["rule_id"] == "CRX-001"]
    assert len(mine) == 1 and "상품이 없어 반영 안 함" in mine[0]["detail"]
    assert out["reasons"] == ["모든 기준을 통과함"]


def test_the_skill_keeps_the_limit_when_the_report_cannot_be_read() -> None:
    out = _review(_Reading(None), {**_ask(), "product_ids": ["prd_1"]})
    assert out["opinion"] == "recommend" and out["suggested_krw"] == 1_000_000
    assert "고객 반응을 못 읽어 반영하지 않음" in out["reasons"]


# ------------------------------------------------------------ 실제 호출의 시간 제한과 오류
def _live_meta() -> RequestMeta:
    return RequestMeta(
        context_id="ctx_live",
        token="finance",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _fast_rules(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    fake = SimpleNamespace(customer_reaction=SimpleNamespace(call_seconds=seconds))
    monkeypatch.setattr(src, "load_rules", lambda: fake)


def test_a_slow_support_call_is_cut_off_and_read_as_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow(target: str, request: Any) -> AgentResult:
        await asyncio.sleep(5)
        raise AssertionError("제한 시간에 끊겨야 한다")

    _fast_rules(monkeypatch, 0.05)
    monkeypatch.setattr(src, "call_agent", slow)
    got = asyncio.run(LiveSources().book_issue_report(_live_meta(), ["prd_1"]))
    assert got is None


def test_an_error_or_a_wrong_shape_from_support_is_unreadable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(target: str, request: Any) -> AgentResult:
        raise ConnectionError("down")

    _fast_rules(monkeypatch, 1.0)
    monkeypatch.setattr(src, "call_agent", failing)
    assert asyncio.run(LiveSources().book_issue_report(_live_meta(), ["prd_1"])) is None

    async def odd(target: str, request: Any) -> AgentResult:
        return AgentResult(
            meta=request.meta, status=TaskStatus.COMPLETED, outcome="ok", output={"items": 3}
        )

    monkeypatch.setattr(src, "call_agent", odd)
    assert asyncio.run(LiveSources().book_issue_report(_live_meta(), ["prd_1"])) is None


def test_a_good_answer_is_read_as_the_contract_report_for_one_month_without_inquiries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def good(target: str, request: Any) -> AgentResult:
        seen.update(target=target, skill=request.skill, input=request.input)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="ok",
            output=report(row("prd_1")).model_dump(mode="json"),
        )

    _fast_rules(monkeypatch, 1.0)
    monkeypatch.setattr(src, "call_agent", good)
    got = asyncio.run(LiveSources().book_issue_report(_live_meta(), ["prd_1", "prd_2"]))
    assert got is not None and got.items[0].product_id == "prd_1"
    assert seen["target"] == "support" and seen["skill"] == "book_issue_stats"
    assert seen["input"]["period"] == "month" and seen["input"]["include_inquiries"] is False
    assert seen["input"]["product_ids"] == ["prd_1", "prd_2"]

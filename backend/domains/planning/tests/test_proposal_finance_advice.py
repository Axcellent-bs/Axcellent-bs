"""영상 기획안을 마케팅에 넘기기 전에 기획이 재무에 제작비를 직접 묻는다 (11/18 태윤).

총괄을 거치지 않는다: 기획 → 재무 `category_budget_advice`(read) → 재무 숫자를 근거 facts로 붙여
→ 마케팅 `receive_campaign_brief`. 재무가 답하지 않아도 마케팅 전달은 막지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import AgentResult, ExecuteDecision, TaskStatus
from ax_contracts.schemas.finance import CategoryBudgetAdviceOutput
from ax_contracts.schemas.planning import CampaignBriefOutput, ReceiveInsightInput
from planning_agent import proposals
from planning_agent.domain.service import PlanningService

ADVICE = {
    "status": "advice",
    "content_category": "아재개그",
    "recommended_production_krw": 130_000,
    "rows": [
        {
            "content_category": "아재개그",
            "measured_campaigns": 1,
            "measuring": 0,
            "cost_per_view": 5.0,
            "latest_production_krw": 100_000,
            "recommended_production_krw": 130_000,
        }
    ],
    "shift": {
        "from_category": "몸개그",
        "to_category": "아재개그",
        "amount_krw": 30_000,
        "ratio": 8.0,
    },
    "summary": "아재개그: 조회 1회에 5.0원. 다음 회차 제작비 100,000원 → 130,000원 권장.",
    "applied_rules": ["CMP-007", "INS-005"],
}


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Brief:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, ctx, brief, key):
        self.sent.append(brief)
        return CampaignBriefOutput(outcome="accepted", campaign_id="CMP-1")


class Finance:
    def __init__(self, out: CategoryBudgetAdviceOutput | str) -> None:
        self.out = out
        self.asked: list = []

    async def ask(self, ctx, category, fmt, proposal_id):
        self.asked.append((category, fmt, proposal_id))
        return self.out


def _insight(category: str | None) -> ReceiveInsightInput:
    return ReceiveInsightInput.model_validate(
        {
            "insight_id": f"ins-{category}",
            "title": "아재개그 숏츠 다음 회차",
            "summary": "아재개그 숏츠 조회가 잘 나온다",
            "skill": "trend_history",
            "facts": [{"label": "아재개그 조회", "value": "평균의 2배", "source": "trend_history"}],
            "content_category": category,
            "content_format": "shorts" if category else None,
        }
    )


def _run(finance, category: str | None = "아재개그"):
    brief = Brief()
    svc = PlanningService(gate=_gate, relater=None, insights=None, marketing=brief, finance=finance)
    svc.BRIEF_RETRY_DELAYS = (0,)

    async def go():
        res = await svc.receive_insight(requester="data", inp=_insight(category))
        pid = res.output["proposal_id"]
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        while svc._sending:
            await asyncio.gather(*list(svc._sending), return_exceptions=True)
        return pid

    pid = asyncio.run(go())
    p = next(x for x in svc.state.proposals if x.id == pid)
    return svc, p, brief


@pytest.mark.role("planning")
def test_planning_asks_finance_and_brief_carries_finance_numbers() -> None:
    fin = Finance(CategoryBudgetAdviceOutput.model_validate(ADVICE))
    svc, p, brief = _run(fin)
    assert fin.asked == [("아재개그", "shorts", p.id)]
    [sent] = brief.sent
    got = {f.label: f.value for f in sent.facts if f.source == proposals.FINANCE_ADVICE}
    assert got == {
        "아재개그 조회당 제작비 (게시 7일 조회수)": "5.0원",
        "재무 권장 다음 회차 제작비 (아재개그)": "130,000원",
        "재무 재배분 의견 (조회당 제작비 차이)": "몸개그 → 아재개그 30,000원 (8.0배)",
    }
    # 기획 화면의 제안 근거에도 같은 숫자가 남고, 활동 기록에 기획 → 재무가 보인다
    assert {f.value for f in p.facts} >= {"130,000원", "5.0원"}
    assert any(a.to == "finance" and "100,000원 → 130,000원" in a.text for a in svc.state.activity)


@pytest.mark.role("planning")
def test_finance_down_does_not_block_marketing() -> None:
    svc, p, brief = _run(Finance("15초 안에 응답 없음"))
    [sent] = brief.sent
    assert all(f.source != proposals.FINANCE_ADVICE for f in sent.facts)
    assert any("재무 제작비 묻기 실패" in a.text for a in svc.state.activity)


@pytest.mark.role("planning")
def test_no_category_means_no_finance_question() -> None:
    fin = Finance(CategoryBudgetAdviceOutput.model_validate(ADVICE))
    _run(fin, category=None)
    assert fin.asked == []


@pytest.mark.role("planning")
def test_measuring_category_says_so_without_numbers() -> None:
    out = CategoryBudgetAdviceOutput(
        status="measuring", content_category="아재개그", summary="아재개그는 재는 중"
    )
    _, _, brief = _run(Finance(out))
    facts = [f for f in brief.sent[0].facts if f.source == proposals.FINANCE_ADVICE]
    assert [(f.label, f.value) for f in facts] == [
        ("재무 제작비 의견 (아재개그)", "게시 7일 조회수를 재는 중")
    ]


@pytest.mark.role("planning")
def test_finance_client_reads_the_contract() -> None:
    async def caller(target, req):
        assert (target, req.skill) == ("finance", "category_budget_advice")
        assert req.input["content_category"] == "아재개그"
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=ADVICE)

    out = asyncio.run(
        proposals.FinanceBudget(caller=caller).ask("ctx", "아재개그", "shorts", "PROP-1")
    )
    assert isinstance(out, CategoryBudgetAdviceOutput) and out.recommended_production_krw == 130_000

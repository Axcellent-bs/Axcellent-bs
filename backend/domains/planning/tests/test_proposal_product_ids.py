"""기획 제안의 상품 ID: 데이터분석 인사이트 → 제안 저장 → 마케팅 브리프 (11/16 재현 님 #1128 후속).

상품 이름(`products`)과 같은 순서의 회사 상품 ID(`product_ids`)를 끝까지 그대로 넘긴다.
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import CampaignBriefOutput, ReceiveInsightInput
from ax_workflow.models import Proposal
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Brief:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, ctx, brief, key):
        self.sent.append(brief)
        return CampaignBriefOutput(outcome="accepted", campaign_id="CMP-1")


def _insight(**extra) -> ReceiveInsightInput:
    return ReceiveInsightInput.model_validate(
        {
            "insight_id": "ins-ids",
            "title": "겨울 밤 독서 숏츠",
            "summary": "독서 검색이 12월에 오른다",
            "skill": "trend_insight_brief",
            "facts": [{"label": "독서 검색 작년 12월", "value": "평균의 1.4배", "source": "x"}],
            "products": ["밤의 도서관", "겨울 산책"],
            **extra,
        }
    )


async def _flush(svc: PlanningService) -> None:
    while svc._sending:
        await asyncio.gather(*list(svc._sending), return_exceptions=True)


def _svc(brief: Brief) -> PlanningService:
    svc = PlanningService(gate=_gate, relater=None, marketing=brief)
    svc.BRIEF_RETRY_DELAYS = (0, 0)
    return svc


async def _approve(svc: PlanningService, inp: ReceiveInsightInput) -> str:
    pid = (await svc.receive_insight(requester="data", inp=inp)).output["proposal_id"]
    await svc.dispatch({"type": "approveProposal", "proposal": pid, "note": "12월 첫 주"})
    await _flush(svc)
    return pid


def test_product_ids_go_from_insight_to_marketing_brief_in_order() -> None:
    brief = Brief()
    svc = _svc(brief)
    pid = asyncio.run(_approve(svc, _insight(product_ids=["P-102", "P-007"])))
    p = next(x for x in svc.state.proposals if x.id == pid)
    assert p.product_ids == ["P-102", "P-007"]
    [sent] = brief.sent
    assert sent.products == ["밤의 도서관", "겨울 산책"]
    assert sent.product_ids == ["P-102", "P-007"]


def test_insight_without_product_ids_sends_an_empty_list() -> None:
    brief = Brief()
    svc = _svc(brief)
    asyncio.run(_approve(svc, _insight()))
    [sent] = brief.sent
    assert sent.products == ["밤의 도서관", "겨울 산책"] and sent.product_ids == []


def test_an_old_saved_proposal_without_product_ids_still_loads() -> None:
    """11/16 전 상태 파일의 제안에는 `product_ids`가 없다."""
    old = {
        "id": "PROP-3",
        "title": "가을 독서",
        "from": "data",
        "skill": "trend_history",
        "insight": "독서 검색이 오른다",
        "products": ["밤의 도서관"],
        "idea": "숏츠",
        "why": "검색 증가",
        "created_at": "2026-11-10T01:00:00Z",
    }
    p = Proposal.model_validate(old)
    assert p.products == ["밤의 도서관"] and p.product_ids == []

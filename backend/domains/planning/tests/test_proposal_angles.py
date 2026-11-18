"""기획 제안 방향 선택지 흐름 (11/18): 받을 때 선택지 → 사람이 방향 고르기 → 마케팅 브리프."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.data import TrendInsight
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from ax_contracts.schemas.planning import CampaignBriefOutput, ReceiveInsightInput
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _insight(ref: str = "ins-1", **kw) -> ReceiveInsightInput:
    base = {
        "insight_id": ref,
        "title": "챌린지",
        "summary": "챌린지 숏츠가 많이 보인다",
        "skill": "trend_insight_brief",
        "facts": [{"label": '"챌린지" 숏츠 중앙값 조회', "value": "506만", "source": "s"}],
        "products": ["밤의 도서관"],
        "shorts_format": "챌린지",
    }
    return ReceiveInsightInput.model_validate({**base, **kw})


TRENDS = [
    TrendInsight(
        insight_id="t-1", rank=1, topic="책장 넘기기 챌린지", line="l", shorts_format="챌린지",
        ad_material=True, real_entity=False, score=3.0, shorts_format_median_views=48000.0,
    )
]  # fmt: skip


class Insights:
    def __init__(self, trends=TRENDS) -> None:
        self.trends_calls = 0
        self._trends = trends

    async def trends(self, ctx):
        self.trends_calls += 1
        return self._trends


class Marketing:
    def __init__(self, followers=300) -> None:
        self.sent: list = []
        self._followers = followers

    async def followers(self, ctx):
        if self._followers is None:
            return "channel_followers 없음"
        ch = {"channel": "instagram", "followers": self._followers}
        return ChannelFollowersOutput.model_validate({"status": "known", "channels": [ch]})

    async def send(self, ctx, brief, key):
        self.sent.append(brief)
        return CampaignBriefOutput(outcome="accepted", campaign_id="CMP-1")


def _svc(insights=None, marketing=None, writer=None, finance=None) -> PlanningService:
    svc = PlanningService(
        gate=_gate, relater=None, insights=insights, marketing=marketing,
        proposal_writer=writer, finance=finance,
    )  # fmt: skip
    svc.BRIEF_RETRY_DELAYS = (0, 0)
    return svc


def _new(svc: PlanningService, **kw) -> str:
    res = asyncio.run(svc.receive_insight(requester="data", inp=_insight(**kw)))
    return res.output["proposal_id"]


def _p(svc: PlanningService, pid: str):
    return next(x for x in svc.state.proposals if x.id == pid)


def test_receive_stores_three_options_with_recommendation() -> None:
    svc = _svc(insights=Insights(), marketing=Marketing(followers=300))
    p = _p(svc, _new(svc))
    assert [o.id for o in p.options] == ["ride_trend", "own_trend", "with_product"]
    ride = p.options[0]
    assert ride.available and ride.recommended and ride.refs[0].topic == "책장 넘기기 챌린지"
    assert p.scale is not None and p.scale.followers == 300 and p.angle is None
    assert "추천 유행에 올라타기" in svc.state.activity[-1].text


def test_receive_without_data_or_marketing_still_builds_options() -> None:
    svc = _svc()
    p = _p(svc, _new(svc))
    assert len(p.options) == 3
    assert not p.options[0].available and p.options[0].blocked_reason == "트렌드 목록을 받지 못함"
    assert not any(o.recommended for o in p.options)
    assert "추천 없음" in svc.state.activity[-1].text


def test_failed_sources_count_as_unknown() -> None:
    svc = _svc(insights=Insights(trends="호출 실패"), marketing=Marketing(followers=None))
    p = _p(svc, _new(svc))
    assert p.options[0].blocked_reason == "트렌드 목록을 받지 못함"
    assert p.scale is not None and p.scale.followers is None


def test_duplicate_does_not_call_sources() -> None:
    ins = Insights()
    svc = _svc(insights=ins)
    _new(svc)
    _new(svc)
    assert ins.trends_calls == 1

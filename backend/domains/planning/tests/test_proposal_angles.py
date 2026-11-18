"""기획 제안 방향 선택지 흐름 (11/18): 받을 때 선택지 → 사람이 방향 고르기 → 마케팅 브리프."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.data import TrendInsight
from ax_contracts.schemas.finance import CategoryBudgetAdviceOutput
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from ax_contracts.schemas.planning import CampaignBriefOutput, ReceiveInsightInput
from planning_agent import proposals
from planning_agent.domain.service import ActionError, PlanningService

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


_T = TrendInsight(
    insight_id="t-1", rank=1, topic="책장 넘기기 챌린지", line="l", shorts_format="챌린지",
    ad_material=True, real_entity=False, score=3.0, shorts_format_median_views=5060000.0,
)  # fmt: skip
# 11/18 재현 님이 trend_insight_brief에 더하는 주제 자기 숏츠 칸
# (계약에 들어오기 전이라 덧붙여 흉내)
TRENDS = [
    SimpleNamespace(
        **_T.model_dump(),
        shorts_format_topic_median_views=48000.0,
        shorts_format_topic_at=None,
        shorts_format_topic_examples=[SimpleNamespace(title="책장 넘기는 손", views=52000)],
    )
]


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


# ---------------------------------------------------------------- 사람 버튼: 방향 고르기


class AngleWriter:
    """LLM 대신: 받은 방향을 기록하고 그 방향 글을 돌려준다."""

    def __init__(self, why: str = "책장 넘기기 챌린지 중앙값 48000에 올라탄다") -> None:
        self.angles: list[str] = []
        self.why = why

    async def draft(self, inp, ctx):
        return None

    async def draft_for_angle(self, p, option, ctx):
        self.angles.append(option.id)
        return proposals.ProposalDraft(f"{option.label} 숏츠", self.why, None, "숏폼 15초", "llm")


class Finance:
    def __init__(self) -> None:
        self.asked: list[tuple] = []

    async def ask(self, ctx, category, fmt, proposal_id):
        self.asked.append((category, fmt, proposal_id))
        return CategoryBudgetAdviceOutput(
            status="advice", content_category=category, recommended_production_krw=130_000,
            summary="권장 제작비",
        )  # fmt: skip


def _choose(svc: PlanningService, pid: str, angle: str):
    return asyncio.run(svc.dispatch({"type": "chooseAngle", "proposal": pid, "angle": angle}))


def test_choose_writes_draft_for_angle() -> None:
    w = AngleWriter()
    svc = _svc(insights=Insights(), marketing=Marketing(), writer=w)
    pid = _new(svc)
    _choose(svc, pid, "ride_trend")
    p = _p(svc, pid)
    assert w.angles == ["ride_trend"]
    assert p.angle == "ride_trend" and p.idea == "유행에 올라타기 숏츠" and p.written_by == "llm"
    assert "방향 선택: 유행에 올라타기" in svc.state.activity[-1].text


def test_choose_falls_back_to_rule_draft() -> None:
    svc = _svc(insights=Insights(), marketing=Marketing())
    pid = _new(svc)
    _choose(svc, pid, "ride_trend")
    p = _p(svc, pid)
    assert p.written_by == "rule" and p.idea.startswith("유행에 올라타기: 챌린지")
    assert "책장 넘기기 챌린지" in p.idea


def test_choose_blocked_or_closed_is_refused() -> None:
    svc = _svc()  # 트렌드 목록 없음 → ride_trend 막힘
    pid = _new(svc)
    before = _p(svc, pid).model_dump()
    with pytest.raises(ActionError) as e:
        _choose(svc, pid, "ride_trend")
    assert e.value.code == "ANGLE_BLOCKED" and "트렌드 목록" in e.value.message
    with pytest.raises(ActionError) as e2:
        _choose(svc, pid, "nope")
    assert e2.value.code == "ANGLE_BLOCKED"
    with pytest.raises(ActionError) as e3:
        _choose(svc, "PROP-99", "own_trend")
    assert e3.value.code == "NOT_FOUND"
    asyncio.run(svc.dispatch({"type": "rejectProposal", "proposal": pid, "reason": "안 함"}))
    with pytest.raises(ActionError) as e4:
        _choose(svc, pid, "own_trend")
    assert e4.value.code == "PROPOSAL_CLOSED"
    assert _p(svc, pid).angle is None and before["idea"] == _p(svc, pid).idea


def test_choose_asks_finance_once_with_proposal_id() -> None:
    fin = Finance()
    svc = _svc(insights=Insights(), marketing=Marketing(), finance=fin)
    pid = _new(svc, content_category="아재개그", content_format="shorts")
    _choose(svc, pid, "own_trend")
    assert fin.asked == [("아재개그", "shorts", pid)]
    p = _p(svc, pid)
    assert any(f.source == proposals.FINANCE_ADVICE for f in p.facts)


def test_choose_twice_does_not_duplicate_finance_facts() -> None:
    fin = Finance()
    svc = _svc(insights=Insights(), marketing=Marketing(), finance=fin)
    pid = _new(svc)
    _choose(svc, pid, "own_trend")
    _choose(svc, pid, "ride_trend")
    assert len(fin.asked) == 1 and fin.asked[0][0] is None  # 카테고리 없으면 비워서 묻는다
    n = sum(f.source == proposals.FINANCE_ADVICE for f in _p(svc, pid).facts)
    assert n == len(proposals.finance_facts(asyncio.run(fin.ask("c", None, None, pid))))


def test_candidate_numbers_count_as_facts() -> None:
    svc = _svc(insights=Insights(), marketing=Marketing(), writer=AngleWriter())
    pid = _new(svc)
    _choose(svc, pid, "ride_trend")
    assert _p(svc, pid).warnings == []  # 48000은 후보 숫자라 근거로 본다
    svc2 = _svc(insights=Insights(), marketing=Marketing(), writer=AngleWriter(why="조회 777 예상"))
    pid2 = _new(svc2)
    _choose(svc2, pid2, "ride_trend")
    assert any("777" in w for w in _p(svc2, pid2).warnings)


def test_confirm_without_angle_still_allowed() -> None:
    svc = _svc()
    pid = _new(svc)
    asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid}))
    assert _p(svc, pid).status == "기획안" and _p(svc, pid).angle is None

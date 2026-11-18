"""기획 제안 방향 선택지 흐름 (11/18): 받을 때 선택지 → 사람이 방향 고르기 → 마케팅 브리프."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.data import TopicFormatExample, TrendInsight
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


TRENDS = [
    TrendInsight(
        insight_id="t-1", rank=1, topic="책장 넘기기 챌린지", line="l", shorts_format="챌린지",
        ad_material=True, real_entity=False, score=3.0, shorts_format_median_views=5060000.0,
        shorts_format_topic_median_views=48000.0,
        shorts_format_topic_examples=[TopicFormatExample(title="책장 넘기는 손", views=52000)],
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


# ---------------------------------------------------------------- 마케팅 브리프


def _approve(svc: PlanningService, pid: str) -> None:
    async def go():
        await svc.dispatch({"type": "makePlan", "proposal": pid})
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        while svc._sending:
            await asyncio.gather(*list(svc._sending), return_exceptions=True)

    asyncio.run(go())


def test_brief_carries_angle_and_trend_refs() -> None:
    m = Marketing()
    svc = _svc(insights=Insights(), marketing=m)
    pid = _new(svc)
    _choose(svc, pid, "ride_trend")
    _approve(svc, pid)
    assert m.sent[-1].idea_angle == "ride_trend"
    assert m.sent[-1].trend_refs == ["책장 넘기기 챌린지"]


def test_brief_without_angle_is_unchanged() -> None:
    m = Marketing()
    svc = _svc(insights=Insights(), marketing=m)
    pid = _new(svc)
    _approve(svc, pid)
    assert m.sent[-1].idea_angle is None and m.sent[-1].trend_refs == []
    m2 = Marketing()
    svc2 = _svc(insights=Insights(), marketing=m2)
    pid2 = _new(svc2)
    _choose(svc2, pid2, "own_trend")
    _approve(svc2, pid2)
    assert m2.sent[-1].idea_angle == "own_trend" and m2.sent[-1].trend_refs == []


# ---------------------------------------------------------------- 최종 리뷰 반영 (11/18)


def test_rule_draft_scale_numbers_are_not_warnings() -> None:
    """추천 이유의 규모 숫자(팔로워·지난 조회)는 받은 값이라 숫자 경고가 아니다.
    (11/18 리뷰 Important 1)"""
    svc = _svc(insights=Insights(), marketing=Marketing(followers=3000))
    pid = _new(svc)
    _choose(svc, pid, "ride_trend")
    p = _p(svc, pid)
    assert "3,000" in p.why and p.warnings == []


def test_double_click_asks_finance_once() -> None:
    """같은 제안에 방향 고르기가 겹치면 두 번째는 ANGLE_BUSY, 재무도 한 번만 (리뷰 Important 2)."""
    gate = asyncio.Event()

    class SlowWriter(AngleWriter):
        async def draft_for_angle(self, p, option, ctx):
            await gate.wait()
            return await super().draft_for_angle(p, option, ctx)

    fin = Finance()
    svc = _svc(insights=Insights(), marketing=Marketing(), writer=SlowWriter(), finance=fin)
    pid = _new(svc)

    async def go():
        act = {"type": "chooseAngle", "proposal": pid, "angle": "own_trend"}
        first = asyncio.create_task(svc.dispatch(act))
        second = asyncio.create_task(svc.dispatch(act))
        await asyncio.sleep(0.05)
        gate.set()  # 막히지 않아도 테스트가 멈추지 않게 풀어 준다
        return await asyncio.gather(first, second, return_exceptions=True)

    _, second = asyncio.run(go())
    assert isinstance(second, ActionError) and second.code == "ANGLE_BUSY"
    assert len(fin.asked) == 1
    _choose(svc, pid, "ride_trend")  # 끝난 뒤에는 다시 고를 수 있다
    assert _p(svc, pid).angle == "ride_trend"


def test_pull_reads_trends_once_for_many_insights() -> None:
    """받아오기 한 번에 트렌드 목록은 한 번만 부른다 (리뷰 Important 3)."""

    class Pull(Insights):
        async def pull(self, ctx):
            topics = [
                ("독서 챌린지", "밤의 도서관"),
                ("요리 레시피", "집밥 노트"),
                ("여행 브이로그", "길 위의 책"),
            ]
            return [
                _insight(f"ins-{i}", title=n, summary=f"{n} 숏츠가 많이 보인다", products=[b])
                for i, (n, b) in enumerate(topics)
            ]

    ins = Pull()
    svc = _svc(insights=ins, marketing=Marketing())
    out = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    assert len(out.output["created"]) == 3 and ins.trends_calls == 1
    assert all(len(p.options) == 3 for p in svc.state.proposals)


def test_option_sources_use_short_timeout() -> None:
    """선택지 출처는 짧게 기다리고, 늦으면 확인할 수 없음으로 만든다 (리뷰 Important 3)."""
    from planning_agent.domain import proposal_actions as pa

    class Slow(Insights):
        async def trends(self, ctx):
            await asyncio.sleep(5)
            return TRENDS

    old = pa.OPTION_SOURCE_TIMEOUT
    pa.OPTION_SOURCE_TIMEOUT = 0.05
    try:
        svc = _svc(insights=Slow(), marketing=Marketing())
        p = _p(svc, _new(svc))
    finally:
        pa.OPTION_SOURCE_TIMEOUT = old
    assert p.options[0].blocked_reason == "트렌드 목록을 받지 못함"
    assert p.scale is not None and p.scale.followers == 300


# ---------------------------------------- 방향 고를 때 카테고리 고르기 (11/18 태윤 요청)


def test_choose_sets_category_and_asks_finance_with_it() -> None:
    """카테고리 없이 온 제안은 방향을 고를 때 사람이 카테고리·형식을 정한다.
    재무에는 그 카테고리로 묻는다."""
    fin = Finance()
    m = Marketing()
    svc = _svc(insights=Insights(), marketing=m, finance=fin)
    pid = _new(svc)
    act = {"type": "chooseAngle", "proposal": pid, "angle": "own_trend",
           "category": " 아재개그 ", "format": "shorts"}  # fmt: skip
    asyncio.run(svc.dispatch(act))
    p = _p(svc, pid)
    assert (p.content_category, p.content_format) == ("아재개그", "shorts")
    assert fin.asked == [("아재개그", "shorts", pid)]
    _approve(svc, pid)
    assert m.sent[-1].content_category == "아재개그" and m.sent[-1].content_format == "shorts"


def test_category_given_later_asks_finance_again_without_duplicates() -> None:
    """처음엔 카테고리 없이 물었고 나중에 카테고리를 정하면 그걸로 다시 묻는다.
    재무 근거는 새 답으로 바꾼다(겹치지 않음)."""
    fin = Finance()
    svc = _svc(insights=Insights(), marketing=Marketing(), finance=fin)
    pid = _new(svc)
    _choose(svc, pid, "own_trend")
    act = {"type": "chooseAngle", "proposal": pid, "angle": "ride_trend", "category": "몸개그"}
    asyncio.run(svc.dispatch(act))
    assert [a[0] for a in fin.asked] == [None, "몸개그"]
    facts = [f for f in _p(svc, pid).facts if f.source == proposals.FINANCE_ADVICE]
    assert facts and all("몸개그" in f.label for f in facts)


def test_category_from_insight_is_kept() -> None:
    """데이터분석이 채운 카테고리는 화면 값으로 덮지 않는다."""
    fin = Finance()
    svc = _svc(insights=Insights(), marketing=Marketing(), finance=fin)
    pid = _new(svc, content_category="피식", content_format="reels")
    act = {"type": "chooseAngle", "proposal": pid, "angle": "own_trend", "category": "몸개그"}
    asyncio.run(svc.dispatch(act))
    assert _p(svc, pid).content_category == "피식"
    assert fin.asked == [("피식", "reels", pid)]


def test_bad_category_or_format_is_refused() -> None:
    svc = _svc(insights=Insights(), marketing=Marketing())
    pid = _new(svc)
    for extra in ({"category": "가" * 41}, {"format": "tiktok"}, {"category": 3}):
        with pytest.raises(ActionError) as e:
            asyncio.run(
                svc.dispatch(
                    {"type": "chooseAngle", "proposal": pid, "angle": "own_trend", **extra}
                )
            )
        assert e.value.code == "INVALID_ACTION"
    assert _p(svc, pid).content_category is None and _p(svc, pid).angle is None


# ---------------------------------------------------------------- 예전 제안에 선택지 채우기 (11/18)


def _old(svc: PlanningService, pid: str, status: str = "확인 대기") -> None:
    """#1282 전에 만들어진 제안처럼 선택지·규모를 비운다."""
    p = _p(svc, pid)
    p.options, p.scale, p.status = [], None, status


def test_pull_fills_options_on_open_proposals_made_before_options() -> None:
    """예전 제안(선택지 없음)은 받아오기 때 선택지를 채운다. 같은 인사이트라 새 제안은 안 생긴다."""

    class Pull(Insights):
        async def pull(self, ctx):
            return [_insight("ins-1")]

    ins = Pull()
    svc = _svc(insights=ins, marketing=Marketing(followers=300))
    pid = _new(svc)
    _old(svc, pid)
    ins.trends_calls = 0  # 제안을 만들 때 부른 것은 빼고 받아오기만 센다
    out = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    p = _p(svc, pid)
    assert out.output["created"] == [] and out.output["filled"] == [pid]
    assert [o.id for o in p.options] == ["ride_trend", "own_trend", "with_product"]
    assert p.options[0].recommended and p.scale is not None and p.scale.followers == 300
    assert any(f"방향 선택지 채움 {pid}" in a.text for a in svc.state.activity)
    assert ins.trends_calls == 1


def test_pull_fills_options_even_when_data_pull_fails() -> None:
    """데이터분석 받아오기가 실패해도(LLM 한도 등) 예전 제안 선택지는 채운다."""

    class Fail(Insights):
        async def pull(self, ctx):
            return "LLM 하루 상한"

    svc = _svc(insights=Fail(), marketing=Marketing(followers=300))
    pid = _new(svc)
    _old(svc, pid)
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "pullInsights"}))
    assert len(_p(svc, pid).options) == 3


def test_pull_does_not_touch_closed_or_already_filled_proposals() -> None:
    class Pull(Insights):
        async def pull(self, ctx):
            return []

    svc = _svc(insights=Pull(), marketing=Marketing(followers=300))
    closed = _new(svc, ref="a")
    _old(svc, closed, status="마케팅 전달")
    filled = _new(svc, ref="b")
    before = [o.model_dump() for o in _p(svc, filled).options]
    out = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    assert out.output["filled"] == []
    assert _p(svc, closed).options == []
    assert [o.model_dump() for o in _p(svc, filled).options] == before

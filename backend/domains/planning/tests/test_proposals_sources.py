"""방향 선택지에 쓰는 바깥 숫자 읽기 (11/18): 데이터분석 트렌드 목록·마케팅 팔로워·재무 제작비."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import AgentResult, TaskStatus
from ax_contracts.schemas.data import TrendInsight
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from planning_agent import proposals

pytestmark = pytest.mark.role("planning")

TREND = {"insight_id": "t1", "rank": 1, "topic": "독서 챌린지", "line": "l"}


def test_trends_returns_topics_or_reason() -> None:
    async def ok(target, req):
        assert (target, req.skill) == ("data", "trend_insight_brief")
        out = {"status": "known", "insights": [TREND]}
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)

    got = asyncio.run(proposals.DataInsights(caller=ok).trends("ctx"))
    assert isinstance(got, list) and isinstance(got[0], TrendInsight)
    assert got[0].topic == "독서 챌린지"

    async def boom(target, req):
        raise ConnectionError("down")

    bad = asyncio.run(proposals.DataInsights(caller=boom).trends("ctx"))
    assert isinstance(bad, str) and "호출 실패" in bad


def test_followers_unknown_skill_is_reason() -> None:
    async def missing(target, req):
        assert (target, req.skill) == ("marketing", "channel_followers")
        return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unknown_skill")

    got = asyncio.run(proposals.MarketingBrief(caller=missing).followers("ctx"))
    assert isinstance(got, str)

    async def ok(target, req):
        out = {"status": "known", "channels": [{"channel": "instagram", "followers": 300}]}
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)

    out = asyncio.run(proposals.MarketingBrief(caller=ok).followers("ctx"))
    assert isinstance(out, ChannelFollowersOutput) and out.channels[0].followers == 300


def test_finance_ask_without_category() -> None:
    seen: list = []

    async def caller(target, req):
        seen.append(req.input)
        out = {"status": "no_data", "summary": "기록 없음"}
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)

    asyncio.run(proposals.FinanceBudget(caller=caller).ask("ctx", None, None, "PROP-1"))
    assert seen[0]["content_category"] is None and seen[0]["proposal_id"] == "PROP-1"


def test_past_keeps_views_number_when_data_sends_it() -> None:
    class R:
        campaign_id = "c1"
        views_label = "조회 미달"
        sales_label = ""
        band = "gray"
        summary_line = ""
        note_line = None
        actions: list = []
        views = 1200
        views_at = None

    assert proposals._past(R()).views == 1200
    del R.views
    assert proposals._past(R()).views is None

"""인사이트 "지난번 결과" 단위 약속 (실제 쇼핑몰·YouTube는 안 부른다).

- 캠페인이 없으면 past_results = [] (화면은 회색 "지난번 결과 없음")
- 캠페인 결과는 campaign_result 그대로, 10분 동안 다시 계산하지 않는다
- 브리프 응답에는 붙이고 schema_version은 v4 (판정·순서는 그대로)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import (
    CampaignEffectInput,
    CampaignEffectOutput,
    TrendInsightBriefOutput,
    VideoPerformanceRequest,
)
from data_agent import past_results
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import video_performance as vp

PUB = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
EFFECT = {
    "campaign_id": "cmp_1", "window_from": "2026-11-01T03:00:00Z",
    "window_to": "2026-11-08T03:00:00Z", "summary": "x", "incremental_orders": -15,
    "orders": {"status": "known", "direction": "none", "excess": -15.0, "baseline_days": 4},
    "revenue": {"status": "unknown", "unknown_reason": "x"},
    "coupons": {"status": "unknown", "unknown_reason": "x"},
    "sales": {"status": "known", "baseline_days": 4, "incremental_gross_krw": -700_000,
              "incremental_low_krw": -1_200_000, "incremental_high_krw": -150_000,
              "direction": "down", "baseline_rule": "시작 전 4주 같은 요일·시간대",
              "period": {"orders": 25, "gross_krw": 1, "discount_krw": 0, "paid_krw": 1,
                         "refund_krw": 0},
              "baseline": {"orders": 40, "gross_krw": 1, "discount_krw": 0, "paid_krw": 1,
                           "refund_krw": 0}},
}  # fmt: skip


class FakeYouTube:
    def __init__(self) -> None:
        self.now = PUB

    async def videos(self, ids):  # noqa: ANN001, ANN201
        views = int(30_000 * min((self.now - PUB).total_seconds() / 3600 / 24, 1))
        return [VideoStat(i, "ch", "24", PUB, views, views // 20, views // 100, (), title=i)
                for i in ids]  # fmt: skip


def _env(tmp: Path) -> dict[str, str]:
    return {vp.DB_ENV: str(tmp / "perf.db"), "DATA_INSIGHT_BRIEF_DB": str(tmp / "brief.db"),
            "DATA_YOUTUBE_DB": str(tmp / "yt.db"),
            "DATA_SHORTS_DB": str(tmp / "shorts.db")}  # fmt: skip


def _setup(tmp: Path) -> dict[str, str]:
    conn = sqlite3.connect(tmp / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    insight = {"insight_id": "ins_1", "rank": 1, "topic": "파이썬 입문", "line": "x",
               "shorts_format": "정보·팁", "target_views_reference": 5000.0}  # fmt: skip
    conn.execute("INSERT INTO briefs VALUES ('2026-11-01','r','x',?,NULL)",
                 (json.dumps({"insights": [insight]}),))  # fmt: skip
    conn.commit()
    conn.close()
    env = _env(tmp)
    yt = FakeYouTube()
    req = VideoPerformanceRequest(video_url="https://youtu.be/aaaaaaaaaaa", campaign_id="cmp_1",
                                  insight_id="ins_1", promo_id="p1")  # fmt: skip
    asyncio.run(vp.video_performance(req, yt, env=env, now=PUB))
    yt.now = PUB + timedelta(hours=168, minutes=10)
    store = vp.store_of(env)
    asyncio.run(vp.measure_due(store, yt, yt.now))
    store.close()
    return env


def test_no_campaigns_means_an_empty_list(tmp_path: Path) -> None:
    past_results.clear_cache()
    got = asyncio.run(past_results.campaign_results(_env(tmp_path), None))
    assert got == []
    brief = TrendInsightBriefOutput.model_validate(
        {"insights": [{"insight_id": "a", "rank": 1, "topic": "t", "line": "x"}]})
    out = past_results.attach_past(brief, got)
    assert out.insights[0].past_results == [] and out.schema_version == "trend_insight_brief.v5"


def test_campaign_result_feeds_the_past_result_and_is_cached(tmp_path: Path) -> None:
    past_results.clear_cache()
    env = _setup(tmp_path)
    calls: list[CampaignEffectInput] = []

    async def measure(data, connector, *, env):  # noqa: ANN001, ANN202, ARG001
        calls.append(data)
        return CampaignEffectOutput.model_validate(EFFECT)

    now = PUB + timedelta(days=9)
    first = asyncio.run(past_results.campaign_results(env, "shop", now=now, measure=measure))
    again = asyncio.run(past_results.campaign_results(env, "shop", now=now, measure=measure))
    assert [r.campaign_id for r in first] == ["cmp_1"] and len(calls) == 1  # 두 번째는 캐시
    assert again[0] is first[0]
    (p,) = past_results.attach_past(TrendInsightBriefOutput.model_validate(
        {"insights": [{"insight_id": "ins_1", "rank": 1, "topic": "이름 바뀜", "line": "x"}]}),
        first).insights[0].past_results  # fmt: skip
    assert (p.views_verdict, p.sales_verdict, p.band) == ("achieved", "less", "red")
    assert p.summary_line == ("조회 목표의 6.00배 (영상 1개 중 1개 달성) · 매출 -700,000원"
                              " · 주문 -15건")
    assert p.note_line == past_results.CONTRA
    assert {a.kind for a in p.alerts} == {"loss", "data_gap"}  # 앞 시점을 안 재서 data_gap도


def test_keys_pick_campaigns_by_proposal_id_or_insight_id(tmp_path: Path) -> None:
    """이벤트 제안의 성과 되돌리기: 영상의 proposal_id(PROP-n)나 insight_id로 캠페인을 고른다."""
    past_results.clear_cache()
    env = _setup(tmp_path)  # cmp_1: insight_id ins_1
    req = VideoPerformanceRequest(video_url="https://youtu.be/bbbbbbbbbbb", campaign_id="cmp_2",
                                  proposal_id="PROP-7", promo_id="p2")  # fmt: skip
    asyncio.run(vp.video_performance(req, FakeYouTube(), env=env, now=PUB))

    async def measure(data, connector, *, env):  # noqa: ANN001, ANN202, ARG001
        return CampaignEffectOutput.model_validate({**EFFECT, "campaign_id": data.campaign_id})

    now = PUB + timedelta(days=9)

    def ids(**kw):  # noqa: ANN003, ANN202
        got = asyncio.run(past_results.campaign_results(env, "shop", now=now, measure=measure,
                                                        **kw))  # fmt: skip
        return [(r.campaign_id, r.proposal_id, r.insight_id) for r in got]

    assert ids() == [("cmp_1", None, "ins_1")]  # 기본: 인사이트가 있는 캠페인만 (예전 그대로)
    assert ids(keys={"PROP-7"}) == [("cmp_2", "PROP-7", None)]
    assert ids(keys={"ins_1", "PROP-7"}) == [("cmp_1", None, "ins_1"), ("cmp_2", "PROP-7", None)]
    assert ids(keys=set()) == [] and ids(keys={"PROP-404"}) == []


# ------------------------------------------------ 게시 7일 조회 중앙값 (views·views_at)


def _video(views: int | None, at: datetime | None = None, *, test: bool = False,
           status: str = "known", verdict: str = "achieved") -> dict:  # fmt: skip
    seven = {"checkpoint": "7d", "status": status, "views": views, "measured_at": at}
    return {"status": "known", "test": test, "verdict": verdict, "checkpoints": [seven]}


def _past(videos: list[dict]):  # noqa: ANN202
    from ax_contracts.schemas.data import CampaignResultOutput

    r = CampaignResultOutput.model_validate({"campaign_id": "cmp_v", "videos": videos})
    return past_results.to_past(r)


def test_views_is_the_median_of_seven_day_views_with_latest_measured_at() -> None:
    a, b, c = PUB, PUB + timedelta(hours=5), PUB + timedelta(hours=2)
    got = _past([_video(1000, a), _video(5000, b), _video(3000, c)])
    assert (got.views, got.views_videos, got.views_checkpoint) == (3000, 3, "7d")
    assert got.views_at == b and got.views_reason is None
    even = _past([_video(1000), _video(2000)])  # 짝수 개는 가운데 둘의 평균, 잰 시각 모르면 None
    assert even.views == 1500 and even.views_at is None and even.views_videos == 2


def test_views_skip_test_videos() -> None:
    got = _past([_video(1000, PUB), _video(3000, PUB), _video(900_000, PUB, test=True)])
    assert got.views == 2000 and got.views_videos == 2


def test_views_none_with_reason_when_not_measured() -> None:
    measuring = _past([_video(None, status="unknown", verdict="measuring")])
    assert measuring.views is None and measuring.views_checkpoint is None
    assert measuring.views_videos == 0
    assert measuring.views_reason == "게시 7일이 아직 안 됨 (측정 중)"
    empty = _past([])
    assert empty.views is None and empty.views_reason == "시험 영상 말고 잰 영상이 없음"
    only_test = _past([_video(1000, PUB, test=True)])
    assert only_test.views is None and only_test.views_reason == "시험 영상 말고 잰 영상이 없음"
    missed = _past([_video(None, status="unknown", verdict="excluded")])
    assert missed.views_reason == "게시 7일 조회를 잰 값이 없음"


def test_views_do_not_change_existing_fields() -> None:
    got = _past([_video(1000, PUB), _video(5000, PUB), _video(3000, PUB)])
    assert got.views_label == "조회 판정 없음"
    assert got.summary_line == "조회 판정 없음 · 매출 측정 불가"


def test_old_past_result_json_without_views_still_reads() -> None:
    from ax_contracts.schemas.data import InsightPastResult

    old = {"campaign_id": "cmp_1", "views_label": "조회 달성", "sales_label": "더 팔림",
           "band": "green", "summary_line": "x"}  # fmt: skip
    got = InsightPastResult.model_validate(old)
    assert got.views is None and got.views_at is None and got.views_checkpoint is None
    assert got.views_videos == 0 and got.views_reason is None

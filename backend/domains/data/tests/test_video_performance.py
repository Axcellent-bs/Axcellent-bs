"""video_performance: 게시한 영상의 1시간·1일·3일·7일 성과. 실제 YouTube는 안 부른다 (가짜).

- 등록(video_url) → 게시 시각을 1번 받고, 시점이 지나면(허용 창 안) 그 값을 저장
- 시점이 아직이면 unknown + 측정 중, 창을 넘기면 놓침, 인스타·틱톡은 연결 대기 + 필요한 것
- 배수: 목표 참고값(인사이트) 대비, 같은 형식·카테고리 숏츠 중앙값 대비. 숫자는 facts에
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas.data import (
    ShortsGroup,
    ShortsStatsOutput,
    VideoPerformanceRequest,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import video_performance as vp

PUB = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
URL = "https://www.youtube.com/shorts/abcdefghijk"


class FakeYouTube:
    """조회수 곡선: 게시 후 시간 → 값. 부른 횟수(영상 수)를 센다."""

    def __init__(self, curve=None, fail: bool = False) -> None:  # noqa: ANN001
        self.curve = curve or (lambda hours: int(1000 * hours))
        self.now = PUB
        self.asked: list[list[str]] = []
        self.fail = fail

    async def videos(self, ids):  # noqa: ANN001, ANN201
        self.asked.append(list(ids))
        if self.fail:
            raise ConnectorUnavailable("QUOTA_EXCEEDED", "할당량")
        hours = (self.now - PUB).total_seconds() / 3600
        views = self.curve(hours)
        return [VideoStat(i, "ch", "24", PUB, views, None if views is None else views // 20,
                          None if views is None else views // 100, (), title="책 리뷰 숏츠")
                for i in ids]  # fmt: skip


def _env(tmp_path: Path) -> dict[str, str]:
    return {vp.DB_ENV: str(tmp_path / "perf.db"),
            "DATA_INSIGHT_BRIEF_DB": str(tmp_path / "brief.db")}  # fmt: skip


def _run(tmp_path: Path, yt: FakeYouTube, at: datetime, shorts=None, **req):  # noqa: ANN001, ANN003, ANN202
    yt.now = at
    request = VideoPerformanceRequest(**({"video_url": URL, "campaign_id": "cmp_1"} | req))
    return asyncio.run(vp.video_performance(request, yt, env=_env(tmp_path), shorts=shorts,
                                            now=at)).output  # fmt: skip


def _brief(tmp_path: Path) -> None:
    """브리프 저장분: 인사이트 ins_1 (목표 참고값 5000, 형식 리뷰, 카테고리 교육·책)."""
    conn = sqlite3.connect(tmp_path / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    insight = {
        "insight_id": "ins_1", "rank": 1, "topic": "책추천", "line": "x",
        "category": "교육·책", "shorts_format": "리뷰", "target_views_reference": 5000.0,
        "target_reference_basis": "교육·책 숏츠 조회 중앙값", "legacy_insight_ids": ["old_1"],
    }  # fmt: skip
    row = ("2026-11-06", "r", PUB.isoformat(), json.dumps({"insights": [insight]}))
    conn.execute("INSERT INTO briefs VALUES (?,?,?,?,NULL)", row)
    conn.commit()
    conn.close()


SHORTS = ShortsStatsOutput(status="known", snapshot_at=PUB, by_format=[
    ShortsGroup(name="리뷰", count=12, median_views=2000.0)])  # fmt: skip


def test_parse_video_urls() -> None:
    assert vp.parse_video(URL) == ("youtube", "abcdefghijk")
    assert vp.parse_video("https://youtu.be/abcdefghijk?t=3") == ("youtube", "abcdefghijk")
    assert vp.parse_video("https://www.youtube.com/watch?si=x&v=abcdefghijk") == (
        "youtube", "abcdefghijk")  # fmt: skip
    assert vp.parse_video("https://www.instagram.com/reel/C1x_Y/") == ("instagram", "C1x_Y")
    assert vp.parse_video("https://www.tiktok.com/@cat/video/7301") == ("tiktok", "7301")
    assert vp.parse_video("https://example.com/v/1") is None


def test_register_then_measure_each_checkpoint_in_its_window(tmp_path: Path) -> None:
    _brief(tmp_path)
    yt = FakeYouTube()
    first = _run(tmp_path, yt, PUB + timedelta(minutes=30), insight_id="ins_1", shorts=SHORTS)
    assert first.tracking and first.published_at == PUB and first.title == "책 리뷰 숏츠"
    assert first.status == "unknown" and first.checkpoints[0].reason.startswith("측정 중 (0.5시간")
    assert yt.asked == [["abcdefghijk"]]  # 등록 때 게시 시각 1번
    one = _run(tmp_path, yt, PUB + timedelta(hours=1, minutes=20), campaign_id="cmp_1",
               video_url=None, shorts=SHORTS)  # fmt: skip
    h1 = one.checkpoints[0]
    assert one.status == "known" and h1.checkpoint == "1h" and h1.views == 1333
    assert (h1.likes, h1.comments, h1.hours_after) == (66, 13, 1.33)
    assert h1.views_per_hour == 999.75 and h1.like_rate == 0.0495  # 1333 ÷ 1.333…시간
    assert h1.target_ratio == 0.267 and h1.peer_ratio == 0.666  # 목표 5000, 리뷰 중앙값 2000
    assert one.target_views_reference == 5000.0 and "책추천" in (one.target_reference_basis or "")
    assert one.peer_median_views == 2000.0 and "형식 '리뷰'" in (one.peer_basis or "")
    by = {f.fact_id: f for f in one.facts}
    assert h1.fact_ids and all(by[i].about == "youtube:abcdefghijk" for i in h1.fact_ids)
    assert {by[i].label for i in h1.fact_ids} >= {"1h 조회수", "1h 목표 대비 배수"}
    assert [c.reason for c in one.checkpoints[1:]][0].startswith("측정 중")
    # 같은 창 안에서 다시 불러도 다시 재지 않는다 (시점마다 한 번)
    asked = len(yt.asked)
    _run(tmp_path, yt, PUB + timedelta(hours=1, minutes=40), campaign_id="cmp_1", video_url=None)
    assert len(yt.asked) == asked


def test_missed_window_and_late_registration_say_why(tmp_path: Path) -> None:
    yt = FakeYouTube()
    _run(tmp_path, yt, PUB + timedelta(minutes=10))
    late = _run(tmp_path, yt, PUB + timedelta(hours=30), video_url=None)  # 1일 창(24~26시간) 지남
    reasons = {c.checkpoint: c.reason for c in late.checkpoints}
    assert reasons["1h"] == "놓침: 그 시점에 재지 못함 (데이터 끊김)"
    assert reasons["1d"].startswith("놓침") and reasons["3d"].startswith("측정 중")
    other = tmp_path / "other"
    other.mkdir()
    old = _run(other, FakeYouTube(), PUB + timedelta(days=4))  # 게시 4일 뒤에 처음 등록
    r = {c.checkpoint: c.reason for c in old.checkpoints}
    assert r["1h"] == r["1d"] == r["3d"] == "놓침: 등록 전에 그 시점이 지남"


def test_collector_measures_due_videos_in_one_call(tmp_path: Path) -> None:
    yt = FakeYouTube()
    _run(tmp_path, yt, PUB)
    _run(tmp_path, yt, PUB, video_url="https://youtu.be/zyxwvutsrqp", campaign_id="cmp_2")
    store = vp.store_of(_env(tmp_path))
    yt.now = PUB + timedelta(hours=24, minutes=30)
    saved, problems = asyncio.run(vp.measure_due(store, yt, yt.now))
    assert saved == 2 and problems == [] and yt.asked[-1] == ["abcdefghijk", "zyxwvutsrqp"]
    assert set(store.points("youtube:abcdefghijk")) == {"1d"}
    yt.fail = True
    yt.now = PUB + timedelta(hours=72, minutes=10)
    got = asyncio.run(vp.measure_due(store, yt, yt.now))
    assert got == (0, ["영상 성과를 재지 못함 [QUOTA_EXCEEDED]"])
    store.close()


def test_the_hourly_collector_measures_registered_videos(tmp_path: Path) -> None:
    from data_agent import youtube_collect

    env = _env(tmp_path)
    yt = FakeYouTube()
    assert asyncio.run(youtube_collect.measure_videos(yt, env)) == 0  # 등록 영상 없음
    assert not (tmp_path / "perf.db").exists()
    _run(tmp_path, yt, PUB)
    yt.now = PUB + timedelta(hours=1, minutes=10)
    assert asyncio.run(youtube_collect.measure_videos(yt, env, yt.now)) == 1


def test_hidden_counts_are_unknown_not_zero(tmp_path: Path) -> None:
    yt = FakeYouTube(curve=lambda h: None)  # 조회수 숨김
    _run(tmp_path, yt, PUB)
    out = _run(tmp_path, yt, PUB + timedelta(hours=1, minutes=5), video_url=None)
    h1 = out.checkpoints[0]
    assert h1.status == "unknown" and h1.reason == "조회수를 받지 못함" and h1.views is None
    assert h1.like_rate is None and out.facts == []


@pytest.mark.parametrize(("url", "platform"), [
    ("https://www.instagram.com/reel/C1x_Y/", "instagram"),
    ("https://www.tiktok.com/@cat/video/7301", "tiktok"),
])  # fmt: skip
def test_instagram_and_tiktok_wait_for_a_link(tmp_path: Path, url: str, platform: str) -> None:
    yt = FakeYouTube()
    out = _run(tmp_path, yt, PUB, video_url=url)
    assert out.platform == platform and out.status == "unknown" and not out.tracking
    assert out.reason.startswith("연결 대기") and out.needs and "토큰" in out.needs[0]
    assert all(c.reason == "연결 대기" for c in out.checkpoints) and yt.asked == []


def test_campaign_without_a_video_and_bad_urls_say_why(tmp_path: Path) -> None:
    yt = FakeYouTube()
    none = _run(tmp_path, yt, PUB, video_url=None, campaign_id="cmp_x")
    assert none.status == "unknown" and "등록된 영상이 없음" in (none.reason or "")
    bad = _run(tmp_path, yt, PUB, video_url="https://example.com/v/1")
    assert bad.reason == "영상 주소를 알아보지 못함 (YouTube·인스타그램·틱톡 주소만)"
    with pytest.raises(ValueError):
        VideoPerformanceRequest()


def test_performance_table_groups_by_insight_format_and_category(tmp_path: Path) -> None:
    """성과 모으기: 인사이트·형식·카테고리별 목표 대비 배수 중앙값 (영상마다 가장 늦게 잰 시점)."""
    _brief(tmp_path)
    yt = FakeYouTube()
    _run(tmp_path, yt, PUB, insight_id="ins_1")
    _run(tmp_path, yt, PUB, video_url="https://youtu.be/zyxwvutsrqp", campaign_id="cmp_2",
         insight_id="ins_1")  # fmt: skip
    _run(tmp_path, yt, PUB, video_url="https://youtu.be/testtesttes", campaign_id="test-video-1",
         insight_id="ins_1", test=True)  # 시험 영상: 표에서 뺀다  # fmt: skip
    _run(tmp_path, yt, PUB, video_url="https://youtu.be/noinsightxx", campaign_id="cmp_3")
    out = _run(tmp_path, yt, PUB + timedelta(hours=24, minutes=30), video_url=None)
    table = {(g.key, g.value): g for g in out.performance_table}
    ins = table[("insight", "ins_1")]
    assert (ins.videos, ins.measured) == (2, 2)  # 시험 영상·인사이트 없는 영상은 안 셈
    assert ins.median_target_ratio == 4.9  # 1일 24.5시간: 조회 24500 ÷ 목표 5000
    assert table[("format", "리뷰")].videos == 2 and table[("category", "교육·책")].videos == 2
    assert all(g.value != "test" for g in out.performance_table)


def test_brief_shows_how_many_videos_are_being_collected(tmp_path: Path) -> None:
    from ax_agent_template.llm import MockLLMClient
    from ax_contracts.schemas.data import TrendInsightBriefRequest, TrendPopularOutput
    from data_agent.skills import trend_insight_brief as brief

    yt = FakeYouTube()
    _run(tmp_path, yt, PUB)
    _run(tmp_path, yt, PUB, video_url="https://youtu.be/testtesttes", test=True)

    async def ages(keywords):  # noqa: ANN001, ANN202
        from ax_contracts.schemas.data import TrendAgeOutput

        return TrendAgeOutput(status="known", items=[])

    out = asyncio.run(brief.make_brief(
        TrendInsightBriefRequest(), MockLLMClient(), env=_env(tmp_path) | {
            brief.DB_ENV: str(tmp_path / "b.db")},
        popular=TrendPopularOutput(items=[]), popular_reason="x", rising=[], age_fn=ages,
        promo=None, catalog=None, now=PUB,
    )).output  # fmt: skip
    assert out.status == "unknown"  # 후보가 없으면 빈 브리프 (표시는 아래 경우)
    from ax_contracts.schemas.data import PopularTopic

    popular = TrendPopularOutput(items=[PopularTopic(rank=1, keyword="롤", topic_category="게임",
                                                     youtube_videos=3)])  # fmt: skip
    full = asyncio.run(brief.make_brief(
        TrendInsightBriefRequest(), MockLLMClient(), env=_env(tmp_path) | {
            brief.DB_ENV: str(tmp_path / "b2.db")},
        popular=popular, popular_reason=None, rising=[], age_fn=ages, promo=None, catalog=None,
        now=PUB,
    )).output  # fmt: skip
    assert "성과 데이터 쌓는 중(영상 1개, 브리프 점수에는 아직 안 넣음)" in full.limits


def test_test_videos_are_marked_and_not_counted(tmp_path: Path) -> None:
    yt = FakeYouTube()
    out = _run(tmp_path, yt, PUB, campaign_id="test-video-1", test=True)
    assert out.test and any("시험 영상" in x for x in out.limits)
    assert vp.counted_videos(_env(tmp_path)) == 0
    _run(tmp_path, yt, PUB, video_url="https://youtu.be/zyxwvutsrqp", campaign_id="cmp_2")
    assert vp.counted_videos(_env(tmp_path)) == 1
    assert vp.counted_videos({vp.DB_ENV: str(tmp_path / "none.db")}) == 0
    assert not (tmp_path / "none.db").exists()  # 세기만 하고 파일을 만들지 않는다

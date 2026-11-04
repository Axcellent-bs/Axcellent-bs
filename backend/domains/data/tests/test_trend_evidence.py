"""trend_evidence: 지금 인기 주제·영상의 "왜 뜨나" 근거.

실제 YouTube·LLM을 부르지 않는다 (가짜 커넥터·가짜 LLM).
- 영상 숫자(경과 시간·비율·구독자 대비 배수·숏츠 여부)는 코드 계산, 모르면 None + 이유
- 네이버 상승 시작일 = 판정 구간에서 평소 범위를 처음 넘은 날
- 구독자 수: 저장된 값 재사용, 없는 것만 묶어 받기, 하루 상한
- 한 줄: 묶어서 LLM 한 번·하루 캐시, 숫자 검증·단정 말 검사 실패면 고정 문장, 근거 없으면 unknown
- RSS 관련 뉴스의 출처·주소 저장
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import (
    PopularTopic,
    PopularVideo,
    TrendEvidence,
    TrendPoint,
    TrendVerdict,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.google_trends import parse_feed
from data_agent.skills import trend_evidence as ev
from data_agent.trending_store import TrendingStore, news_links
from data_agent.youtube_store import StoredVideo

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)
TODAY = date(2026, 11, 4)


def _stored(vid: str = "v1", channel: str = "c1", views: int | None = 10000) -> StoredVideo:
    return StoredVideo(vid, channel, NOW - timedelta(hours=10), views, 500, 50, 1, ())


# ---------------------------------------------------------------- 영상


def test_video_numbers_are_computed_by_code() -> None:
    v = PopularVideo(video_id="v1", views=10000, duration_seconds=45)
    out = ev.video_evidence(v, _stored(), speed=120.0, subs={"c1": 2000}, subs_reason=None,
                            fmt="챌린지", short_max=60, now=NOW)  # fmt: skip
    assert out.hours_since_published == 10.0 and out.views_per_hour == 120.0
    assert out.like_rate == 0.05 and out.comment_rate == 0.005
    assert out.channel_subscribers == 2000 and out.views_per_subscriber == 5.0
    assert out.is_short is True and out.shorts_format == "챌린지"
    assert out.evidence_reason is None


def test_unknown_video_numbers_stay_none_with_reasons() -> None:
    v = PopularVideo(video_id="v1", views=None, duration_seconds=None)
    hidden = ev.video_evidence(v, _stored(views=None), speed=None, subs={"c1": None},
                               subs_reason=None, fmt="챌린지", short_max=60, now=NOW)  # fmt: skip
    assert hidden.views_per_subscriber is None and hidden.like_rate is None
    assert hidden.is_short is None and hidden.shorts_format is None
    assert hidden.evidence_reason is not None
    assert "채널이 구독자 수를 숨김" in hidden.evidence_reason
    assert "시간당 조회 모름" in hidden.evidence_reason and "길이 모름" in hidden.evidence_reason
    capped = ev.video_evidence(PopularVideo(video_id="v1", views=10, duration_seconds=300),
                               _stored(), speed=1.0, subs={}, subs_reason="상한",
                               fmt="챌린지", short_max=60, now=NOW)  # fmt: skip
    assert capped.is_short is False and capped.shorts_format is None  # 롱폼은 형식 없음
    assert capped.evidence_reason == "상한"


# ---------------------------------------------------------------- 네이버 상승 시작


def _verdict(status: str = "rising") -> TrendVerdict:
    days = [date(2026, 10, 30) + timedelta(days=i) for i in range(5)]
    return TrendVerdict(
        group_name="t1", status=status, ratio=2.95,  # type: ignore[arg-type]
        evidence=TrendEvidence(latest_period=days[-1], latest_value=21.0, baseline_value=10.0,
                               baseline_points=56, band_high=14.0),
        series=[TrendPoint(day=d, value=v)
                for d, v in zip(days, [20, 12, 13, 15, 21], strict=True)],
        window_from=date(2026, 10, 31),
        unknown_reason=None if status != "unknown" else "검색량 적음",
    )  # fmt: skip


def test_rising_start_is_the_first_window_day_above_the_usual_band() -> None:
    assert ev.rising_start(_verdict()) == (date(2026, 11, 2), None)  # 10-30은 기준선 구간
    assert ev.rising_start(None) == (None, "오늘 네이버 판정이 없는 주제")
    assert ev.rising_start(_verdict("unknown")) == (None, "검색량 적음")


# ---------------------------------------------------------------- 구독자


class Channels:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    async def channels(self, ids):  # noqa: ANN001, ANN201
        self.calls.append(list(ids))
        if self.fail:
            raise ConnectorUnavailable("QUOTA_EXCEEDED", "quota")
        return {i: (None if i == "hidden" else 1000) for i in ids if i != "gone"}


def _subs(tmp_path: Path, ids: list[str], yt, *, cap: int = 2):  # noqa: ANN001, ANN202
    store = ev.EvidenceStore(tmp_path / "ev.db")
    try:
        return asyncio.run(ev.subscribers(str(tmp_path / "yt.db"), ids, youtube=yt, store=store,
                                          cap=cap, now=NOW, today=TODAY, queries=[]))  # fmt: skip
    finally:
        store.close()


def test_subscribers_are_fetched_once_then_reused(tmp_path: Path) -> None:
    yt = Channels()
    got, why = _subs(tmp_path, ["c1", "c2", "c1", "hidden"], yt)
    assert yt.calls == [["c1", "c2", "hidden"]] and why is None
    assert got == {"c1": 1000, "c2": 1000, "hidden": None}
    again = Channels()
    got2, _ = _subs(tmp_path, ["c1", "hidden"], again)
    assert again.calls == [] and got2 == {"c1": 1000, "hidden": None}  # 저장된 값 재사용


def test_subscriber_fetch_respects_the_daily_cap_and_failures(tmp_path: Path) -> None:
    yt = Channels()
    got, why = _subs(tmp_path / "a", ["c1"], yt, cap=0)
    assert yt.calls == [] and got == {} and why == "구독자 받기 하루 상한(0번)에 닿아 받지 않음"
    _, why2 = _subs(tmp_path / "b", ["c1"], Channels(fail=True))
    assert why2 == "구독자 받기 실패 [QUOTA_EXCEEDED]"
    _, why3 = _subs(tmp_path / "c", ["c1", "gone"], Channels())
    assert why3 == "YouTube가 채널 1개를 주지 않음"
    _, why4 = _subs(tmp_path / "d", ["c1"], object())
    assert why4 == "YouTube 커넥터가 없어 구독자 수를 받지 않음"


# ---------------------------------------------------------------- 한 줄


class WhyLLM(MockLLMClient):
    def __init__(self, lines: dict[str, str] | None = None, fail: bool = False) -> None:
        super().__init__()
        self.lines = lines or {}
        self.fail = fail
        self.asked = 0
        self.blocks: list[dict] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked += 1
        self.blocks.append(dict(untrusted or {}))
        if self.fail:
            raise RuntimeError("down")
        return ev._Why(items=[ev._WhyItem(keyword=k, line=v) for k, v in self.lines.items()])


def _topic(name: str, **kw) -> PopularTopic:  # noqa: ANN003
    return PopularTopic(rank=1, keyword=name, **kw)


def _why(tmp_path: Path, topics: list[PopularTopic], llm: WhyLLM) -> None:
    store = ev.EvidenceStore(tmp_path / "ev.db")
    try:
        asyncio.run(ev.why_lines(topics, llm, store=store, today=TODAY, context_id=None,
                                 queries=[]))  # fmt: skip
    finally:
        store.close()


def test_why_lines_use_only_evidence_numbers_in_one_call(tmp_path: Path) -> None:
    topics = [
        _topic("t1", youtube_videos=8, rising_ratio=2.95),
        _topic("롤", youtube_videos=5, trending_rank=3),
        _topic("숫자 지어냄", youtube_videos=4),
        _topic("단정", youtube_videos=6),
        _topic("근거 없음"),
    ]
    llm = WhyLLM({
        "t1": "YouTube 인기 영상 8개, 네이버 평소의 2.95배 근거가 있어요",
        "롤": "인기 영상 5개와 Google 인기 검색어 3위 근거가 있어요",
        "숫자 지어냄": "인기 영상 40개 근거가 있어요",
        "단정": "결승 때문에 인기 영상 6개가 떴어요",
    })  # fmt: skip
    _why(tmp_path, topics, llm)
    assert llm.asked == 1 and len(llm.blocks[0]) == 4  # 근거 없는 주제는 묻지 않는다
    t1, lol, made, blame, none = topics
    assert t1.why_status == "llm" and t1.why_line is not None and "2.95배" in t1.why_line
    assert lol.why_status == "llm"
    assert made.why_status == "fixed" and made.why_line == "YouTube 인기 영상 수 4 근거가 있어요"
    assert blame.why_status == "fixed" and "고정 문장" in (blame.why_reason or "")
    assert none.why_status == "unknown" and none.why_line is None and none.why_reason


def test_why_lines_are_cached_for_the_day(tmp_path: Path) -> None:
    llm = WhyLLM({"t1": "YouTube 인기 영상 8개 근거가 있어요"})
    _why(tmp_path, [_topic("t1", youtube_videos=8)], llm)
    again = [_topic("t1", youtube_videos=8)]
    _why(tmp_path, again, llm)
    assert llm.asked == 1 and again[0].why_status == "llm"
    changed = [_topic("t1", youtube_videos=9)]  # 근거 숫자가 바뀌면 다시 묻는다
    _why(tmp_path, changed, WhyLLM({"t1": "YouTube 인기 영상 9개 근거가 있어요"}))
    assert changed[0].why_line == "YouTube 인기 영상 9개 근거가 있어요"


def test_llm_failure_gives_the_fixed_sentence(tmp_path: Path) -> None:
    topics = [_topic("t1", youtube_videos=8, rising_ratio=2.95)]
    _why(tmp_path, topics, WhyLLM(fail=True))
    assert topics[0].why_status == "fixed"
    assert topics[0].why_line == "YouTube 인기 영상 수 8 · 네이버 평소 대비 배수 2.95 근거가 있어요"
    assert topics[0].why_reason == "LLM 호출 실패 (RuntimeError)라 고정 문장"


# ---------------------------------------------------------------- RSS 관련 뉴스


FEED = """<?xml version="1.0"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>t1</title><ht:approx_traffic>500+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>T1 결승</ht:news_item_title>
<ht:news_item_url>https://news.example/a</ht:news_item_url>
<ht:news_item_source>예시뉴스</ht:news_item_source></ht:news_item>
<ht:news_item><ht:news_item_title>링크 이상</ht:news_item_title>
<ht:news_item_url>javascript:x</ht:news_item_url></ht:news_item>
</item></channel></rss>""".encode()


def test_feed_news_source_and_url_are_saved_and_read(tmp_path: Path) -> None:
    items = parse_feed(FEED)
    assert items[0].news == (
        ("T1 결승", "예시뉴스", "https://news.example/a"),
        ("링크 이상", None, None),  # http(s)가 아닌 주소는 버린다
    )
    store = TrendingStore(tmp_path / "tr.db")
    store.save(NOW, "KR", items)
    store.close()
    found = news_links(tmp_path / "tr.db", "KR", NOW, ["t1"])
    assert found["t1"][0] == {
        "title": "T1 결승",
        "source": "예시뉴스",
        "url": "https://news.example/a",
    }
    assert [n.title for n in ev.topic_news(found["t1"])] == ["T1 결승", "링크 이상"]


def test_labeled_keywords_match_and_missing_answers_are_not_cached(tmp_path: Path) -> None:
    topics = [_topic("t1", youtube_videos=8), _topic("롤", youtube_videos=5)]
    llm = WhyLLM({"주제1 t1": "YouTube 인기 영상 8개 근거가 있어요"})  # 롤은 답 없음
    _why(tmp_path, topics, llm)
    assert topics[0].why_status == "llm" and topics[1].why_status == "fixed"
    again = [_topic("롤", youtube_videos=5)]
    llm2 = WhyLLM({"롤": "YouTube 인기 영상 5개 근거가 있어요"})
    _why(tmp_path, again, llm2)
    assert llm2.asked == 1 and again[0].why_status == "llm"  # 빈 답은 캐시하지 않아 다시 묻는다

"""shorts_stats 화면 카드 숏츠(`by_category[].videos`). 실제 YouTube·LLM을 부르지 않는다.

- 분류마다 설정 개수(`[shorts] screen_videos`, 기본 10)까지, 이미 모은 숏츠 안에서만
  (더 검색하지 않음)
- 영상마다 구독자 대비 조회(channels.list, 저장 재사용, 하루 상한), 분류, 광고 재료 칸
- 통계 숫자(수·평균·중앙값·top 등)는 화면 칸을 더하기 전과 같다 (전·후 비교: fixtures 기록)
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template.llm import MockLLMClient
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.shorts_stats import (
    ShortsStatsRequest,
    _FormatItem,
    _Formats,
    shorts_stats,
)
from data_agent.skills.topic_category import _Categories, _Item
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)
GOLDEN = Path(__file__).parent / "fixtures" / "shorts_stats_before_screen_videos.json"
# 화면 칸 (이번에 더한 것). 전·후 비교에서 뺀다
NEW_VIDEO_KEYS = {
    "category", "channel_subscribers", "views_per_subscriber", "subscriber_reason",
    "ad_usable", "caution_types", "caution_reason",
}  # fmt: skip
HIDDEN = "cs3"  # 구독자 수를 숨긴 채널


def _title(vid: str) -> str:
    if vid.startswith("s10_"):
        return f"{vid} 노래 숏츠"
    if vid.endswith("_1"):
        return f"{vid} 손흥민 선수 숏츠"
    return f"{vid} 숏츠"


def _stat(vid: str, views: int, seconds: int | None, channel: str, cat: str) -> VideoStat:
    return VideoStat(vid, channel, cat, NOW - timedelta(days=1), views, 1, 1, ("tag",),
                     title=_title(vid), rank=1, channel_title="채널", duration_seconds=seconds,
                     thumbnail_url=f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg")  # fmt: skip


class FakeLLM(MockLLMClient):
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        data = dict(untrusted or {})
        if schema is _Categories:
            items = []
            for key, name in data.items():
                if not key.startswith("소재"):
                    continue
                n = key[2:]
                if "노래" in name:
                    items.append(_Item(id=n, category="K-pop·음악"))
                elif "손흥민" in name:
                    items.append(_Item(id=n, category="연예·스포츠", real_entity=True,
                                       entity_names=["손흥민"], is_person=True))  # fmt: skip
                else:
                    items.append(_Item(id=n, category="게임"))
            return _Categories(items=items)
        n = [k[2:] for k in data if k.startswith("영상")]
        return _Formats(items=[_FormatItem(id=i, format="챌린지") for i in n])


class ScreenYouTube:
    """검색 2개 카테고리(20: 12개, 10: 3개), 다시 조회하면 조회수가 늘고, 채널 구독자를 준다."""

    def __init__(self, *, channels: bool = True) -> None:
        self.quota_used = 0
        self.searched: list[str] = []
        self.channel_calls: list[list[str]] = []
        if not channels:
            self.channels = None  # type: ignore[assignment]

    @staticmethod
    def _found(cat: str) -> list[VideoStat]:
        n = 12 if cat == "20" else 3
        return [_stat(f"s{cat}_{i}", 1000 * (i + 1), 30, f"cs{i % 4}", cat) for i in range(n)]

    async def search_shorts(self, category_id: str, published_after: datetime,
                            language: str | None = None) -> list[VideoStat]:  # fmt: skip
        self.searched.append(category_id)
        self.quota_used += 100
        return self._found(category_id) if category_id in ("20", "10") else []

    async def videos(self, ids: list[str]) -> list[VideoStat]:
        self.quota_used += 1
        out = []
        for vid in ids:
            cat, i = vid[1:].split("_")
            out.append(_stat(vid, 1000 * (int(i) + 1) + 500, 30, f"cs{int(i) % 4}", cat))
        return out

    async def channels(self, ids: list[str]) -> dict[str, int | None]:
        self.channel_calls.append(list(ids))
        self.quota_used += 1
        return {c: (None if c == HIDDEN else 1000) for c in ids}


def _env(tmp_path: Path, extra: str = "") -> dict[str, str]:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    # 인기 영상 카테고리: 20이 가장 많고 10도 있다 (검색은 이 둘)
    popular = [_stat(f"p{i}", 100 * (i + 1), s, f"cp{i % 2}", "10" if i == 5 else "20")
               for i, s in enumerate([15, 30, 45, 300, 50, 20])]  # fmt: skip
    store.save(NOW - timedelta(hours=2), [Row(s, ("popular",)) for s in popular])
    store.save(
        NOW - timedelta(hours=1),
        [Row(VideoStat(**{**s.__dict__, "view_count": (s.view_count or 0) + 360}), ("popular",))
         for s in popular],
    )  # fmt: skip
    store.close()
    config = tmp_path / "trends.toml"
    config.write_text("[shorts]\nmax_seconds = 60\nmin_sample = 5\nlanguage = \"\"\n" + extra,
                      encoding="utf-8")  # fmt: skip
    return {
        "DATA_YOUTUBE_DB": str(db),
        "DATA_TREND_CONFIG": str(config),
        "DATA_SHORTS_DB": str(tmp_path / "shorts.db"),
        "DATA_POPULAR_VIDEOS_DB": str(tmp_path / "popular_videos.db"),
        "DATA_TOPIC_CATEGORY_DB": str(tmp_path / "topics.db"),
    }


def run_scenario(tmp_path: Path, extra: str = "", youtube: ScreenYouTube | None = None):  # noqa: ANN201
    """검색한 날(NOW) 한 번, 2시간 뒤 다시 조회 한 번. 두 번째 응답을 돌려준다."""
    env = _env(tmp_path, extra)
    yt = youtube or ScreenYouTube()
    asyncio.run(shorts_stats(ShortsStatsRequest(), yt, FakeLLM(), env=env, now=NOW))
    return asyncio.run(shorts_stats(ShortsStatsRequest(), yt, FakeLLM(), env=env, now=LATER)).output


def stats_view(output: Any) -> dict[str, Any]:
    """통계 칸만 (화면 칸 `videos`와 영상의 새 칸을 뺀 응답)."""
    data = output.model_dump(mode="json")
    # 예전 응답에 없던 칸: 이 시나리오엔 하위 주제 저장분이 없어 0이어야 한다
    assert data.pop("subtopic_shorts") == 0
    # 예전 응답에 없던 칸 (홍보 영상 근거: 전체 시간당 조회 중앙값·최근 N일 게시된 수)
    for k in ("median_views_per_hour", "median_views_per_hour_count", "recent_days",
              "median_views_per_hour_reason"):
        data.pop(k, None)
    for key in ("by_category", "by_format"):
        for group in data[key]:
            group.pop("videos", None)
            group.pop("recent_count", None)
            group.pop("recent_unknown", None)
            for k in ("promo_median_views_per_hour", "promo_velocity_videos", "promo_reason"):
                group.pop(k, None)
            for v in group["top"]:
                for k in NEW_VIDEO_KEYS:
                    v.pop(k, None)
    return data


def _group(out: Any, name: str) -> Any:
    return next(g for g in out.by_category if g.name == name)


def test_stats_numbers_are_the_same_as_before(tmp_path: Path) -> None:
    before = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert stats_view(run_scenario(tmp_path)) == before


def test_screen_videos_per_category_up_to_the_setting(tmp_path: Path) -> None:
    out = run_scenario(tmp_path)
    game = _group(out, "게임")
    assert game.status == "known" and len(game.top) == 3
    assert len(game.videos) == 10  # 기본 10개 (게임 숏츠는 더 많다)
    views = [v.views for v in game.videos]
    assert views == sorted(views, reverse=True)
    assert [v.video_id for v in game.top] == [v.video_id for v in game.videos[:3]]
    # 표본 부족 칸도 화면 카드는 준다 (통계는 unknown 그대로)
    music = _group(out, "K-pop·음악")
    assert music.status == "unknown" and music.top == [] and len(music.videos) == 3
    assert all(g.videos == [] for g in out.by_format)


def test_screen_videos_setting_and_no_extra_search(tmp_path: Path) -> None:
    yt = ScreenYouTube()
    out = run_scenario(tmp_path, "screen_videos = 4\n", yt)
    assert all(len(g.videos) <= 4 for g in out.by_category)
    assert len(_group(out, "게임").videos) == 4
    assert len(yt.searched) == len(set(yt.searched))  # 카테고리마다 하루 한 번만 (더 검색하지 않음)


def test_views_per_subscriber_and_hidden_channel(tmp_path: Path) -> None:
    yt = ScreenYouTube()
    out = run_scenario(tmp_path, youtube=yt)
    videos = [v for g in out.by_category for v in g.videos]
    by_id = {v.video_id: v for v in videos}
    shown = by_id["s20_8"]  # 채널 cs0, 구독자 1000
    assert shown.channel_subscribers == 1000
    assert shown.views_per_subscriber == round(shown.views / 1000, 2)
    hidden = by_id["s20_7"]  # 채널 cs3: 숨김
    assert hidden.views_per_subscriber is None and hidden.channel_subscribers is None
    assert hidden.subscriber_reason == "채널이 구독자 수를 숨김"
    # 채널당 한 번 받아 저장하고, 다음 실행은 저장분을 쓴다
    asked = [c for call in yt.channel_calls for c in call]
    assert len(asked) == len(set(asked))


def test_subscriber_daily_cap_gives_a_reason(tmp_path: Path) -> None:
    out = run_scenario(tmp_path, "channel_calls = 0\n")
    videos = [v for g in out.by_category for v in g.videos]
    assert videos and all(v.views_per_subscriber is None for v in videos)
    assert all("상한" in (v.subscriber_reason or "") for v in videos)


def test_category_and_ad_caution_per_video(tmp_path: Path) -> None:
    out = run_scenario(tmp_path)
    videos = {v.video_id: v for g in out.by_category for v in g.videos}
    song = videos["s10_0"]
    assert song.category == "K-pop·음악"
    assert song.ad_usable is False and song.caution_types == ["music"]
    person = videos["s20_1"]
    assert person.category == "연예·스포츠" and person.caution_types == ["real_person"]
    assert person.ad_usable is False and "손흥민" in (person.caution_reason or "")
    plain = videos["s20_8"]
    assert plain.category == "게임" and plain.ad_usable is True and plain.caution_types == []
    assert plain.thumbnail_url and plain.video_url


def test_recent_window_is_the_search_window(tmp_path: Path) -> None:
    """홍보 영상 최근 기간 = 숏츠 검색 기간(search_days). 게시 시각(하루 전)으로 센다."""
    out = run_scenario(tmp_path, "search_days = 10\n")
    assert out.recent_days == 10
    groups = [g for g in out.by_format if g.count]
    assert groups
    for g in groups:
        assert (g.recent_count or 0) + (g.recent_unknown or 0) == g.count
    assert sum(g.recent_count or 0 for g in groups) > 0  # 하루 전 게시분은 10일 안
    assert run_scenario(tmp_path / "default").recent_days == 7  # 기본 search_days

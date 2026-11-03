"""shorts_stats: 숏츠 통계. 실제 YouTube·LLM을 부르지 않는다 (저장된 스냅샷 + 가짜 검색·LLM).

- 길이 기준(설정, 기본 60초)으로 숏츠를 고른다. 길이를 모르면 빼고 수만 센다
- 표본이 기준보다 적은 묶음 → unknown + 표본 부족
- 검색 실패(할당량 초과 포함) → 그 검색만 unknown, 나머지는 그대로. 같은 날은 다시 검색하지 않는다
- 형식 분류는 묶어서, 저장해 재사용한다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.shorts_stats import (
    ShortsConfig,
    ShortsStatsRequest,
    ShortsStore,
    _FormatItem,
    _Formats,
    _Short,
    group_stats,
    shorts_stats,
)
from data_agent.skills.topic_category import _Categories, _Item
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)


def _stat(vid: str, views: int, seconds: int | None, *, cat: str = "20") -> VideoStat:
    return VideoStat(vid, "ch", cat, NOW - timedelta(days=1), views, 1, 1, ("tag",),
                     title=f"{vid} 숏츠", rank=1, channel_title="채널",
                     duration_seconds=seconds)  # fmt: skip


class FakeLLM(MockLLMClient):
    def __init__(self) -> None:
        super().__init__()
        self.format_calls = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        data = dict(untrusted or {})
        if schema is _Categories:
            n = [k[2:] for k in data if k.startswith("소재")]
            return _Categories(items=[_Item(id=i, category="게임") for i in n])
        self.format_calls += 1
        n = [k[2:] for k in data if k.startswith("영상")]
        return _Formats(items=[_FormatItem(id=i, format="챌린지") for i in n])


class FakeYouTube:
    def __init__(self, fail: dict[str, str] | None = None) -> None:
        self.fail = fail or {}
        self.quota_used = 0
        self.calls: list[str] = []

    async def search_shorts(self, category_id: str, published_after: datetime) -> list[VideoStat]:
        self.calls.append(category_id)
        self.quota_used += 101
        if category_id in self.fail:
            raise ConnectorUnavailable(self.fail[category_id], "x")
        return [_stat(f"s{category_id}_{i}", 1000 * (i + 1), 30, cat=category_id) for i in range(6)]


def _env(tmp_path: Path, popular: list[VideoStat], extra: str = "") -> dict[str, str]:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    store.save(NOW - timedelta(hours=2), [Row(s, ("popular",)) for s in popular])
    store.save(
        NOW - timedelta(hours=1),
        [Row(VideoStat(**{**s.__dict__, "view_count": (s.view_count or 0) + 360}), ("popular",))
         for s in popular],
    )  # fmt: skip
    store.close()
    config = tmp_path / "trends.toml"
    config.write_text("[shorts]\nmax_seconds = 60\nmin_sample = 5\n" + extra, encoding="utf-8")
    return {
        "DATA_YOUTUBE_DB": str(db),
        "DATA_TREND_CONFIG": str(config),
        "DATA_SHORTS_DB": str(tmp_path / "shorts.db"),
    }


def _run(env: dict[str, str], youtube: FakeYouTube, llm: FakeLLM | None = None):  # noqa: ANN202
    return asyncio.run(
        shorts_stats(ShortsStatsRequest(), youtube, llm or FakeLLM(), env=env, now=NOW)
    ).output


def test_shorts_are_chosen_by_length(tmp_path: Path) -> None:
    popular = [_stat(f"p{i}", 100, s) for i, s in enumerate([15, 60, 61, 300, None, 45])]
    out = _run(_env(tmp_path, popular, "search_calls = 0\n"), FakeYouTube())
    assert out.popular_shorts == 3  # 15·60·45초 (60초 이하, 61초는 아님)
    assert out.unknown_length == 1  # 길이를 모르면 빼고 센다
    assert out.search_status == "unknown" and "search_calls = 0" in (out.search_reason or "")
    assert "길이로 판단" in out.limitation and "이미 뜬 영상끼리" in out.limitation


def test_small_groups_are_unknown_with_sample_shortage(tmp_path: Path) -> None:
    popular = [_stat(f"p{i}", 100 * (i + 1), 30) for i in range(3)]
    out = _run(_env(tmp_path, popular, "search_calls = 0\n"), FakeYouTube())
    [game] = out.by_category
    assert (game.name, game.status, game.count) == ("게임", "unknown", 3)
    assert game.reason == "표본 부족 (3개, 기준 5개)" and game.avg_views is None


def test_group_stats_compute_averages_and_top_three() -> None:
    shorts = [
        _Short(
            f"v{i}",
            f"제목{i}",
            "채널",
            100 * i,
            10.0 * i if i < 3 else None,
            (),
            "popular",
            category="게임",
            format="챌린지",
        )  # fmt: skip
        for i in range(1, 7)
    ]
    [g] = group_stats(shorts, "category", ["게임"], ShortsConfig())
    assert (g.count, g.avg_views) == (6, 350.0)
    assert (g.avg_views_per_hour, g.velocity_videos) == (15.0, 2)  # 잰 것만 평균
    assert [t.title for t in g.top] == ["제목6", "제목5", "제목4"]


def test_search_failure_is_unknown_only_for_that_search_and_runs_once_a_day(
    tmp_path: Path,
) -> None:
    popular = [_stat(f"p{i}", 100, 300, cat="20") for i in range(3)] + [
        _stat("q1", 100, 300, cat="24"),
        _stat("q2", 100, 300, cat="10"),
    ]
    env = _env(tmp_path, popular)
    youtube = FakeYouTube(fail={"24": "UNAVAILABLE"})
    out = _run(env, youtube)
    by = {s.youtube_category_id: s for s in out.search}
    assert by["20"].status == "known" and by["20"].shorts == 6
    assert by["24"].status == "unknown" and by["24"].reason == "검색 실패 [UNAVAILABLE]"
    assert by["10"].status == "known"
    assert out.searched_shorts == 12 and out.quota_used == 303
    again = _run(env, youtube)
    # 다시 부르지 않는다 (검색 순서: 많이 나온 카테고리 순, 같으면 id 순)
    assert again.search_status == "cached" and youtube.calls == ["20", "10", "24"]
    assert again.searched_shorts == 12


def test_quota_exceeded_stops_the_remaining_searches(tmp_path: Path) -> None:
    popular = [_stat("a", 1, 300, cat="20"), _stat("b", 1, 300, cat="24")]
    youtube = FakeYouTube(fail={"20": "QUOTA_EXCEEDED"})
    out = _run(_env(tmp_path, popular), youtube)
    reasons = {s.youtube_category_id: s.reason for s in out.search}
    assert reasons == {"20": "할당량 초과 [QUOTA_EXCEEDED]", "24": "할당량을 넘어 검색하지 않음"}
    assert youtube.calls == ["20"]


def test_formats_are_classified_in_batches_and_stored(tmp_path: Path) -> None:
    popular = [_stat(f"p{i}", 100, 30) for i in range(6)]
    env = _env(tmp_path, popular, "search_calls = 0\n")
    llm = FakeLLM()
    out = _run(env, FakeYouTube(), llm)
    assert llm.format_calls == 1 and out.by_format[0].name == "챌린지"
    _run(env, FakeYouTube(), llm)
    assert llm.format_calls == 1  # 저장된 형식을 다시 쓴다
    store = ShortsStore(tmp_path / "shorts.db")
    assert len(store.formats([f"p{i}" for i in range(6)], NOW - timedelta(days=1))) == 6
    store.close()


# ------------------------------------------------ 검색 숏츠 다시 조회 (시간당 조회 증가)


class RecheckYouTube(FakeYouTube):
    """검색 결과를 다시 조회(videos.list)하면 조회수가 `grow`만큼 늘어 있다."""

    def __init__(self, grow: int = 200, fail: str | None = None) -> None:
        super().__init__()
        self.grow = grow
        self.recheck_fail = fail
        self.rechecked: list[list[str]] = []

    async def videos(self, ids: list[str]) -> list[VideoStat]:
        self.rechecked.append(list(ids))
        self.quota_used += 1
        if self.recheck_fail:
            raise ConnectorUnavailable(self.recheck_fail, "x")
        out = []
        for vid in ids:
            cat, i = vid[1:].split("_")
            out.append(_stat(vid, 1000 * (int(i) + 1) + self.grow, 30, cat=cat))
        return out


def _run_at(env: dict[str, str], youtube: FakeYouTube, at: datetime):  # noqa: ANN202
    return asyncio.run(
        shorts_stats(ShortsStatsRequest(), youtube, FakeLLM(), env=env, now=at)
    ).output


def test_searched_shorts_get_a_speed_only_after_two_looks(tmp_path: Path) -> None:
    popular = [_stat(f"p{i}", 100, 300, cat="20") for i in range(3)]
    env = _env(tmp_path, popular, "search_calls = 1\nrecheck_min_hours = 1\n")
    youtube = RecheckYouTube(grow=200)
    first = _run_at(env, youtube, NOW)
    assert first.searched_shorts == 6 and youtube.rechecked == []  # 방금 봐서 다시 보지 않음
    [g] = first.by_format
    assert g.avg_views_per_hour is None and g.velocity_pending == 6
    assert g.velocity_reason == "다음 조회 대기 6개 (한 번만 봄)"
    later = _run_at(env, youtube, NOW + timedelta(hours=2))
    assert later.rechecked == 6 and len(youtube.rechecked) == 1
    [g2] = later.by_format
    assert (g2.avg_views_per_hour, g2.velocity_videos, g2.velocity_pending) == (100.0, 6, 0)
    assert g2.top[0].views_per_hour == 100.0  # 200회 ÷ 2시간


def test_recheck_failure_leaves_shorts_waiting(tmp_path: Path) -> None:
    popular = [_stat("p0", 100, 300, cat="20")]
    env = _env(tmp_path, popular, "search_calls = 1\n")
    _run_at(env, RecheckYouTube(), NOW)
    failing = RecheckYouTube(fail="QUOTA_EXCEEDED")
    out = _run_at(env, failing, NOW + timedelta(hours=2))
    assert out.recheck_reason == "다시 조회 실패 [QUOTA_EXCEEDED] (그 숏츠는 다음 조회 대기)"
    assert out.rechecked == 0 and out.by_format[0].velocity_pending == 6


def test_recheck_respects_the_cap(tmp_path: Path) -> None:
    popular = [_stat("p0", 100, 300, cat="20")]
    env = _env(tmp_path, popular, "search_calls = 1\nrecheck_videos = 4\n")
    youtube = RecheckYouTube()
    _run_at(env, youtube, NOW)
    out = _run_at(env, youtube, NOW + timedelta(hours=2))
    assert out.rechecked == 4 and len(youtube.rechecked[0]) == 4
    assert out.by_format[0].velocity_pending == 2  # 상한 밖 2개는 다음 조회 대기

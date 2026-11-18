"""숏츠 표본의 빠진 게시 시각 채우기. 가짜 커넥터·가짜 HTTP만 쓴다 (실제 API를 부르지 않음)."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from data_agent import shorts_backfill as sb
from data_agent import youtube_quota
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat, YouTubeConnector
from data_agent.skills.shorts_stats import ShortsStore

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)


@dataclass
class _Found:
    """숏츠 검색 저장분 한 줄 (save_search가 읽는 칸)."""

    video_id: str
    title: str = "책장 털기 챌린지"
    channel_title: str = "채널"
    duration_seconds: int | None = 30
    view_count: int | None = 10
    tags: tuple[str, ...] = ()
    language: str | None = "ko"


def _stat(vid: str) -> VideoStat:
    return VideoStat(vid, f"UC{vid}", "20", NOW - timedelta(days=2), 99, 1, 1, ())


class FakeYouTube:
    """videos(ids)만. 장부에는 진짜 커넥터처럼 부른 곳 shorts_backfill로 1단위씩 적는다."""

    def __init__(self, ledger: youtube_quota.Ledger, gone: set[str] = frozenset(),
                 fail: bool = False) -> None:  # fmt: skip
        self.ledger, self.gone, self.fail = ledger, set(gone), fail
        self.asked: list[list[str]] = []

    async def videos(self, ids):  # noqa: ANN001, ANN201
        self.ledger.add(sb.CALLER, 1)
        self.asked.append(list(ids))
        if self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "test")
        return [_stat(v) for v in ids if v not in self.gone]


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"DATA_SHORTS_DB": str(tmp_path / "shorts.db"),
            "DATA_YOUTUBE_DB": str(tmp_path / "yt.db"),
            "DATA_TREND_CONFIG": str(tmp_path / "none.toml"), **extra}  # fmt: skip


def _seed(tmp_path: Path, n: int, *, with_published: int = 0, day: date | None = None,
          extra: list[_Found] | None = None) -> list[str]:  # fmt: skip
    """searched_videos에 n개(게시 시각 없음), 앞 with_published개는 게시 시각을 넣는다."""
    store = ShortsStore(tmp_path / "shorts.db")
    found = [_Found(f"v{i:03d}") for i in range(n)] + (extra or [])
    store.save_search(day or NOW.date(), "20", "ok", None, found)
    if with_published:
        store.record_views(NOW, [_stat(f.video_id) for f in found[:with_published]])
    store.close()
    return [f.video_id for f in found]


def _published(tmp_path: Path) -> set[str]:
    conn = sqlite3.connect(tmp_path / "shorts.db")
    try:
        return {r[0] for r in conn.execute("SELECT video_id FROM published")}
    finally:
        conn.close()


def test_only_missing_ones_in_chunks_of_50(tmp_path: Path) -> None:
    ids = _seed(tmp_path, 120, with_published=10)
    ledger = youtube_quota.Ledger()
    yt = FakeYouTube(ledger)
    result = asyncio.run(sb.backfill(yt, _env(tmp_path), now=NOW, ledger=ledger))
    asked = [v for chunk in yt.asked for v in chunk]
    assert [len(c) for c in yt.asked] == [50, 50, 10]
    assert set(asked) == set(ids[10:])  # 이미 아는 10개는 부르지 않는다
    assert (result.filled, result.missing, result.left, result.calls) == (110, 0, 0, 3)
    assert _published(tmp_path) == set(ids)
    assert "[backfill] published 채움 110 · 없음 0 · 남음 0" == result.line()


def test_long_foreign_and_old_rows_are_not_targets(tmp_path: Path) -> None:
    old = NOW.date() - timedelta(days=40)
    foreign = _Found("en", title="hello", channel_title="ch", language="en")
    _seed(tmp_path, 0, extra=[_Found("long", duration_seconds=600), foreign])
    _seed(tmp_path, 0, day=old, extra=[_Found("old")])
    _seed(tmp_path, 0, extra=[_Found("ok")])
    assert sb.targets(_env(tmp_path), NOW, set()) == ["ok"]


def test_missing_ones_are_recorded_and_not_asked_again(tmp_path: Path) -> None:
    _seed(tmp_path, 3)
    ledger = youtube_quota.Ledger()
    yt = FakeYouTube(ledger, gone={"v001"})
    first = asyncio.run(sb.backfill(yt, _env(tmp_path), now=NOW, ledger=ledger))
    assert (first.filled, first.missing) == (2, 1)
    assert sb.missing_ids(tmp_path / "shorts.db") == {"v001"}
    again = FakeYouTube(ledger)
    second = asyncio.run(sb.backfill(again, _env(tmp_path), now=NOW, ledger=ledger))
    assert again.asked == [] and second.calls == 0  # 지워진 영상은 다시 부르지 않는다


def test_daily_cap_stops_and_leaves_the_rest_for_tomorrow(tmp_path: Path) -> None:
    _seed(tmp_path, 200)
    env = _env(tmp_path, DATA_SHORTS_BACKFILL_DAILY="3")
    ledger = youtube_quota.Ledger()
    ledger.add(sb.CALLER, 1)  # 오늘 이미 1번 불렀다
    yt = FakeYouTube(ledger)
    result = asyncio.run(sb.backfill(yt, env, now=NOW, ledger=ledger))
    assert len(yt.asked) == 2 and result.left == 100
    assert "하루 상한 3번" in (result.stopped or "")
    assert sum(u for c, u, _ in ledger.rows() if c == sb.CALLER) == 3


def test_fresh_shorts_reserve_is_kept(tmp_path: Path) -> None:
    _seed(tmp_path, 60)
    env = _env(tmp_path, DATA_YOUTUBE_DAILY_UNITS="1000")  # 예약 = 150, 다른 기능 몫 = 850
    ledger = youtube_quota.Ledger()
    ledger.add("youtube_collect", 850)
    yt = FakeYouTube(ledger)
    result = asyncio.run(sb.backfill(yt, env, now=NOW, ledger=ledger))
    assert yt.asked == [] and result.left == 60 and "예약 몫" in (result.stopped or "")


def test_failure_stops_without_marking_missing(tmp_path: Path) -> None:
    _seed(tmp_path, 3)
    ledger = youtube_quota.Ledger()
    result = asyncio.run(sb.backfill(FakeYouTube(ledger, fail=True), _env(tmp_path), now=NOW,
                                     ledger=ledger))  # fmt: skip
    assert result.filled == 0 and result.left == 3 and "UNAVAILABLE" in (result.stopped or "")
    assert sb.missing_ids(tmp_path / "shorts.db") == set()


def test_nothing_to_do_creates_no_file(tmp_path: Path) -> None:
    result = asyncio.run(sb.backfill(None, _env(tmp_path), now=NOW,
                                     ledger=youtube_quota.Ledger()))  # fmt: skip
    assert result.calls == 0 and not (tmp_path / "shorts.db").exists()


def test_real_connector_writes_the_shared_ledger_as_shorts_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """진짜 커넥터 + 가짜 HTTP: 장부(LEDGER)에 부른 곳 shorts_backfill로 1단위가 적힌다."""
    ids = _seed(tmp_path, 2)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked = request.url.params["id"].split(",")
        seen.extend(asked)
        items = [{"id": v, "snippet": {"publishedAt": "2026-11-16T00:00:00Z",
                                       "channelId": f"UC{v}", "title": "t"},
                  "statistics": {"viewCount": "5"}} for v in asked]  # fmt: skip
        return httpx.Response(200, json={"items": items})

    env = {**_env(tmp_path), "YOUTUBE_API_KEY": "test-key"}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yt = YouTubeConnector(env, client=client, caller=sb.CALLER)
    result = asyncio.run(sb.backfill(yt, env, now=NOW))
    assert result.filled == 2 and sorted(seen) == ids
    assert (sb.CALLER, 1, 0) in youtube_quota.LEDGER.rows()
    assert _published(tmp_path) == set(ids)

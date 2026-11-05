"""YouTube 할당량 장부 하나: 모든 호출이 같은 장부에 단위를 적고, 하루 상한을 넘으면 검색을 멈춘다.

실제 YouTube를 부르지 않는다 (httpx MockTransport).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import YouTubeConnector
from data_agent.skills.data_sources import list_sources
from data_agent.stage_timing import recording
from data_agent.youtube_quota import LEDGER, Ledger

ENV = {"YOUTUBE_API_KEY": "test-key", "DATA_YOUTUBE_DAILY_UNITS": "250"}


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/search"):
        return httpx.Response(200, json={"items": []})
    return httpx.Response(200, json={"items": []})


def _connector(env: dict[str, str] | None = None, caller: str | None = None) -> YouTubeConnector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return YouTubeConnector(env or ENV, client=client, caller=caller)


def test_every_call_is_written_by_caller_and_searches_stop_at_the_cap() -> None:
    after = datetime.now(UTC) - timedelta(days=1)

    async def go() -> None:
        yt = _connector(caller="youtube_collect")
        await yt.videos(["a"])  # 1단위, 수집기 이름
        with recording("video_explorer@precompute1", lambda _: None):
            await yt.search_recent(after, query="롤")  # 100단위, 단계 기록 이름이 먼저
        await yt.search_query_shorts("책", after)  # 100단위 → 201
        with pytest.raises(ConnectorUnavailable) as stopped:
            await yt.search_recent(after, query="또")  # 301 > 250: 부르지 않음
        assert stopped.value.code == "QUOTA_EXCEEDED" and "하루 상한" in str(stopped.value)
        await yt.videos(["b"])  # 1단위 호출은 계속

    asyncio.run(go())
    rows = {c: (u, s) for c, u, s in LEDGER.rows()}
    assert rows == {"youtube_collect": (102, 1), "video_explorer": (100, 1)}
    usage = LEDGER.usage(ENV)
    got = (usage.units, usage.unit_cap, usage.searches, usage.search_stopped)
    assert got == (202, 250, 2, True)
    assert usage.by_caller[0].caller == "youtube_collect" and "상한" in (usage.note or "")


def test_the_ledger_is_shared_through_sqlite(tmp_path: Path) -> None:
    a, b = Ledger(tmp_path / "q.db"), Ledger(tmp_path / "q.db")  # 서버·수집기처럼 따로 연 연결
    a.add("youtube_collect", 450)
    b.add("shorts_stats", 101, search=True)
    assert a.units() == b.units() == 551
    assert a.usage({}).unit_cap == 8000 and not a.usage({}).search_stopped  # 기본 상한
    assert a.can_search({"DATA_YOUTUBE_DAILY_UNITS": "600"}) is False


def test_data_sources_shows_today_youtube_usage() -> None:
    LEDGER.add("video_explorer", 300, search=True)
    out = asyncio.run(list_sources({}, prober=None)).output
    assert out.youtube_usage is not None
    assert (out.youtube_usage.units, out.youtube_usage.unit_cap) == (300, 8000)

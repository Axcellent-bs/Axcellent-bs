"""YouTube 할당량 장부 하나: 모든 호출이 같은 장부에 단위를 적고, 하루 상한을 넘으면 검색을 멈춘다.

실제 YouTube를 부르지 않는다 (httpx MockTransport).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import YouTubeConnector
from data_agent.skills.data_sources import list_sources
from data_agent.stage_timing import recording
from data_agent.youtube_quota import LEDGER, Ledger, reset_label

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


def test_fresh_shorts_keeps_its_reserved_share() -> None:
    """다른 기능이 몫(상한 − 예약)을 다 써도 새 숏츠 추적은 예약 몫까지 검색한다."""
    env = {"YOUTUBE_API_KEY": "test-key", "DATA_YOUTUBE_DAILY_UNITS": "1000",
           "DATA_FRESH_SHORTS_DAILY_UNITS": "300"}  # fmt: skip
    after = datetime.now(UTC) - timedelta(days=1)

    async def go() -> tuple[int, int]:
        other, fresh = _connector(env, caller="trend_popular"), _connector(env, "fresh_shorts")
        n = m = 0
        for _ in range(10):
            try:
                await other.search_recent(after, query="롤")
                n += 1
            except ConnectorUnavailable as error:
                assert "새 숏츠 추적 몫 300 예약" in str(error)
        for _ in range(5):
            try:
                await fresh.search_recent(after, query="책")
                m += 1
            except ConnectorUnavailable as error:
                assert "새 숏츠 추적 몫에 닿아" in str(error)
        return n, m

    assert asyncio.run(go()) == (7, 3)  # 700 = 1,000 − 300, 그 뒤 예약 300
    line = LEDGER.status_line(env)
    assert line == ("[quota] 오늘 1,000/1,000단위 · 다른 기능 700/700 (trend_popular 700(검색 7)) ·"
                    f" 새 숏츠 추적 300/300 예약 · {reset_label()}")  # fmt: skip


def test_default_reserve_is_1200_of_8000() -> None:
    from data_agent.youtube_quota import reserve

    assert reserve({}) == 1200
    assert reserve({"DATA_YOUTUBE_DAILY_UNITS": "250"}) == 37  # 상한을 낮추면 15%
    assert reserve({"DATA_FRESH_SHORTS_DAILY_UNITS": "900"}) == 900


def test_the_day_follows_pacific_midnight_and_old_kst_rows_move_once(tmp_path: Path) -> None:
    import sqlite3

    from data_agent import youtube_quota as yq

    before = datetime(2026, 11, 6, 7, 59, tzinfo=UTC)  # 한국 16:59, 태평양 11-05 23:59
    after = datetime(2026, 11, 6, 8, 1, tzinfo=UTC)
    days = (yq.day_of(before).isoformat(), yq.day_of(after).isoformat())
    assert days == ("2026-11-05", "2026-11-06")
    assert yq.reset_label(before) == "다음 초기화: 오늘 17:00 KST"
    assert yq.reset_label(datetime(2026, 7, 1, 3, 0, tzinfo=UTC)) == "다음 초기화: 오늘 16:00 KST"

    def old_table(path: Path) -> None:
        conn = sqlite3.connect(path)  # 예전 표 (한국 날짜)
        conn.execute("CREATE TABLE usage (day TEXT NOT NULL, caller TEXT NOT NULL, units INTEGER"
                     " NOT NULL, searches INTEGER NOT NULL, PRIMARY KEY (day, caller))")
        conn.execute("INSERT INTO usage VALUES ('2026-11-06', 'trend_popular', 3907, 39)")
        conn.execute("INSERT INTO usage VALUES ('2026-11-05', 'other', 5, 0)")
        conn.commit()
        conn.close()

    old_table(tmp_path / "a.db")  # 한국 16:59에 켬: 한국 11-06 줄은 모두 태평양 11-05 안
    ledger = Ledger(tmp_path / "a.db", now=before)
    assert ledger.rows(date(2026, 11, 5)) == [("trend_popular", 3907, 39)]
    ledger.add("youtube_collect", 1, day=date(2026, 11, 5))
    assert Ledger(tmp_path / "a.db", now=before).units(date(2026, 11, 5)) == 3908  # 한 번만
    old_table(tmp_path / "b.db")  # 한국 17:01에 켬: 새 장부 날짜는 0부터
    assert Ledger(tmp_path / "b.db", now=after).rows(date(2026, 11, 6)) == []


def test_one_unit_calls_also_stop_at_the_total_cap(tmp_path: Path) -> None:
    """확인과 기록을 한 번에 (SQLite): 합계가 상한을 넘는 호출은 1단위라도 막는다."""
    env = {"DATA_YOUTUBE_DAILY_UNITS": "1000", "DATA_FRESH_SHORTS_DAILY_UNITS": "300"}
    ledger = Ledger(tmp_path / "q.db")
    ledger.add("trend_popular", 950)
    assert ledger.take("fresh_shorts", 100, search=True, env=env) is not None  # 남은 50 < 100
    assert all(ledger.take("youtube_collect", 1, env=env) is None for _ in range(50))
    why = ledger.take("youtube_collect", 1, env=env)
    assert why is not None and "부르지 않음" in why and "다음 초기화" in why
    assert ledger.units() == 1000


def test_quota_line_code_version_comes_from_run_all_or_git() -> None:
    """[quota] 줄 끝 "· 코드 <짧은 커밋>": run_all.sh가 넘긴 값, 없으면 git (옛 코드가 보이게)."""
    from data_agent import youtube_collect

    assert youtube_collect.code_version({"AX_GIT_COMMIT": "abc1234"}) == "abc1234"
    assert youtube_collect.code_version({}) == youtube_collect._git_commit() != ""

"""새 숏츠 추적(fresh_shorts) 단위 약속. 실제 YouTube·LLM은 부르지 않는다 (가짜).

- 대상 형식: 브리프·캠페인에서 쓰는 형식 상위 5개 (기타·unknown 제외, 모자라면 저장분으로 채움)
- 수집기 루프가 1시간마다 부른다 (run_all.sh 일정 그대로)
- 상태 줄: "새 숏츠 추적: 오늘 N유닛 / 1,200, 추적 중 N개"
- 성과 탭: 기준 모으는 중 진행(형식 × 시점 표본 수), 신호 "기준 모으는 중 (n/5)"
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from data_agent import fresh_shorts, youtube_collect, youtube_quota
from data_agent.connectors.youtube import VideoStat

NOW = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)


def _env(tmp: Path) -> dict[str, str]:
    return {"DATA_YOUTUBE_DB": str(tmp / "youtube.db"), "DATA_SHORTS_DB": str(tmp / "shorts.db"),
            "DATA_FRESH_SHORTS_DB": str(tmp / "fresh.db"),
            "DATA_VIDEO_PERF_DB": str(tmp / "perf.db"),
            "DATA_INSIGHT_BRIEF_DB": str(tmp / "brief.db")}  # fmt: skip


def _brief(tmp: Path, formats: list[str]) -> None:
    conn = sqlite3.connect(tmp / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    rows = [{"insight_id": f"i{n}", "rank": n + 1, "topic": "t", "line": "x", "shorts_format": f}
            for n, f in enumerate(formats)]  # fmt: skip
    conn.execute("INSERT INTO briefs VALUES ('2026-11-06','r','x',?,NULL)",
                 (json.dumps({"insights": rows}),))  # fmt: skip
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def ledger(monkeypatch: pytest.MonkeyPatch) -> youtube_quota.Ledger:
    fresh = youtube_quota.Ledger(None)
    monkeypatch.setattr(youtube_quota, "LEDGER", fresh)
    return fresh


def test_target_formats_follow_the_brief_and_skip_other(tmp_path: Path) -> None:
    _brief(tmp_path, ["정보·팁", "정보·팁", "리액션", "기타", "챌린지"])
    got = fresh_shorts.target_formats(_env(tmp_path))
    assert got[:3] == ["정보·팁", "챌린지", "리액션"] and len(got) == 5  # 같은 횟수는 형식 목록 순
    assert "기타" not in got and "unknown" not in got


def test_collector_loop_runs_the_tracker_every_hour() -> None:
    """run_all.sh가 띄우는 YouTube 수집기 루프(1시간마다)가 새 숏츠 추적을 부른다."""
    assert "await track_fresh(env)" in inspect.getsource(youtube_collect.run)


class FakeYouTube:
    def __init__(self) -> None:
        self.searches: list[str] = []

    async def search_fresh_shorts(self, query, published_after, **_):  # noqa: ANN001, ANN003, ANN201
        self.searches.append(query)
        youtube_quota.LEDGER.add(fresh_shorts.CALLER, 100, search=True)
        return await self.videos(["newshort001", "longvideo01"])

    async def videos(self, ids):  # noqa: ANN001, ANN201
        youtube_quota.LEDGER.add(fresh_shorts.CALLER, 1)
        out = []
        for i in ids:
            seconds = 90 if i.startswith("long") else 30
            out.append(VideoStat(i, "ch", "24", NOW - timedelta(hours=1), 500, 20, 3, (),
                                 title=i, duration_seconds=seconds))  # fmt: skip
        return out


def test_track_fresh_finds_then_tracks_and_shows_status(tmp_path: Path) -> None:
    env = _env(tmp_path)
    fake = FakeYouTube()
    one = asyncio.run(fresh_shorts.run_tick(fake, env, NOW, formats=["정보·팁"],
                                            classify=lambda st: {s.video_id: "정보·팁"
                                                                 for s in st}))  # fmt: skip
    assert fake.searches == ["정보 팁"]  # 가운뎃점은 띄어쓰기로
    assert (one.searches, one.added, one.tracking, one.snapshots) == (1, 1, 1, 1)  # 90초는 뺌
    later = NOW + timedelta(hours=1)
    again = asyncio.run(fresh_shorts.run_tick(fake, env, later, formats=["정보·팁"],
                                              classify=lambda st: {}))  # fmt: skip
    assert again.searches == 0 and again.snapshots == 1  # 6시간 안에는 찾지 않고 추적만
    conn = sqlite3.connect(tmp_path / "youtube.db")
    assert conn.execute("SELECT DISTINCT sources FROM snapshots").fetchall() == [("fresh_track",)]
    conn.close()
    status = fresh_shorts.status(env, NOW + timedelta(hours=1))
    assert status.line == "새 숏츠 추적: 오늘 103유닛 / 1,200, 추적 중 1개"
    assert (status.units_today, status.tracking, status.find_stopped) == (103, 1, False)


def test_track_fresh_never_breaks_the_collector(tmp_path: Path) -> None:
    class Broken:
        async def search_fresh_shorts(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("x")

    got = asyncio.run(youtube_collect.track_fresh(_env(tmp_path), NOW, connector=Broken()))
    assert got == 0


def test_list_all_shows_baseline_progress_and_collecting_signals(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import VideoPerformanceRequest
    from data_agent.skills import video_performance as vp
    from data_agent.skills.video_factors import level_of

    _brief(tmp_path, ["리액션"])
    out = asyncio.run(vp.video_performance(VideoPerformanceRequest(list_all=True), None,
                                           env=_env(tmp_path), now=NOW)).output  # fmt: skip
    assert out.schema_version == "video_performance.v5"
    cells = {(p.shorts_format, p.checkpoint): p for p in out.baseline_progress}
    assert len(out.baseline_progress) == 20 and cells[("리액션", "1h")].n == 0
    assert all(p.need == 5 for p in out.baseline_progress) and out.baseline_ready is False
    assert level_of(10.0, None, 2, "1시간 조회") == ("unknown", "기준 모으는 중 (2/5)")


def test_a_full_format_is_not_searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """그 형식 추적 칸이 다 찼으면 검색 비용을 쓰지 않는다 (칸이 비면 다시 찾는다)."""
    monkeypatch.setattr(fresh_shorts, "PER_FORMAT", 1)
    env = _env(tmp_path)
    fake = FakeYouTube()
    classify = lambda st: {s.video_id: "정보·팁" for s in st}  # noqa: E731
    asyncio.run(fresh_shorts.run_tick(fake, env, NOW, formats=["정보·팁"], classify=classify))
    later = asyncio.run(fresh_shorts.run_tick(fake, env, NOW + timedelta(hours=6),
                                              formats=["정보·팁"], classify=classify))  # fmt: skip
    assert len(fake.searches) == 1 and later.full_skips == 1 and later.tracking == 1

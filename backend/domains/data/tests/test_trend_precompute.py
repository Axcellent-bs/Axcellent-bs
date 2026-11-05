"""트렌드 감지 탭 미리 계산: 수집기가 계산해 SQLite에 넣고 화면은 읽기만. 숫자는 그대로여야 한다.

- 바꾸기 전(그 자리에서 계산)과 후(미리 계산해 저장한 것을 읽음)의 응답이 같다 (세 skill)
- 미리 계산한 뒤 화면 요청은 계산하지 않는다. 새 스냅샷이 들어오면 다시 계산한다
- 저장분은 SQLite라 다시 켜도 남는다 (새 ResponseCache로 읽어도 hit)
- 저장분이 없으면(수집기 꺼짐) 첫 요청이 계산해 저장한다
- 수집기 중복 실행 잠금, 최근 스냅샷 조회 범위(48시간)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta
from data_agent import handlers, trend_precompute
from data_agent.connectors.youtube import VideoStat
from data_agent.response_cache import ResponseCache
from data_agent.today_board import TodayBoard
from data_agent.youtube_collect import acquire_lock
from data_agent.youtube_store import Row, SnapshotStore, recent_snapshots

NOW = datetime.now(UTC).replace(microsecond=0)
VIDEOS = [("v1", 1, ("t1", "lck"), 1000, 3000), ("v2", 2, ("t1",), 1000, 2000),
          ("v3", 3, ("lck", "롤"), 500, 600)]  # fmt: skip
SKILLS = ("trend_popular", "youtube_velocity", "shorts_stats")
# 계산한 시각이 들어가는 칸 (같은 데이터라도 부를 때마다 다르다)
CLOCK_FIELDS = {"collected_at", "generated_at", "measured_at", "computed_at"}


def _stat(vid: str, rank: int, tags: tuple[str, ...], views: int) -> VideoStat:
    return VideoStat(vid, "ch", "20", NOW - timedelta(days=1), views, 10, 1, tags, rank=rank)


def _save(db: Path, at: datetime, col: int, extra: int = 0) -> None:
    store = SnapshotStore(db)
    store.save(at, [Row(_stat(v[0], v[1], v[2], v[col] + extra), ("popular",)) for v in VIDEOS])
    store.close()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    config = tmp_path / "trends.toml"
    body = "[candidates]\nmin_videos = 2\n\n[popular]\ntop = 10\n\n[shorts]\nsearch_calls = 0\n"
    config.write_text(body, encoding="utf-8")
    db = tmp_path / "youtube.db"
    _save(db, NOW - timedelta(hours=2), 3)
    _save(db, NOW - timedelta(hours=1), 4)
    out = {
        "DATA_YOUTUBE_DB": str(db), "DATA_TREND_CONFIG": str(config),
        "DATA_TRENDING_DB": str(tmp_path / "trending.db"),
        "DATA_SHORTS_DB": str(tmp_path / "shorts.db"),
        "DATA_TOPIC_CATEGORY_DB": str(tmp_path / "categories.db"),
        "DATA_POPULAR_VIDEOS_DB": str(tmp_path / "popular_videos.db"),
        "DATA_TREND_RESPONSES_DB": str(tmp_path / "responses.db"),
    }  # fmt: skip
    for k, v in out.items():
        monkeypatch.setenv(k, v)
    for k in ("YOUTUBE_API_KEY", "DATA_TODAY_BOARD_DB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(handlers, "get_youtube", lambda: None)
    monkeypatch.setattr(handlers, "_category_cache", None)
    monkeypatch.setattr("data_agent.today_board.BOARD", TodayBoard())
    monkeypatch.setattr(handlers, "RESPONSES", ResponseCache(out["DATA_TREND_RESPONSES_DB"]))
    return out


def _request(skill: str) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_screen", token="x",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input={})


def _call(skill: str) -> dict:
    res = asyncio.run(getattr(handlers, skill)(_request(skill)))
    assert str(res.status).lower().endswith("completed"), (skill, res.outcome)
    return {"outcome": res.outcome, "output": res.output}


def _strip(value: object) -> object:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in CLOCK_FIELDS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _count_compute(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    for name in ("popular", "run_shorts_stats", "measure_velocity_named"):
        real = getattr(handlers, name)

        async def counted(*a, _real=real, _name=name, **k):  # noqa: ANN002, ANN003, ANN202
            calls.append(_name)
            return await _real(*a, **k)

        monkeypatch.setattr(handlers, name, counted)
    return calls


def test_precomputed_responses_equal_computed_ones(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 바꾸기 전: 저장 없이 그 자리에서 계산
    monkeypatch.setattr(handlers, "RESPONSES", ResponseCache())
    before = {s: _call(s) for s in SKILLS}
    # 바꾼 뒤: 수집기가 미리 계산 → 화면은 저장분을 읽기만
    stored = ResponseCache(env["DATA_TREND_RESPONSES_DB"])
    monkeypatch.setattr(handlers, "RESPONSES", stored)
    assert asyncio.run(trend_precompute.precompute(lambda _: None)) == []
    calls = _count_compute(monkeypatch)
    after = {s: _call(s) for s in SKILLS}
    assert calls == []  # 화면 요청은 계산하지 않았다
    for s in SKILLS:
        assert _strip(after[s]) == _strip(before[s]), s


def test_new_snapshot_recomputes_and_store_survives_restart(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_compute(monkeypatch)
    first = _call("trend_popular")  # 저장분 없음(수집기 꺼짐) → 계산해 저장
    assert calls == ["popular"]
    # 다시 켠 것처럼 새 연결로 읽어도 저장분이 있다
    monkeypatch.setattr(handlers, "RESPONSES", ResponseCache(env["DATA_TREND_RESPONSES_DB"]))
    assert _call("trend_popular") == first and calls == ["popular"]
    _save(Path(env["DATA_YOUTUBE_DB"]), NOW, 4, extra=900)  # 새 스냅샷
    fresh = _call("trend_popular")
    assert calls == ["popular", "popular"] and fresh != first


def test_collector_lock_blocks_a_second_collector(tmp_path: Path) -> None:
    lock = tmp_path / "youtube_collect.lock"
    first = acquire_lock(lock)
    assert first is not None
    try:
        assert acquire_lock(lock) is None
    finally:
        first.close()
    again = acquire_lock(lock)
    assert again is not None
    again.close()


def test_recent_snapshots_look_back_48_hours_from_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "youtube.db"
    for hours in (100, 2, 1, 0):
        _save(db, NOW - timedelta(hours=hours), 3)
    monkeypatch.delenv("DATA_TREND_LOOKBACK_HOURS", raising=False)
    assert len(recent_snapshots(db, 3)) == 3  # 매시간이면 그대로
    assert len(recent_snapshots(db, 5)) == 3  # 100시간 전 것은 보지 않는다
    monkeypatch.setenv("DATA_TREND_LOOKBACK_HOURS", "200")
    assert len(recent_snapshots(db, 5)) == 4


def test_explain_and_naver_caches_survive_restart(tmp_path: Path) -> None:
    from datetime import date

    from data_agent.connectors.naver_datalab import DailyCache, TrendResponse, TrendSeries
    from data_agent.explain import ExplainCache, Explanation

    path = tmp_path / "explain.db"
    ExplainCache(path=path).put("p", Explanation("급상승 1개입니다.", False))
    assert ExplainCache(path=path).get("p") == Explanation("급상승 1개입니다.", False)
    assert ExplainCache(path=path).get("q") is None

    day = date(2026, 11, 5)
    value = TrendResponse(day, day, "date", (TrendSeries("고양이", ("고양이",), ((day, 1.5),)),))
    naver = tmp_path / "naver.db"
    first = DailyCache(today=lambda: day, path=naver)
    first.put("k", value)
    first.count_call(10)
    again = DailyCache(today=lambda: day, path=naver)  # 다시 켬
    assert again.get("k") == value and again.calls == 1
    tomorrow = DailyCache(today=lambda: date(2026, 11, 6), path=naver)
    assert tomorrow.get("k") is None and tomorrow.calls == 0

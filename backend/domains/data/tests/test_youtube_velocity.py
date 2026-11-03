"""youtube_velocity skill: YouTube 영상 지금 속도(판정 없음). 실제 YouTube를 부르지 않는다(임시 DB).

- 지금 속도 = 최근 두 스냅샷 조회수 차이 ÷ 시간, 가속 = 그 전 구간과 비교
- 조회수 빠짐 → 속도 비우고 순위에서 뺌, 좋아요 숨김 → 그 칸만 비움, 지워진 영상 → 뺌
- 스냅샷 2개 미만 → unknown + 이유
- 상위 N개(설정), 판정 단어 없음, 핸들러
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import YouTubeVelocityRequest
from data_agent import handlers
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.youtube_velocity import measure_velocity, top_setting
from data_agent.youtube_store import Row, SnapshotStore, recent_snapshots

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)


def _stat(vid: str, views: int | None, *, likes: int | None = 5, rank: int | None = 1) -> VideoStat:
    return VideoStat(
        vid, "ch", "20", T0 - timedelta(days=1), views, likes, 1, ("태그", vid), rank=rank
    )


def _db(tmp_path: Path, runs: list[tuple[datetime, list[VideoStat]]]) -> Path:
    path = tmp_path / "yt.db"
    store = SnapshotStore(path)
    for at, stats in runs:
        store.save(at, [Row(s, ("popular",)) for s in stats])
    store.close()
    return path


def _run(path: Path, **kw):  # noqa: ANN003, ANN202
    env = {"DATA_YOUTUBE_DB": str(path), "DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}
    return measure_velocity(YouTubeVelocityRequest(**kw), env=env, now=T0 + timedelta(hours=3))


RUNS = [
    (T0, [_stat("a", 1000), _stat("b", 5000), _stat("gone", 10)]),
    (
        T0 + timedelta(hours=1),
        [_stat("a", 1100), _stat("b", 5600), _stat("gone", 20), _stat("hid", 50)],
    ),
    (
        T0 + timedelta(hours=2),
        [_stat("a", 1400), _stat("b", 5900, likes=None), _stat("hid", None), _stat("new", 9)],
    ),
]


def test_speed_is_the_view_gain_per_hour_between_the_last_two_snapshots(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, RUNS)).output
    assert out.status == "known"
    assert (out.latest_at, out.previous_at, out.earlier_at) == (
        T0 + timedelta(hours=2),
        T0 + timedelta(hours=1),
        T0,
    )
    by = {v.video_id: v for v in out.videos}
    assert [v.video_id for v in out.videos] == [
        "a",
        "b",
    ]  # 지금 속도 큰 순, 같으면 영상 id 순 (둘 다 300)
    assert by["a"].views_per_hour == 300.0 and by["a"].prev_views_per_hour == 100.0
    assert by["a"].speed_change == 200.0 and by["a"].faster is True
    assert by["b"].views_per_hour == 300.0 and by["b"].prev_views_per_hour == 600.0
    assert by["b"].faster is False


def test_missing_views_are_left_out_and_hidden_likes_stay_empty(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, RUNS)).output
    by = {v.video_id: v for v in out.videos}
    assert "hid" not in by  # 조회수가 빠져 속도를 못 잼 → 순위에 넣지 않음 (0으로 채우지 않음)
    assert out.missing_views == 1
    assert by["b"].likes is None and by["b"].views == 5900  # 좋아요 숨김은 그 칸만 비움
    assert "gone" not in by  # 최근 스냅샷에 없음 (지워짐 등)
    assert "new" not in by  # 앞 스냅샷이 없어 비교할 수 없음
    assert out.compared_videos == 3  # a·b·hid


def test_no_earlier_snapshot_means_no_acceleration(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, RUNS[1:])).output
    a = next(v for v in out.videos if v.video_id == "a")
    assert a.views_per_hour == 300.0 and a.prev_views_per_hour is None
    assert a.speed_change is None and a.faster is None
    assert out.earlier_at is None


def test_fewer_than_two_snapshots_is_unknown(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, RUNS[:1])).output
    assert out.status == "unknown" and out.videos == []
    assert out.unknown_reason == (
        "YouTube 스냅샷이 2개 미만이라 지금 속도를 잴 수 없음"
        " (지금 1개, 두 시각의 조회수 차이로 잰다)"
    )
    missing = _run(tmp_path / "none.db").output
    assert missing.status == "unknown" and "지금 0개" in (missing.unknown_reason or "")
    assert not (tmp_path / "none.db").exists()  # 읽기 전용: 없는 파일을 만들지 않는다


def test_top_n_and_no_judgement_words(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, RUNS), top=1).output
    assert len(out.videos) == 1
    dumped = out.model_dump_json()
    assert "급상승" not in dumped and "rising" not in dumped
    assert top_setting({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}) == 10
    assert top_setting({}) == 10


def test_recent_snapshots_reads_newest_first(tmp_path: Path) -> None:
    snaps = recent_snapshots(_db(tmp_path, RUNS), 2)
    assert [s.collected_at for s in snaps] == [T0 + timedelta(hours=2), T0 + timedelta(hours=1)]
    assert snaps[0].videos["hid"].view_count is None


def _request(payload: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_vel", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentRequest(meta=meta, skill="youtube_velocity", input=payload)


def test_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_YOUTUBE_DB", str(_db(tmp_path, RUNS)))
    result = asyncio.run(handlers.HANDLERS["youtube_velocity"](_request({"top": 5})))
    assert result.status == TaskStatus.COMPLETED and result.outcome == "known"
    assert [v["video_id"] for v in result.output["videos"]] == ["a", "b"]
    bad = asyncio.run(handlers.HANDLERS["youtube_velocity"](_request({"top": 0})))
    assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"

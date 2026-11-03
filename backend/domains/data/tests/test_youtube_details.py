"""YouTube 영상 채널 이름·길이: 응답 파싱, 저장, youtube_velocity 표시.

실제 YouTube를 부르지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import YouTubeVelocityRequest
from data_agent.connectors.youtube import VideoStat, duration_seconds
from data_agent.skills.youtube_velocity import measure_velocity
from data_agent.youtube_store import Row, SnapshotStore, video_details

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)


def test_iso_duration_is_read_in_seconds_or_none() -> None:
    assert duration_seconds("PT45S") == 45
    assert duration_seconds("PT1M5S") == 65
    assert duration_seconds("PT1H2M3S") == 3723
    assert duration_seconds("P1DT1S") == 86401
    assert duration_seconds("P0D") == 0
    assert duration_seconds(None) is None and duration_seconds("이상한 값") is None


def _stat(vid: str, views: int, *, channel: str = "", seconds: int | None = None) -> VideoStat:
    return VideoStat(vid, "ch", "20", NOW - timedelta(days=1), views, 1, 1, ("t",),
                     title=f"{vid} 제목", rank=1, channel_title=channel,
                     duration_seconds=seconds)  # fmt: skip


def test_velocity_shows_title_channel_and_length(tmp_path: Path) -> None:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    store.save(NOW - timedelta(hours=2), [Row(_stat("v1", 100), ("popular",))])
    store.save(
        NOW - timedelta(hours=1),
        [Row(_stat("v1", 400, channel="캣채널", seconds=42), ("popular",))],
    )
    store.close()
    assert video_details(db, ["v1"])["v1"].duration_seconds == 42
    out = measure_velocity(YouTubeVelocityRequest(), env={"DATA_YOUTUBE_DB": str(db)}, now=NOW)
    [video] = out.output.videos
    assert (video.title, video.channel_title, video.duration_seconds) == ("v1 제목", "캣채널", 42)
    assert video.views_per_hour == 300.0


def test_missing_details_stay_none(tmp_path: Path) -> None:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    store.save(NOW - timedelta(hours=2), [Row(_stat("v1", 100), ("popular",))])
    store.save(NOW - timedelta(hours=1), [Row(_stat("v1", 200), ("popular",))])
    assert store.popular_ids_without_details() == ["v1"]
    store.close()
    out = measure_velocity(YouTubeVelocityRequest(), env={"DATA_YOUTUBE_DB": str(db)}, now=NOW)
    assert out.output.videos[0].channel_title is None  # 지어내지 않는다
    assert out.output.videos[0].duration_seconds is None

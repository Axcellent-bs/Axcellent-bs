"""썸네일·영상 주소: 이미 받은 YouTube 응답 값만 저장·사용한다 (추가 호출 없음)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from data_agent.connectors.youtube import VideoStat, _parse, _stat, _VideoPage, thumbnail_url
from data_agent.skills.shorts_stats import ShortsStore
from data_agent.video_links import links_from, video_url
from data_agent.youtube_store import SnapshotStore, video_thumbnails

AT = datetime(2026, 11, 4, 3, tzinfo=UTC)


def _item(vid: str, thumbs: dict | None) -> dict:
    snippet = {"publishedAt": "2026-11-03T00:00:00Z", "channelId": "c", "title": "t"}
    if thumbs is not None:
        snippet["thumbnails"] = thumbs
    return {"id": vid, "snippet": snippet}


def test_connector_reads_thumbnail_from_the_same_response() -> None:
    page = _parse(_VideoPage, {"items": [
        _item("a", {"default": {"url": "d.jpg"}, "medium": {"url": "m.jpg"}}),
        _item("b", {"high": {"url": "h.jpg"}}),
        _item("c", None),
    ]}, "videos")  # fmt: skip
    got = {v.id: _stat(v).thumbnail_url for v in page.items}
    assert got == {"a": "m.jpg", "b": "h.jpg", "c": None}  # medium 먼저, 없으면 None
    assert thumbnail_url({}) is None


def _vs(vid: str, thumb: str | None) -> VideoStat:
    return VideoStat(vid, "c", None, AT, 10, None, None, (), "t", thumbnail_url=thumb,
                     channel_title="ch")  # fmt: skip


def test_store_keeps_thumbnails_and_links_use_only_stored_values(tmp_path: Path) -> None:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    try:
        store.save_details(AT, [_vs("a", "a.jpg"), _vs("b", None)])
    finally:
        store.close()
    assert video_thumbnails(db, ["a", "b", "z"]) == {"a": "a.jpg"}
    got = {v.video_id: v for v in links_from(db, ["a", "b"])}
    assert got["a"].thumbnail_url == "a.jpg" and got["a"].video_url == video_url("a")
    assert got["b"].thumbnail_url is None  # 저장된 값이 없으면 지어내지 않음
    assert links_from(tmp_path / "none.db", ["a"])[0].thumbnail_url is None
    assert video_url("x", short=True) == "https://www.youtube.com/shorts/x"


def test_shorts_store_keeps_search_thumbnails() -> None:
    store = ShortsStore(":memory:")
    try:
        store.record_views(AT, [_vs("s1", "s1.jpg"), _vs("s2", None)])
        assert store.thumbnails(["s1", "s2"]) == {"s1": "s1.jpg"}
    finally:
        store.close()

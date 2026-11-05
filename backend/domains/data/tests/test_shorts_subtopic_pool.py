"""shorts_stats 표본에 하위 주제 검색(#587) 숏츠를 합친다 (같은 영상 한 번, 추가 검색 없음).

실제 YouTube·LLM을 부르지 않는다 (저장된 스냅샷 + 가짜 다시 조회·LLM).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.domain_subtopics import Short, SubtopicStore, pool_shorts
from data_agent.skills.shorts_stats import (
    ShortsStatsRequest,
    _FormatItem,
    _Formats,
    shorts_stats,
)
from data_agent.skills.topic_category import _Categories, _Item
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)


def _stat(vid: str) -> VideoStat:
    return VideoStat(vid, "ch", "20", NOW - timedelta(days=1), 100, 1, 1, ("tag",),
                     title=f"{vid} 숏츠", rank=1, channel_title="채널",
                     duration_seconds=30)  # fmt: skip


class FakeLLM(MockLLMClient):
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        data = dict(untrusted or {})
        if schema is _Categories:
            n = [k[2:] for k in data if k.startswith("소재")]
            return _Categories(items=[_Item(id=i, category="게임") for i in n])
        n = [k[2:] for k in data if k.startswith("영상")]
        return _Formats(items=[_FormatItem(id=i, format="챌린지") for i in n])


class RecheckYouTube:
    """다시 조회(videos.list)만 받는다. 검색을 부르면 센다."""

    def __init__(self) -> None:
        self.quota_used = 0
        self.rechecked: list[str] = []
        self.searched = 0

    async def search_shorts(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.searched += 1
        return []

    async def videos(self, ids):  # noqa: ANN001, ANN201
        self.rechecked += list(ids)
        return []


def _env(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    store.save(NOW - timedelta(hours=1), [Row(_stat(f"p{i}"), ("popular",)) for i in range(3)])
    store.close()
    config = tmp_path / "trends.toml"
    config.write_text('[shorts]\nmin_sample = 5\nsearch_calls = 0\nlanguage = "ko"\n',
                      encoding="utf-8")  # fmt: skip
    return {"DATA_YOUTUBE_DB": str(db), "DATA_TREND_CONFIG": str(config),
            "DATA_SHORTS_DB": str(tmp_path / "shorts.db")}  # fmt: skip


def _pool(tmp_path: Path, shorts: list[Short], *, days_ago: int = 1) -> None:
    store = SubtopicStore(tmp_path / "subtopics.db")
    store.put("파이썬 입문", NOW - timedelta(days=days_ago), shorts)
    store.close()


def test_subtopic_shorts_join_the_sample_once(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _pool(tmp_path, [
        Short("p0", "p0 숏츠", (), 100),  # 인기 스냅샷에 이미 있음: 한 번만
        Short("t1", "파이썬 기초 1분", ("파이썬",), 5000, "https://i.ytimg.com/t1.jpg"),
        Short("t2", "코딩 공부 팁", (), 7000),
        Short("t3", "english only", (), 9000),  # 한국어 아님: 뺌
    ])  # fmt: skip
    yt = RecheckYouTube()
    out = asyncio.run(shorts_stats(ShortsStatsRequest(), yt, FakeLLM(), env=env, now=NOW)).output
    assert out.subtopic_shorts == 2 and out.shorts == 5
    assert yt.searched == 0  # 추가 검색 없음
    assert {"t1", "t2"} <= set(yt.rechecked)  # 다시 조회(videos.list)로 채널·시간당 조회
    assert "하위 주제 2" in out.summary
    [game] = out.by_category
    assert game.count == 5 and game.status == "known"
    t1 = next(v for v in game.videos if v.video_id == "t1")
    assert t1.thumbnail_url == "https://i.ytimg.com/t1.jpg"


def test_old_subtopic_searches_and_missing_file_are_skipped(tmp_path: Path) -> None:
    assert pool_shorts({}, NOW, tmp_path / "shorts.db") == []  # 파일이 없으면 만들지 않음
    assert not (tmp_path / "subtopics.db").exists()
    _pool(tmp_path, [Short("t1", "파이썬 기초", (), 5000)], days_ago=8)  # 보관 7일 지남
    assert pool_shorts({}, NOW, tmp_path / "shorts.db") == []
    assert pool_shorts({}, NOW, ":memory:") == []

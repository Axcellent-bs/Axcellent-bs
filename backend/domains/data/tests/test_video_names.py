"""영상 분류에서 실존 인물·상표 이름도 판정하고(#0-c), 근거 숏츠 광고 재료 칸을 그 결과로 정한다.

실제 LLM·YouTube를 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import VideoLink
from data_agent import caution
from data_agent.evidence_videos import VideoInfo, label, stored_info
from data_agent.skills.domain_subtopics import Short, SubtopicStore
from data_agent.skills.topic_category import (
    VIDEO_KEY,
    CategoryCache,
    VideoInput,
    _Categories,
    _Item,
    classify_videos,
)

NOW = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


class VideoLLM(MockLLMClient):
    """제목에 '페이커'가 있으면 실존 인물, '롤'이 있으면 게임 상표로 답한다."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[list[str]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        assert schema is _Categories
        data = dict(untrusted or {})
        titles = {k[2:].split(" ")[0]: v for k, v in data.items() if k.startswith("영상")}
        self.asked.append(list(titles.values()))
        items = []
        for n, title in titles.items():
            if "페이커" in title:
                names = ["페이커", "지어낸 이름"]
                items.append(_Item(id=n, category="게임", real_entity=True, entity_names=names,
                                   is_person=True))  # fmt: skip
            elif "롤" in title:
                items.append(_Item(id=n, category="게임", real_entity=True, entity_names=["롤"]))
            else:
                items.append(_Item(id=n, category="생활·취미", real_entity=False))
        return _Categories(items=items)


def test_video_classify_judges_names_only_from_the_title_and_tags() -> None:
    llm = VideoLLM()
    got = asyncio.run(classify_videos([
        VideoInput("a", "페이커 하이라이트"), VideoInput("b", "롤 신챔 공략"),
        VideoInput("c", "로마 역사 10분 정리"),
    ], llm))  # fmt: skip
    assert (got["a"].is_person, got["a"].entity_names) == (True, ("페이커",))  # 지어낸 이름은 뺌
    assert (got["b"].real_entity, got["b"].is_person) == (True, False)
    assert (got["c"].category, got["c"].real_entity) == ("생활·취미", False)


def test_videos_stored_before_names_are_asked_again_once(tmp_path: Path) -> None:
    db = tmp_path / "topics.db"
    cache = CategoryCache(db, now=lambda: NOW)
    cache.put(VIDEO_KEY + "a", cache.get("x") or _old())  # 예전 영상 분류: 이름 칸 없음
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT real_entity FROM topic_categories").fetchone() == (None,)
    conn.close()
    llm = VideoLLM()
    first = asyncio.run(classify_videos([VideoInput("a", "페이커 하이라이트")], llm, cache=cache))
    assert first["a"].is_person and len(llm.asked) == 1  # 다시 물어 채움
    again = asyncio.run(classify_videos([VideoInput("a", "페이커 하이라이트")], llm, cache=cache))
    assert again["a"].source == "stored" and len(llm.asked) == 1  # 채운 뒤엔 재사용


def _old():  # noqa: ANN202
    from data_agent.skills.topic_category import CategoryResult

    return CategoryResult("게임", None, 0, is_person=False)  # real_entity 없음 (예전 영상 분류)


def test_evidence_videos_follow_the_video_judgment_and_get_titles() -> None:
    topic = caution.judge("게임", real_entity=False, is_person=False, basis="주제 '게임' 분류")
    links = [caution.apply(VideoLink(video_id=v), topic) for v in ("a", "b", "z")]
    info = {"a": VideoInfo("페이커 하이라이트", "채널", 1200), "b": VideoInfo("롤 신챔 공략")}
    [filled] = asyncio.run(label([links], info, VideoLLM()))
    a, b, z = filled
    assert (a.ad_usable, a.caution_types, a.title, a.views) == (
        False, ["real_person"], "페이커 하이라이트", 1200)  # fmt: skip
    assert "영상 분류" in (a.caution_reason or "") and "페이커" in (a.caution_reason or "")
    assert (b.ad_usable, b.caution_types, b.views) == (False, ["trademark"], None)
    assert (z.ad_usable, z.title) == (True, None)  # 제목이 없으면 주제 분류 결과 그대로


def test_stored_info_reads_titles_views_and_subtopic_shorts(tmp_path: Path) -> None:
    from data_agent.connectors.youtube import VideoStat
    from data_agent.youtube_store import Row, SnapshotStore

    yt = tmp_path / "yt.db"
    store = SnapshotStore(yt)
    stat = VideoStat("a", "ch", "20", NOW - timedelta(days=1), 500, 1, 1, (), title="인기 영상",
                     channel_title="채널A", duration_seconds=30)  # fmt: skip
    store.save(NOW - timedelta(hours=2), [Row(stat, ("popular",))])
    store.save(NOW - timedelta(hours=1), [Row(VideoStat(**{**stat.__dict__, "view_count": 900}),
                                              ("popular",))])  # fmt: skip
    store.close()
    sub = SubtopicStore(tmp_path / "subtopics.db")
    sub.put("씨앗", NOW - timedelta(days=1), [Short("t", "하위 주제 숏츠", (), 70)])
    sub.close()
    env = {"DATA_YOUTUBE_DB": str(yt), "DATA_SHORTS_DB": str(tmp_path / "shorts.db")}
    got = stored_info(env, NOW, ["a", "t", "none"])
    assert got["a"] == VideoInfo("인기 영상", "채널A", 900)
    assert got["t"] == VideoInfo("하위 주제 숏츠", None, 70)
    assert got["none"] == VideoInfo()
    assert json.dumps(stored_info(env, NOW, [])) == "{}"


class NullLLM(MockLLMClient):
    """참/거짓·이름 칸을 null로 답한다."""

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        return schema.model_validate({"items": [
            {"id": "1", "category": "교육·책", "real_entity": None, "entity_names": None,
             "is_person": None}]})  # fmt: skip


class FailLLM(MockLLMClient):
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        raise TimeoutError


def test_null_flags_do_not_drop_the_batch_and_failures_say_why() -> None:
    got = asyncio.run(classify_videos([VideoInput("a", "책 추천 3권")], NullLLM()))
    a = got["a"]
    assert (a.category, a.real_entity, a.entity_names) == ("교육·책", False, ())
    topic = caution.judge("교육·책", real_entity=None, is_person=None, basis="하위 주제 '책' 분류")
    [[v]] = asyncio.run(label([[caution.apply(VideoLink(video_id="a"), topic)]],
                              {"a": VideoInfo("책 추천 3권")}, FailLLM()))  # fmt: skip
    assert v.ad_usable is None and v.title == "책 추천 3권"
    assert (v.caution_reason or "").startswith("영상 분류 못 함 (LLM 호출 실패 (TimeoutError))")

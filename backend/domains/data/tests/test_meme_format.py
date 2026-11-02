"""밈 형식 분류: 대표 영상 제목 → 정해진 목록 안의 형식. 목록 밖은 기타, 근거가 없으면 unknown.

실제 LLM·네이버를 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import LLMNotConfiguredError, MockLLMClient
from ax_contracts.schemas.data import ContentRecommendRequest, TrendScanInput
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.youtube import VideoStat
from data_agent.explain import _Idea, explain_idea
from data_agent.skills.content_recommend import recommend
from data_agent.skills.meme_format import MEME_FORMATS, _Meme, classify
from data_agent.skills.trend_scan import REQUEST_DAYS
from data_agent.youtube_store import Row, SnapshotStore, video_titles

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
SNAP_AT = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
END = date(2026, 11, 1)
START = END - timedelta(days=REQUEST_DAYS - 1)
TITLES = [
    "T1 결승 하이라이트",
    "페이커 슈퍼플레이 모음",
    "LCK 명장면",
    "t1 vs 젠지 풀경기",
    "결승 리뷰",
    "6번째",
]


def _llm(answer: str) -> MockLLMClient:
    return MockLLMClient(responses={_Meme: _Meme(format=answer)})  # type: ignore[arg-type]


def test_answer_in_the_list_is_kept_and_titles_go_only_to_the_data_block() -> None:
    llm = _llm("하이라이트")
    got = asyncio.run(classify("t1", TITLES, llm))
    assert (got.format, got.reason, got.titles, got.hint) == ("하이라이트", None, 5, "숏폼")
    head, _, block = llm.calls[0].partition("<untrusted")
    assert "T1 결승 하이라이트" in block and "T1 결승 하이라이트" not in head
    assert "6번째" not in llm.calls[0]  # 최대 5개
    assert all(f in head for f in MEME_FORMATS)


def test_an_answer_outside_the_list_becomes_other() -> None:
    got = asyncio.run(classify("t1", TITLES, _llm("브이로그")))
    assert (got.format, got.reason) == ("기타", "목록 밖 답이라 기타로 둠")
    assert asyncio.run(classify("t1", TITLES, _llm("정보·리뷰"))).hint == "롱폼"


def test_no_titles_or_no_llm_is_unknown_with_a_reason_not_other() -> None:
    llm = _llm("챌린지")
    empty = asyncio.run(classify("t1", ["", "  "], llm))
    assert (empty.format, empty.reason, empty.titles) == (
        "unknown",
        "YouTube 근거 영상 제목이 없음",
        0,
    )
    assert llm.calls == []  # 근거가 없으면 부르지 않는다

    class NoLLM:
        async def structured(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            raise LLMNotConfiguredError("없음")

    got = asyncio.run(classify("t1", TITLES, NoLLM()))  # type: ignore[arg-type]
    assert (got.format, got.reason, got.hint) == ("unknown", "LLM 설정이 없어 분류하지 못함", None)
    failed = asyncio.run(classify("t1", TITLES, MockLLMClient()))  # 응답 없음 → 호출 실패
    assert failed.format == "unknown" and (failed.reason or "").startswith("LLM 호출 실패")


def test_the_idea_prompt_and_fallback_format_follow_the_meme_format() -> None:
    llm = MockLLMClient(
        responses={_Idea: _Idea(idea="평소보다 2배 늘었으니 리뷰 영상을.", format="롱폼")}
    )  # type: ignore[arg-type]
    idea = asyncio.run(
        explain_idea({"배수": 2.0}, {"키워드": "t1"}, llm, meme="정보·리뷰", format_hint="롱폼")
    )
    assert not idea.fallback and idea.format == "롱폼"
    assert "밈 형식(대표 영상 제목으로 분류): 정보·리뷰" in llm.calls[0].partition("<untrusted")[0]
    broken = asyncio.run(
        explain_idea(
            {"배수": 2.0}, {"키워드": "t1"}, MockLLMClient(), meme="정보·리뷰", format_hint="롱폼"
        )
    )
    assert broken.fallback and broken.format == "롱폼"  # 고정 문장도 권장 형식을 따른다


# ---------------------------------------------------------------- skill


def _stat(vid: str, rank: int, tags: tuple[str, ...], title: str) -> VideoStat:
    return VideoStat(
        video_id=vid, channel_id="c", category_id="20", published_at=NOW - timedelta(days=1),
        view_count=1, like_count=1, comment_count=1, tags=tags, title=title, rank=rank,
    )  # fmt: skip


class Naver:
    def __init__(self) -> None:
        self.calls_today = 0

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.calls_today += 1
        name = query.keyword_groups[0].group_name
        spike = 3.0 if name == "t1" else 1.0

        def value(i: int) -> float:
            boost = spike if i >= REQUEST_DAYS - 3 else 1.0
            return round(50 * (1 + 0.05 * math.sin(i * 2.1)) * boost, 4)

        points = tuple((START + timedelta(days=i), value(i)) for i in range(REQUEST_DAYS))
        return TrendResponse(
            query.start_date, query.end_date, query.time_unit, (TrendSeries(name, (name,), points),)
        )


def test_recommendations_carry_the_meme_format_from_representative_titles(tmp_path: Path) -> None:
    db = tmp_path / "youtube.db"
    store = SnapshotStore(db)
    store.save(
        SNAP_AT,
        [Row(_stat(f"v{i}", i + 1, ("t1",), t), ("popular",)) for i, t in enumerate(TITLES[:3])],
    )
    store.close()
    assert video_titles(db, ["v0", "v9"]) == {"v0": "T1 결승 하이라이트"}
    config = tmp_path / "trends.toml"
    config.write_text(
        '[[groups]]\nname = "그림책"\nkeywords = ["그림책"]\n[candidates]\nrep_videos = 5\n',
        encoding="utf-8",
    )
    llm = MockLLMClient(responses={
        _Meme: _Meme(format="하이라이트"),
        _Idea: _Idea(idea="경기 장면을 모아 보세요.", format="숏폼"),
    })  # type: ignore[arg-type]  # fmt: skip
    env = {"DATA_YOUTUBE_DB": str(db), "DATA_TREND_CONFIG": str(config)}
    out = asyncio.run(
        recommend(ContentRecommendRequest(end_date=END), Naver(), llm, env=env, now=NOW)
    ).output
    [item] = out.items
    assert (item.keyword, item.meme_format, item.meme_titles, item.meme_reason) == (
        "t1",
        "하이라이트",
        3,
        None,
    )
    idea_prompt = next(c for c in llm.calls if "영상 아이디어" in c)
    assert "밈 형식(대표 영상 제목으로 분류): 하이라이트" in idea_prompt


def test_topics_without_youtube_videos_are_unknown(tmp_path: Path) -> None:
    config = tmp_path / "trends.toml"
    config.write_text('[[groups]]\nname = "t1"\nkeywords = ["t1"]\n', encoding="utf-8")
    llm = MockLLMClient(responses={_Idea: _Idea(idea="아이디어.", format="숏폼")})  # type: ignore[arg-type]
    env = {"DATA_YOUTUBE_DB": str(tmp_path / "none.db"), "DATA_TREND_CONFIG": str(config)}
    out = asyncio.run(
        recommend(ContentRecommendRequest(end_date=END), Naver(), llm, env=env, now=NOW)
    ).output
    [item] = out.items
    assert (item.meme_format, item.meme_reason, item.meme_titles) == (
        "unknown",
        "YouTube 근거 영상이 없음",
        0,
    )

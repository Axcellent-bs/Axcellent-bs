"""domain_subtopics: 씨앗 키워드 → YouTube 한국 숏츠 → 함께 나오는 말 → 프로필 축 분류.

실제 YouTube·LLM을 부르지 않는다 (가짜 커넥터·가짜 LLM).
- 결과 영상 태그·제목에 min_videos개 이상 함께 나온 말만, 씨앗 자체·흔한 말(문서 빈도)·회사 제외
  목록은 뺀다
- 같은 대상(공백만 다름, 같은 영상들의 더 긴 구)은 묶는다
- 축은 프로필 하위 주제 축 + 기타 안에서만
- 씨앗마다 일주일 캐시, 새 검색은 하루 상한까지, 할당량 단위를 센다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import DomainProfileOutput, DomainSubtopicsRequest, SeedKeyword
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import domain_subtopics as dsub
from data_agent.youtube_store import HistoryVideo

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)


def _v(vid: str, title: str, tags: tuple[str, ...], views: int, seconds: int = 40) -> VideoStat:
    return VideoStat(vid, "ch", "27", NOW - timedelta(days=3), views, 1, 1, tags, title=title,
                     channel_title="채널", duration_seconds=seconds, language="ko")  # fmt: skip


RESULTS = {
    "만년필": [
        _v("a1", "만년필 잉크 추천 브이로그", ("만년필", "잉크", "필사", "shorts"), 1000),
        _v("a2", "만년필 잉크 리뷰", ("잉크", "필사", "다꾸"), 3000),
        _v("a3", "필사 루틴 만년필", ("필사", "다꾸", "shorts"), 2000),
        _v("a4", "긴 영상", ("필사",), 9000, seconds=300),  # 숏츠 길이 밖
        _v("a5", "fountain pen", ("잉크",), 500),  # 한국어 아님 (제목·채널 이름에 한글 없음)
    ],
    "무지 노트": [
        _v("b1", "무지노트 다꾸 꿀팁", ("다꾸", "스티커"), 800),
        _v("b2", "다꾸 스티커 정리", ("다꾸", "스티커", "shorts"), 1200),
    ],
}


class FakeYouTube:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def search_query_shorts(self, query, published_after, region="KR", language=None,  # noqa: ANN001, ANN201
                                  max_results=50):  # fmt: skip
        self.calls.append(query)
        if self.fail:
            raise ConnectorUnavailable("RATE_LIMITED", "quota")
        out = RESULTS.get(query, [])
        if query == "만년필":  # a5는 채널 이름도 영문
            out = [*out[:4], VideoStat(**{**out[4].__dict__, "channel_title": "pen"})]
        return out


class AxisLLM(MockLLMClient):
    def __init__(self, answer: dict[str, str]) -> None:
        super().__init__()
        self.answer = answer
        self.seen: dict[str, str] = {}

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.seen = dict(untrusted or {})
        items = []
        for k, v in self.seen.items():
            if k.startswith("주제"):
                name = v.split(" (")[0]
                items.append(dsub._Axis(id=k[2:], axis=self.answer.get(name, "아무축")))
        return dsub._Axes(items=items)


PROFILE = DomainProfileOutput(
    status="known", industry="출판·교육", subtopic_axes=["활용 방식", "같이 묶이는 관심사"],
    seed_keywords=[SeedKeyword(keyword=k, evidence=k, evidence_from="product")
                   for k in ("만년필", "무지 노트", "독서대")],
)  # fmt: skip
HISTORY = [  # 쌓인 인기 영상: 'shorts'는 여러 날·여러 카테고리에 늘 붙는 말
    HistoryVideo(f"h{i}", str(i % 5), ("shorts",), f"영상 {i}",
                 frozenset({date(2026, 11, 1 + i % 3)}))
    for i in range(30)
]  # fmt: skip


def _env(tmp_path: Path, extra: str = "") -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "trends.toml"
    # 흔한 말 제외는 trend_candidates 자동 제외 설정을 그대로 쓴다 (캣북스 설정도 켜져 있음)
    cfg.write_text(
        "[candidates.auto_exclude]\nenabled = true\n[subtopics]\n" + extra, encoding="utf-8"
    )
    return {"DATA_TREND_CONFIG": str(cfg), "DATA_SUBTOPICS_DB": str(tmp_path / "sub.db")}


def _run(env, youtube, llm=None, now=NOW, profile=PROFILE):  # noqa: ANN001, ANN202
    llm = llm or AxisLLM({"필사": "활용 방식", "다꾸": "같이 묶이는 관심사"})
    return asyncio.run(
        dsub.make_subtopics(
            DomainSubtopicsRequest(),
            llm,
            env=env,
            profile=profile,
            youtube=youtube,
            history=HISTORY,
            now=now,
        )  # fmt: skip
    ).output


def test_co_occurring_terms_become_subtopics_with_axes(tmp_path: Path) -> None:
    out = _run(_env(tmp_path), FakeYouTube())
    by = {s.name: s for s in out.subtopics}
    assert set(by) >= {"필사", "다꾸", "잉크", "스티커"}
    assert "만년필" not in by and "만년필 잉크" not in by  # 씨앗을 품은 말은 뺌
    assert "shorts" not in by and "shorts" in out.dropped_common  # 문서 빈도로 찾은 흔한 말
    pil = by["필사"]
    assert pil.seed_keyword == "만년필" and pil.videos == 3  # a1·a2·a3, a4(길이 밖)는 안 셈
    assert pil.median_views == 2000.0 and pil.axis == "활용 방식"
    assert by["다꾸"].axis == "같이 묶이는 관심사"
    assert by["잉크"].axis == "기타"  # 목록 밖 답("아무축")은 기타
    assert out.axes == ["활용 방식", "같이 묶이는 관심사", "기타"]


def test_searches_are_cached_for_a_week_and_capped_per_day(tmp_path: Path) -> None:
    env = _env(tmp_path, "daily_searches = 2\n")
    yt = FakeYouTube()
    first = _run(env, yt)
    assert yt.calls == ["만년필", "무지 노트"]  # 하루 2번
    assert [s.status for s in first.searches] == ["searched", "searched", "skipped"]
    assert "하루 검색 상한 2번" in (first.searches[2].reason or "")
    assert first.quota_used == 2 * dsub.SEARCH_UNITS
    yt2 = FakeYouTube()
    nextday = _run(env, yt2, now=NOW + timedelta(days=1))
    assert yt2.calls == ["독서대"] and nextday.searches[0].status == "cached"
    later = FakeYouTube()
    _run(env, later, now=NOW + timedelta(days=8))  # 일주일 지나면 다시 검색
    assert later.calls == ["만년필", "무지 노트"]


def test_pending_searches_count_for_the_plan_quota(tmp_path: Path) -> None:
    env = _env(tmp_path, "daily_searches = 5\n")
    store = dsub.SubtopicStore(env["DATA_SUBTOPICS_DB"])
    seeds = [s.keyword for s in PROFILE.seed_keywords]
    assert dsub.pending_searches(seeds, store, dsub.load_config(env), NOW) == 3
    store.close()
    _run(env, FakeYouTube())
    store = dsub.SubtopicStore(env["DATA_SUBTOPICS_DB"])
    assert dsub.pending_searches(seeds, store, dsub.load_config(env), NOW) == 0
    store.close()


def test_search_failure_and_missing_axes_say_why(tmp_path: Path) -> None:
    out = _run(_env(tmp_path), FakeYouTube(fail=True))
    assert out.status == "unknown" and all(s.status == "failed" for s in out.searches)
    assert "RATE_LIMITED" in (out.searches[0].reason or "")
    no_axes = PROFILE.model_copy(update={"subtopic_axes": []})
    out2 = _run(_env(tmp_path / "b"), FakeYouTube(), profile=no_axes)
    assert all(s.axis == "기타" for s in out2.subtopics)
    assert "프로필에 하위 주제 축이 없어 모두 기타" in out2.limits


def test_same_object_is_merged() -> None:
    terms = {
        "만년필잉크": dsub.Term("만년필 잉크", {"a", "b"}, {"title"}, set()),
        "만년필잉크추천": dsub.Term("만년필 잉크 추천", {"a"}, {"title"}, set()),
        "잉크": dsub.Term("잉크", {"c"}, {"tag"}, set()),
    }
    kept = {t.name: t for t in dsub.merge_same(terms)}
    assert set(kept) == {"만년필 잉크", "잉크"} and kept["만년필 잉크"].merged == {
        "만년필 잉크 추천"
    }

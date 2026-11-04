"""트렌드 감지 탭 속도: 응답 캐시·설명 문장 캐시·저장된 브리프 먼저. 숫자는 그대로여야 한다.

- 같은 데이터 도장이면 처음 계산한 응답을 그대로 돌려준다 (다시 계산하지 않음)
- 새 스냅샷이 들어오면 도장이 바뀌어 다시 계산한다 (무효)
- 판정판에 같은 판정을 다시 기록해도 도장은 그대로, 판정이 바뀌면 바뀐다
- 설명 문장 캐시: 같은 숫자면 LLM을 다시 부르지 않고, 숫자가 다르면 새로 묻는다. 실패는 저장 안 함
- 저장된 브리프가 있으면 재료를 계산하기 전에 같은 응답을 준다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta
from ax_contracts.schemas.data import TrendEvidence, TrendInsightBriefRequest, TrendVerdict
from data_agent import handlers
from data_agent.connectors.youtube import VideoStat
from data_agent.explain import ExplainCache, _Summary, explain
from data_agent.response_cache import ResponseCache, data_stamp
from data_agent.today_board import TodayBoard
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
VIDEOS = [("v1", 1, ("t1", "lck"), 1000, 3000), ("v2", 2, ("t1",), 1000, 2000),
          ("v3", 3, ("lck", "롤"), 500, 600)]  # fmt: skip


def _stat(vid: str, rank: int, tags: tuple[str, ...], views: int) -> VideoStat:
    return VideoStat(vid, "ch", "20", NOW - timedelta(days=1), views, 10, 1, tags, rank=rank)


def _env(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "trends.toml"
    config.write_text("[candidates]\nmin_videos = 2\n\n[popular]\ntop = 10\n", encoding="utf-8")
    db = tmp_path / "youtube.db"
    store = SnapshotStore(db)
    for at, col in ((NOW - timedelta(hours=2), 3), (NOW - timedelta(hours=1), 4)):
        store.save(at, [Row(_stat(v[0], v[1], v[2], v[col]), ("popular",)) for v in VIDEOS])
    store.close()
    return {"DATA_YOUTUBE_DB": str(db), "DATA_TREND_CONFIG": str(config),
            "DATA_TRENDING_DB": str(tmp_path / "trending.db")}  # fmt: skip


def _request(skill: str) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_speed", token="x",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input={})


def test_popular_response_is_reused_until_a_new_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: E501
    env = _env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(handlers, "RESPONSES", ResponseCache())
    monkeypatch.setattr(handlers, "get_youtube", lambda: None)
    calls: list[int] = []
    real = handlers.popular

    async def counted(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(handlers, "popular", counted)
    first = asyncio.run(handlers.trend_popular(_request("trend_popular")))
    again = asyncio.run(handlers.trend_popular(_request("trend_popular")))
    assert len(calls) == 1 and again.output == first.output  # 같은 응답, 다시 계산 안 함
    store = SnapshotStore(env["DATA_YOUTUBE_DB"])  # 새 스냅샷
    store.save(NOW, [Row(_stat(v[0], v[1], v[2], v[4] + 900), ("popular",)) for v in VIDEOS])
    store.close()
    fresh = asyncio.run(handlers.trend_popular(_request("trend_popular")))
    assert len(calls) == 2 and fresh.output != first.output  # 무효 → 새로 계산


def test_stamp_changes_with_snapshot_and_board_only(tmp_path: Path) -> None:
    env = _env(tmp_path)
    board = TodayBoard(today=lambda: date(2026, 11, 3))
    a = data_stamp(env, board.stamp())
    v = TrendVerdict(group_name="고양이", status="rising", ratio=2.0, evidence=TrendEvidence())
    board.record_verdict(["고양이"], v)
    b = data_stamp(env, board.stamp())
    board.record_verdict(["고양이"], v)  # 같은 판정을 다시 기록
    assert a != b and b == data_stamp(env, board.stamp())


class _Counting(MockLLMClient):
    def __init__(self, line: str) -> None:
        super().__init__()
        self.line = line
        self.asked = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked += 1
        return _Summary(summary=self.line)


def test_explain_cache_reuses_only_the_same_numbers() -> None:
    cache = ExplainCache()
    llm = _Counting("급상승 1개입니다.")
    first = asyncio.run(explain("trend", {"급상승 수": 1}, llm, cache))
    again = asyncio.run(explain("trend", {"급상승 수": 1}, llm, cache))
    assert llm.asked == 1 and again == first and not first.fallback
    asyncio.run(explain("trend", {"급상승 수": 2}, _Counting("급상승 2개입니다."), cache))
    bad = _Counting("급상승 9개입니다.")  # 숫자 검증 실패 → 템플릿, 저장 안 함
    asyncio.run(explain("trend", {"급상승 수": 3}, bad, cache))
    asyncio.run(explain("trend", {"급상승 수": 3}, bad, cache))
    assert bad.asked == 2
    plain = _Counting("급상승 1개입니다.")  # 캐시를 안 주면 늘 묻는다 (다른 skill은 그대로)
    asyncio.run(explain("trend", {"급상승 수": 1}, plain))
    assert plain.asked == 1


def test_stored_brief_is_returned_before_computing_materials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: E501
    from data_agent.skills import trend_insight_brief as brief

    db = tmp_path / "brief.db"
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[brief]\nmax_insights = 3\n", encoding="utf-8")
    env = {"DATA_INSIGHT_BRIEF_DB": str(db), "DATA_TREND_CONFIG": str(cfg)}
    now = datetime(2026, 11, 4, 3, tzinfo=UTC)
    assert brief.cached_brief(TrendInsightBriefRequest(), env, now) is None
    rules = brief.rules_version(brief.load_config(env))
    out = brief.empty(date(2026, 11, 4), rules, now, "테스트")
    store = brief.BriefStore(db)
    store.put(date(2026, 11, 4), rules, out, now)
    store.close()
    hit = brief.cached_brief(TrendInsightBriefRequest(), env, now)
    assert hit is not None and hit.output.cached and hit.output.reason == "테스트"
    assert brief.cached_brief(TrendInsightBriefRequest(regenerate=True), env, now) is None
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(brief, "now_utc", lambda: now)

    async def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("저장된 브리프가 있으면 지금 인기를 계산하지 않는다")

    monkeypatch.setattr("data_agent.skills.trend_popular.popular", boom)
    res = asyncio.run(handlers.trend_insight_brief(_request("trend_insight_brief")))
    assert res.output["reason"] == "테스트" and res.output["cached"] is True

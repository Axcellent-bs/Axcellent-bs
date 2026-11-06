"""YouTube 할당량 정리 평가 정의(`eval/youtube_quota/`)의 약속.

- 새 숏츠 추적 몫 1,200단위 예약: 다른 기능은 6,800단위 안에서 멈추고, 그 뒤에도 새 숏츠 추적은
  자기 몫만큼 검색한다
- 지금 인기 보충 검색: 카테고리당 하루 최대 2번(실패도 셈), 성공한 결과는 6시간 재사용
- 관문은 코드 PR에서 켬 (평가 정의 PR #799는 관찰만: dev 0.0 / 0.3333)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import httpx
import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "youtube_quota"
DAY_START = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)  # 2026-11-06 0시 (한국)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"youtube_quota_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_specs() -> None:
    cases = {c["name"]: c for c in make.cases()}
    assert cases["reserve"]["expected"] == {"others_searches": 68, "others_units_max": 6800,
                                            "fresh_searches": 12}  # fmt: skip
    assert cases["fill_rate_limited_all_day"]["expected"]["max_calls"] == 2
    assert cases["fill_ok_all_day"]["expected"]["max_calls"] == 2  # 0시, 6시
    assert cases["fill_ok_cache_window"]["expected"]["max_calls"] == 1  # 6시간 안
    assert make.cases() == list(cases.values())


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {}
    for c in cases:
        want = c["expected"]
        perfect[c["name"]] = (
            {"others_searches": want["others_searches"], "others_units": 6800,
             "fresh_searches": want["fresh_searches"]}
            if c["kind"] == "reserve" else {"calls": want["max_calls"]}
        )  # fmt: skip
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)
    over = dict(perfect, fill_rate_limited_all_day={"calls": 48})  # 실패를 매번 다시 검색
    assert score.score(cases, over)["quota.fill_cap_respected"] < 1.0


# ---------------------------------------------------------------- 실제 코드에 넣은 답


def _reserve_answer(case: dict) -> dict:
    """실제 커넥터 길(_get_live)로 부른 곳마다 검색을 시도한다. 장부는 이 케이스만의 메모리 장부."""
    from data_agent import youtube_quota
    from data_agent.connectors import ConnectorUnavailable
    from data_agent.connectors import youtube as yt

    env = {yt.KEY_ENV: "eval-key", youtube_quota.CAP_ENV: str(case["cap"]),
           "DATA_FRESH_SHORTS_DAILY_UNITS": str(case["reserve"])}  # fmt: skip
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": []})))  # fmt: skip
    saved = youtube_quota.LEDGER
    youtube_quota.LEDGER = youtube_quota.Ledger(None)
    done: dict[str, int] = {}

    async def run() -> None:
        for step in case["steps"]:
            conn = yt.YouTubeConnector(env, client=client, caller=step["caller"])
            for _ in range(step["tries"]):
                try:
                    await conn._get_live("search", {})
                except ConnectorUnavailable:
                    continue
                done[step["caller"]] = done.get(step["caller"], 0) + 1
        await client.aclose()

    try:
        asyncio.run(run())
        rows = youtube_quota.LEDGER.rows()
    finally:
        youtube_quota.LEDGER = saved
    return {
        "others_searches": sum(n for c, n in done.items() if c != "fresh_shorts"),
        "others_units": sum(u for c, u, _ in rows if c != "fresh_shorts"),
        "fresh_searches": done.get("fresh_shorts", 0),
    }


class _FakeSearch:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls = 0

    async def search_category(self, *args: object, **kwargs: object) -> list:
        from data_agent.connectors import ConnectorUnavailable

        self.calls += 1
        if self.outcome == "fail":
            raise ConnectorUnavailable("RATE_LIMITED", "YouTube 응답 429 (RATE_LIMITED)")
        return []


def _fill_answer(case: dict) -> dict:
    """지금 인기 보충 검색 함수를 정해진 시각마다 부른다 (회사 설정과 같은 하루 상한 9번)."""
    from data_agent.skills import trend_popular as tp

    config = tp.PopularConfig(fill_search_calls=9)
    store = tp.FillStore(":memory:")
    youtube = _FakeSearch(case["outcome"])

    async def run() -> None:
        for h in case["hours"]:
            now = DAY_START + timedelta(hours=h)
            today = now.astimezone(tp.KST).date()
            await tp._fill_rows(config, youtube, store, case["category"], now, today, [],
                                case["term"])  # fmt: skip

    try:
        asyncio.run(run())
    finally:
        store.close()
    return {"calls": youtube.calls}


def _answers() -> dict[str, dict]:
    return {
        c["name"]: _reserve_answer(c) if c["kind"] == "reserve" else _fill_answer(c)
        for c in make.cases()
    }


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (기준선 그대로)


def test_youtube_quota_pass_the_gates() -> None:
    """관문: 예약 몫 보장(다른 기능 6,800 안, 새 숏츠 추적 1,200)·보충 검색 상한(카테고리당 2번)."""
    cases = make.cases()
    metrics = score.score(cases, _answers())
    print("youtube_quota eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
    for name, gate in GATES.items():
        assert metrics[name] >= gate, f"{name} {metrics[name]} (관문 {gate})"

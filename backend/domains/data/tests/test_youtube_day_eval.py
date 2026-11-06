"""YouTube 장부 하루 경계·전체 상한 평가 정의(`eval/youtube_day/`)의 약속.

- 장부의 하루 = 미국 태평양 시간 0시 (서머타임 자동). 경계 앞뒤 사용량은 다른 날로 잡힌다
- 다음 초기화는 한국 시각 문구로
- 합계 8,000 초과 0회 (예약 도입 전에 많이 쓴 날은 새 숏츠도 남은 만큼만)
- 관문은 코드 PR에서 켬 (평가 정의 PR #804는 관찰만: dev 0.0 / 0.0 / 0.5556, 초과 150번)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "youtube_day"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"youtube_day_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_specs() -> None:
    cases = {c["name"]: c for c in make.cases()}
    assert cases["pst_before"]["expected"]["day"] != cases["pst_after"]["expected"]["day"]
    assert cases["today_7950"]["expected"] == {"fresh_searches": 0, "unit_calls": 50,
                                               "over_cap": 0}  # fmt: skip
    assert cases["used_7500"]["expected"]["fresh_searches"] == 5
    assert cases["quiet_day"]["expected"] == {"fresh_searches": 8, "unit_calls": 100,
                                              "over_cap": 0}  # fmt: skip
    assert make.cases() == list(cases.values())


def _perfect(case: dict) -> dict:
    want = case["expected"]
    if case["kind"] == "day":
        return dict(want)
    return {"totals": [case["cap"]], "fresh_searches": want["fresh_searches"],
            "unit_calls": want["unit_calls"]}  # fmt: skip


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {c["name"]: _perfect(c) for c in cases}
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)
    over = dict(perfect, today_7950={"totals": [8001], "fresh_searches": 0, "unit_calls": 50})
    assert score.score(cases, over)["quota_day.cap_kept_rate"] < 1.0
    assert score.over_cap(cases, over) == 1


# ---------------------------------------------------------------- 실제 코드에 넣은 답


def _day_answer(case: dict) -> dict | None:
    from data_agent import youtube_quota

    day_of = getattr(youtube_quota, "day_of", None)
    label = getattr(youtube_quota, "reset_label", None)
    if day_of is None or label is None:  # dev: 시각을 받는 함수가 없음 = 0점
        return None
    at = datetime.fromisoformat(case["at"].replace("Z", "+00:00"))
    return {"day": day_of(at).isoformat(), "label": label(at)}


def _cap_answer(case: dict) -> dict:
    """실제 커넥터 길(_get_live): 다른 기능이 이미 쓴 장부에서 새 숏츠 검색 → 수집기 1단위 호출."""
    from data_agent import youtube_quota
    from data_agent.connectors import ConnectorUnavailable
    from data_agent.connectors import youtube as yt

    env = {yt.KEY_ENV: "eval-key", youtube_quota.CAP_ENV: str(case["cap"]),
           "DATA_FRESH_SHORTS_DAILY_UNITS": str(case["reserve"])}  # fmt: skip
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": []})))  # fmt: skip
    saved = youtube_quota.LEDGER
    youtube_quota.LEDGER = youtube_quota.Ledger(None)
    totals: list[int] = []
    got = {"fresh_searches": 0, "unit_calls": 0}

    async def run() -> None:
        youtube_quota.LEDGER.add("trend_popular", case["used_before"])
        fresh = yt.YouTubeConnector(env, client=client, caller="fresh_shorts")
        collect = yt.YouTubeConnector(env, client=client, caller="youtube_collect")
        plan = (("search", fresh, case["fresh_tries"], "fresh_searches"),
                ("videos", collect, case["unit_calls"], "unit_calls"))
        for resource, conn, n, key in plan:
            for _ in range(n):
                try:
                    await conn._get_live(resource, {})
                except ConnectorUnavailable:
                    continue
                got[key] += 1
                totals.append(youtube_quota.LEDGER.units())
        await client.aclose()

    try:
        asyncio.run(run())
    finally:
        youtube_quota.LEDGER = saved
    return {"totals": totals, **got}


def _answers() -> dict[str, dict | None]:
    return {c["name"]: _day_answer(c) if c["kind"] == "day" else _cap_answer(c)
            for c in make.cases()}  # fmt: skip


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (기준선 그대로)


def test_youtube_day_pass_the_gates() -> None:
    """관문: 태평양 0시 경계(서머타임 전환일 포함)·한국 시각 초기화 문구·합계 8,000 초과 0회."""
    cases = make.cases()
    answers = _answers()
    metrics = score.score(cases, answers)
    print("youtube_day eval:", json.dumps(metrics, ensure_ascii=False),
          "over_cap:", score.over_cap(cases, answers))  # fmt: skip
    assert set(metrics) == set(score.METRICS)
    assert score.over_cap(cases, answers) == 0
    for name, gate in GATES.items():
        assert metrics[name] >= gate, f"{name} {metrics[name]} (관문 {gate})"

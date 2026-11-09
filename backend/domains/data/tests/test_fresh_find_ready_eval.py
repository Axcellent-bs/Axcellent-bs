"""새 숏츠 찾기 준비 평가(`eval/fresh_find_ready/`)의 약속.

- LLM 장부가 형식 판정을 막으면 찾기(검색 100단위)를 부르지 않고 시각도 남기지 않는다
- 막지 않으면 지금처럼 찾고 60초 이하 새 영상을 추적한다
- 평가 정의와 코드를 한 PR에 넣고 관문을 바로 켠다: 04:30 찾기 전에 LLM 사용이 80%를 넘으면
  검색 500단위를 쓰고 0개가 되므로 (dev 관찰값은 PR 본문: 막는 케이스 0.0, 허용 케이스 1.0)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "fresh_find_ready"
NOW = datetime.now(UTC).replace(microsecond=0)  # LLM 장부는 오늘(한국 날짜) 칸을 본다


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"fresh_find_ready_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_rule() -> None:
    by = {c["name"]: c["expected"] for c in make.cases()}
    assert by["llm_low"]["units"] == 202 and by["llm_low"]["added"] == 4
    assert by["llm_just_below"]["searches"] == 2
    for name in ("llm_at_reserve_tokens", "llm_at_reserve_calls", "llm_cap_reached"):
        assert by[name] == {"searches": 0, "units": 0, "finds_recorded": 0, "added": 0}


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {c["name"]: dict(c["expected"]) for c in cases}
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


class FakeYouTube:
    """형식 검색 → 결과 3개(30·59·90초). 장부에 단가대로 적는다."""

    def __init__(self, case: dict, ledger: object) -> None:
        self.case, self.ledger = case, ledger

    def _charge(self, units: int, search: bool = False) -> None:
        from data_agent import youtube_quota

        self.ledger.add("fresh_shorts", units, search=search, day=youtube_quota.day_of(NOW))

    async def search_fresh_shorts(self, query, published_after, **_):  # noqa: ANN001, ANN003, ANN201
        self._charge(100, search=True)
        return await self.videos([f"{query[:2]}{s:02d}xxxxxxx"[:11] for s in self.case["seconds"]])

    async def videos(self, ids):  # noqa: ANN001, ANN201
        from data_agent.connectors.youtube import VideoStat

        self._charge(1)
        return [VideoStat(i, "ch", "24", NOW - timedelta(hours=1), 100, 1, 0, (),
                          title=f"{i} 영상", duration_seconds=int(i[2:4]))
                for i in ids]  # fmt: skip


class FakeLLM:
    """형식 판정: 검색어 앞 두 글자로 형식을 돌려준다 (LLM 장부는 run_tick이 먼저 본다)."""

    def __init__(self, formats: list[str]) -> None:
        self.by_head = {f[:2]: f for f in formats}

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        items = [{"id": k[2:], "format": self.by_head[v[:2]]}
                 for k, v in (untrusted or {}).items() if k.startswith("영상")]  # fmt: skip
        return schema.model_validate({"items": items})


def _run(case: dict, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict | None:
    try:
        from data_agent import fresh_shorts, handlers, llm_budget, youtube_quota
    except ImportError:
        return None
    ledger = youtube_quota.Ledger(None)
    budget = llm_budget.Budget(None)
    budget.add(case["llm_calls"], case["llm_tokens"])
    monkeypatch.setattr(youtube_quota, "LEDGER", ledger)
    monkeypatch.setattr(llm_budget, "BUDGET", budget)
    monkeypatch.setattr(handlers, "get_llm", lambda: FakeLLM(case["formats"]))
    env = {"DATA_YOUTUBE_DB": str(tmp / "youtube.db"), "DATA_SHORTS_DB": str(tmp / "shorts.db"),
           "DATA_FRESH_SHORTS_DB": str(tmp / "fresh.db"), "DATA_LLM_DAILY_CALLS": "200",
           "DATA_LLM_DAILY_TOKENS": "400000"}  # fmt: skip
    result = asyncio.run(fresh_shorts.run_tick(FakeYouTube(case, ledger), env, NOW,
                                               formats=case["formats"]))  # fmt: skip
    day = youtube_quota.day_of(NOW)
    conn = sqlite3.connect(tmp / "fresh.db")
    finds = conn.execute("SELECT COUNT(*) FROM finds").fetchone()[0]
    conn.close()
    return {"searches": result.searches, "finds_recorded": finds, "added": result.added,
            "units": sum(u for c, u, _ in ledger.rows(day) if c == "fresh_shorts")
            - result.tracked_calls}  # fmt: skip


def observe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    answers = {}
    for case in make.cases():
        folder = tmp_path / case["name"]
        folder.mkdir()
        answers[case["name"]] = _run(case, folder, monkeypatch)
    return score.score(make.cases(), answers)


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함


def test_fresh_find_ready_passes_the_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문: LLM 장부가 판정을 막으면 0단위·시각 없음, 아니면 지금처럼 찾고 추적."""
    metrics = observe(tmp_path, monkeypatch)
    print("fresh_find_ready eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
    for name, gate in GATES.items():
        assert metrics[name] >= gate, f"{name} {metrics[name]} (관문 {gate})"

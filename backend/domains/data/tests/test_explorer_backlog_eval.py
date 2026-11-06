"""영상 탐색 밀린 길이 확인·분류 대기 평가 정의(`eval/explorer_backlog/`)의 약속.

- 다시 읽기 하루 50유닛 (YouTube 장부 날짜), 숏 후보·최근 것 먼저
- 분류 묶음에서 개별 결과가 섞이지 않음, LLM 상한 가까우면 숏츠만 (숏츠 먼저)
- 이 PR에서는 관찰만 (관문은 코드 PR에서)
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "explorer_backlog"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"explorer_backlog_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_answers_come_from_the_specs() -> None:
    cases = {c["name"]: c for c in make.cases()}
    assert cases["reread_cap"]["expected"]["calls"] == [50, 0, 50]
    order = cases["reread_order"]["expected"]["read"]
    assert len(order) == 50 and not any(v.startswith("fl") for v in order)
    assert cases["batch_isolation"]["expected"]["b07"] == "unknown"
    assert cases["near_cap_shorts_only"]["expected"]["asked"][0] == "s00"
    assert make.cases() == list(cases.values())


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {}
    for c in cases:
        want = c["expected"]
        perfect[c["name"]] = {
            "reread_cap": {"calls": want.get("calls")},
            "reread_order": {"read": want.get("read")},
            "batch": {"categories": want},
            "near_cap": {"asked": want.get("asked")},
        }[c["kind"]]
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


# ---------------------------------------------------------------- 실제 코드에 넣은 답


class _FakeYouTube:
    def __init__(self, seconds: dict[str, int]) -> None:
        self.seconds = seconds
        self.calls: list[list[str]] = []
        self.now = datetime.now(UTC)

    async def videos(self, ids):  # noqa: ANN001, ANN201
        from data_agent.connectors.youtube import VideoStat

        self.calls.append(list(ids))
        return [VideoStat(i, "c", "24", self.now - timedelta(days=1), 100, 1, 1, (), title="한글",
                          channel_title="한글 채널", duration_seconds=self.seconds.get(i, 30))
                for i in ids]  # fmt: skip


def _env(tmp: Path, units: int | None) -> dict[str, str]:
    toml = tmp / "trends.toml"
    extra = f"video_units = {units}\nvideo_calls = {units}\n" if units else ""
    toml.write_text('[explorer]\ncategories = ["게임"]\n' + extra, encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(toml), "DATA_SHORTS_DB": str(tmp / "shorts.db"),
            "DATA_POPULAR_VIDEOS_DB": str(tmp / "fill.db"),
            "DATA_SUBTOPICS_DB": str(tmp / "none-subtopics.db")}  # fmt: skip


def _fill(env: dict[str, str], day, rows: list[dict]) -> None:  # noqa: ANN001
    from data_agent.skills.trend_popular import FillStore

    store = FillStore(env["DATA_POPULAR_VIDEOS_DB"])
    store.put(day, "24", "ko", rows)
    store.close()


def _refresh(env: dict[str, str], yt: _FakeYouTube, store, now: datetime) -> None:  # noqa: ANN001
    from data_agent.skills import video_explorer as ve

    yt.now = now
    asyncio.run(ve._refresh(env, yt, store, ve.load_config(env), {}, now, ve._day(now), [], []))


def _cap_answer(case: dict, tmp: Path) -> dict:
    from data_agent.skills import video_explorer as ve

    env = _env(tmp, None)
    first = _at(case["runs"][0])
    rows = [{"video_id": f"L{n:04d}", "duration_seconds": 600,
             "published_at": (first - timedelta(hours=3)).isoformat()}
            for n in range(case["longs"])]  # fmt: skip
    _fill(env, ve._day(first), rows)
    store = ve.ExplorerStore(tmp / "explorer.db")
    yt = _FakeYouTube({})
    calls = []
    try:
        for run in case["runs"]:
            before = len(yt.calls)
            _refresh(env, yt, store, _at(run))
            calls.append(len(yt.calls) - before)
    finally:
        store.close()
    return {"calls": calls}


def _order_answer(case: dict, tmp: Path) -> dict:
    from data_agent.connectors.youtube import VideoStat
    from data_agent.skills import video_explorer as ve
    from data_agent.skills.shorts_stats import ShortsStore

    env = _env(tmp, case["units"])
    now = _at(case["now"])
    shorts = ShortsStore(env["DATA_SHORTS_DB"])
    for s in case["shorts_sample"]:
        stat = VideoStat(s["video_id"], "c", "24", now - timedelta(days=s["day_ago"]), 100, 1, 1,
                         (), title="한글 숏", channel_title="한글",
                         duration_seconds=30)  # fmt: skip
        shorts.save_search(ve._day(now) - timedelta(days=s["day_ago"]), "24", "known", None,
                           [stat], language="ko")  # fmt: skip
    shorts.close()
    _fill(env, ve._day(now), [{"video_id": f["video_id"], "duration_seconds": f["seconds"],
                               "published_at": (now - timedelta(hours=f["hours_ago"])).isoformat()}
                              for f in case["fill"]])  # fmt: skip
    store = ve.ExplorerStore(tmp / "explorer.db")
    yt = _FakeYouTube({})
    try:
        _refresh(env, yt, store, now)
    finally:
        store.close()
    return {"read": [v for call in yt.calls for v in call]}


class _ShuffleLLM:
    """답 순서를 뒤집고 한 영상을 빼서 돌려준다. 물은 제목을 기록한다."""

    def __init__(self, drop_titles: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.asked: list[str] = []
        self.batches: list[int] = []
        self.drop = drop_titles

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        from data_agent.skills.topic_category import _Categories, _Item

        data = dict(untrusted or {})
        items = []
        for key, title in data.items():
            if not key.startswith("영상"):
                continue
            self.asked.append(title)
            if title in self.drop:
                continue
            n = key[2:].split(" ")[0]
            cat = "게임" if "게임" in title else "유머" if "웃긴" in title else "생활·취미"
            items.append(_Item(id=n, category=cat, real_entity=False))
        self.batches.append(len([k for k in data if k.startswith("영상")]))
        return _Categories(items=list(reversed(items)))


def _batch_answer(case: dict) -> dict:
    from data_agent.skills.topic_category import VideoInput, classify_videos

    titles = {v["video_id"]: v["title"] for v in case["videos"]}
    llm = _ShuffleLLM({titles[case["drop"]]})
    found = asyncio.run(classify_videos([VideoInput(v, t) for v, t in titles.items()], llm))
    return {"categories": {v: r.category for v, r in found.items()}}


def _near_answer(case: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    from data_agent import llm_budget
    from data_agent.connectors.youtube import VideoStat
    from data_agent.skills import video_explorer as ve

    budget = llm_budget.Budget(None)
    budget.add(int(case["share"] * 100), 0)
    monkeypatch.setattr(llm_budget, "BUDGET", budget)
    env = {"DATA_LLM_DAILY_CALLS": "100"}
    now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    pool = {v["video_id"]: ve._Item(VideoStat(v["video_id"], "c", "24", now, v["views"], 1, 1, (),
                                              title=v["video_id"], channel_title="한글",
                                              duration_seconds=v["seconds"]), "chart", now)
            for v in case["videos"]}  # fmt: skip
    config = dataclasses.replace(ve.ExplorerConfig(), classify_max=case["classify_max"])
    llm = _ShuffleLLM()
    try:
        asyncio.run(ve._classify(pool, config, llm, None, None, [], env=env, max_seconds=60))
    except TypeError:  # dev: env·max_seconds를 받지 않음
        asyncio.run(ve._classify(pool, config, llm, None, None, []))
    return {"asked": llm.asked}


def _answers(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in make.cases():
        folder = tmp / c["name"]
        folder.mkdir()
        if c["kind"] == "reread_cap":
            out[c["name"]] = _cap_answer(c, folder)
        elif c["kind"] == "reread_order":
            out[c["name"]] = _order_answer(c, folder)
        elif c["kind"] == "batch":
            out[c["name"]] = _batch_answer(c)
        else:
            out[c["name"]] = _near_answer(c, monkeypatch)
    return out


def test_explorer_backlog_observe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관찰만: 지금 코드의 점수를 남긴다 (관문은 코드 PR에서 켬)."""
    cases = make.cases()
    metrics = score.score(cases, _answers(tmp_path, monkeypatch))
    print("explorer_backlog eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)

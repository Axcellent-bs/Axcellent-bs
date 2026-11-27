"""기획 장면 시간 = 대본·스토리보드 칸 시간 (11/27 운영 wr_0af5abe5f5 · PROP-44).

기획 장면은 0~2/2~7/7~12/12~15초인데, 의뢰 준비에서 쓴 대본과 스토리보드 칸은 고르게 나눈
0~4/4~7.5/7.5~11/11~15초였다(규칙 대본 split_times, LLM 대본이 시간을 새로 정하고 칸에 덮어씀).
가짜 LLM, 메모리 DB로만 돈다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from marketing_agent import creative_records as cr
from marketing_agent import creative_script as cs
from marketing_agent import request_script as rs
from marketing_agent.video_draft import VideoPlanIn


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = _load("request_script_helpers_plan_times", "test_request_script.py")
store = S.store
TIMES = [(0, 2), (2, 7), (7, 12), (12, 15)]


def _plan(times: list[tuple[float, float]] | None = TIMES) -> VideoPlanIn:
    scenes = []
    for i in range(1, 5):
        s: dict[str, Any] = {"prompt": f"scene {i} prompt text", "visual": f"화면 {i}",
                             "caption": f"자막 {i}"}  # fmt: skip
        if times:
            s |= {"start_s": times[i - 1][0], "end_s": times[i - 1][1]}
        scenes.append(s)
    return VideoPlanIn.model_validate({"concept": "겨울 독서", "script": "s", "duration_s": 15,
                                       "scenes": scenes})  # fmt: skip


class EvenLLM:
    """시간을 제 마음대로 정하는 작가 (가중치 같게 → 고르게 나눈 시간). 장면 수도 n."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.prompt = ""
        self.data: dict[str, Any] | None = None

    async def structured(self, prompt: str, schema: type, untrusted: dict | None = None, **_: Any):
        self.prompt, self.data = prompt, untrusted
        scenes = [{"visual_ko": f"장면 {i}", "line_ko": f"대사 {i}", "speaker_ko": "내레이션",
                   "weight": 1} for i in range(1, self.n + 1)]  # fmt: skip
        return cs.ScriptRead(title_ko="겨울 독서", scenes=scenes, cta_ko="")


def _times(rows: list[dict[str, Any]]) -> list[tuple[float, float]]:
    return [(r["start_s"], r["end_s"]) for r in rows]


def test_llm_script_and_board_use_the_plan_times(store: cr.CreativeRecords) -> None:
    plan = _plan()
    rid = S._record(store, plan)  # noqa: SLF001
    llm = EvenLLM(4)
    assert asyncio.run(rs.attach(rid, plan, "겨울 독서 홍보", lambda: llm, "ctx")) is True
    rec = store.get(rid) or {}
    assert rec["script"]["source"] == "llm"
    assert _times(rec["script"]["scenes"]) == TIMES  # 작가가 0~3.75… 줘도 기획 시간
    assert _times(rec["storyboard"]["shots"]) == TIMES  # 칸 시간(그림 밑 글)도 기획 시간
    assert [s["line_ko"] for s in rec["storyboard"]["shots"]] == ["대사 1", "대사 2", "대사 3",
                                                                    "대사 4"]  # fmt: skip
    # 지시문: 시간은 주어진 값 그대로 (데이터 칸 scene_times)
    assert cs.TIMES_RULE.strip() in llm.prompt
    assert (llm.data or {})["scene_times"][1] == {"no": 2, "start_s": 2.0, "end_s": 7.0}


@pytest.mark.parametrize("n", [2, 6])
def test_llm_with_a_different_scene_count_is_fitted_to_the_plan(
    store: cr.CreativeRecords, n: int
) -> None:
    plan = _plan()
    rid = S._record(store, plan)  # noqa: SLF001
    assert asyncio.run(rs.attach(rid, plan, "겨울 독서 홍보", lambda: EvenLLM(n), "ctx")) is True
    rec = store.get(rid) or {}
    script = rec["script"]["scenes"]
    assert len(script) == 4 and _times(script) == TIMES
    assert [s["no"] for s in script] == [1, 2, 3, 4]
    if n == 2:  # 모자란 장면은 기획 장면(빈 대사)으로
        assert [s["line_ko"] for s in script] == ["대사 1", "대사 2", "", ""]
        assert script[2]["visual_ko"] == "화면 3"
    else:  # 많으면 자른다
        assert [s["line_ko"] for s in script] == ["대사 1", "대사 2", "대사 3", "대사 4"]
    assert _times(rec["storyboard"]["shots"]) == TIMES


def test_rule_script_uses_the_plan_times(store: cr.CreativeRecords) -> None:
    plan = _plan()
    rid = S._record(store, plan)  # noqa: SLF001
    assert asyncio.run(rs.attach(rid, plan, "겨울 독서 홍보", None)) is True
    rec = store.get(rid) or {}
    assert _times(rec["script"]["scenes"]) == TIMES
    assert _times(rec["storyboard"]["shots"]) == TIMES


def test_without_plan_times_the_script_is_split_as_today() -> None:
    plan = _plan(None)
    assert rs.plan_times(plan) is None
    got = rs.plan_script(plan)
    assert got is not None
    want = cs.split_times([1.0] * 4, 15)
    assert [(s.start_s, s.end_s) for s in got.scenes] == [tuple(t) for t in want]
    # LLM 대본도 지금처럼 작가가 정한 시간 그대로 (기획 시간 고정 없음)
    script = asyncio.run(rs.write_request_script(plan, "홍보", {}, lambda: EvenLLM(4)))
    assert script is not None and script.source == "llm" and len(script.scenes) == 4
    assert [(s.start_s, s.end_s) for s in script.scenes] == [tuple(t) for t in want]
    llm = EvenLLM(4)
    asyncio.run(rs.write_request_script(plan, "홍보", {}, lambda: llm))
    assert cs.TIMES_RULE.strip() not in llm.prompt and "scene_times" not in (llm.data or {})


def test_broken_plan_times_fall_back_to_even_split() -> None:
    gap = _plan([(0, 2), (3, 7), (7, 12), (12, 15)])  # 2~3초가 비었다
    assert rs.plan_times(gap) is None
    back = _plan([(0, 2), (2, 1), (1, 12), (12, 15)])
    assert rs.plan_times(back) is None


def test_a_human_edited_script_is_left_alone(store: cr.CreativeRecords) -> None:
    plan = _plan()
    rid = S._record(store, plan)  # noqa: SLF001
    store.attach_script(rid, {"title_ko": "사람이 쓴 대본"}, None)
    assert asyncio.run(rs.attach(rid, plan, "홍보", lambda: EvenLLM(4))) is False
    assert (store.get(rid) or {})["script"]["title_ko"] == "사람이 쓴 대본"

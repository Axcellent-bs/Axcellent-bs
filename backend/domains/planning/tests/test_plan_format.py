"""보고서 추천 형식 카드·웃음 재료 상황을 기획 제작안에 (11/26). 가짜 LLM만 쓴다.

카드는 지시문에(우리가 쓴 카드), 상황은 데이터 칸에만(고객·외부 글에서 나온 요약)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import format_cards
from ax_contracts.schemas.planning import ReceiveInsightOutput
from ax_workflow.models import HumorSituation
from planning_agent import plan_format as pf
from planning_agent import video_plan as vp
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_format", "test_video_plan.py")
C = _load("company_character_helpers_format", "test_plan_company_character.py")
R = _load("request_rules_helpers_format", "test_plan_request_rules.py")
SITUATION = "시험 전날 계획표 vs 새벽 3시 실제 모습"
SITUATIONS = [HumorSituation(text_ko=SITUATION, format_id="F-02"),
              HumorSituation(text_ko="벼락치기 다짐", format_id="F-03")]  # fmt: skip
F02_BEATS = ["기대", "현실", "펀치 리액션", "엔드카드"]


def _prop(**kw: Any) -> Any:
    return V._prop(format="숏폼 15초", **kw)  # noqa: SLF001


def _answer(beats: list[str] | None) -> Any:
    scenes = [
        {"visual": f"장면 {i} 책상", "caption": "", "prompt": f"A student at a desk, shot {i}",
         "beat_ko": beats[i - 1] if beats else ""}
        for i in range(1, 5)
    ]  # fmt: skip
    return vp._PlanOut.model_validate(  # noqa: SLF001
        {"concept": "시험 기간 밈", "script": "기대하고 현실을 본다", "scenes": scenes}
    )


def _write(p: Any, *answers: Any) -> tuple[Any, list[str]]:
    llm = C.SeqLLM(plans=list(answers))
    plan = asyncio.run(vp.write(llm, p, "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    return plan, V.plan_calls(llm)


def test_recommended_card_goes_into_the_prompt_and_the_plan() -> None:
    p = _prop(format_ids=["F-02"], humor_situations=SITUATIONS)
    plan, sent = _write(p, _answer(F02_BEATS))
    assert len(sent) == 1  # LLM 호출 수 그대로 (같은 호출에 beat_ko)
    head, _, data = sent[0].partition("<untrusted_data>")
    assert "형식 예시" in head and "기대 vs 현실 (F-02)" in head
    assert "기대 20% → 현실 25% → 펀치 리액션 35%(펀치) → 엔드카드 20%" in head
    assert SITUATION not in head and SITUATION in data  # 상황은 데이터 칸에만
    assert plan.format_id == "F-02" and plan.situation_ko == SITUATION
    assert plan.format_name == "기대 vs 현실"
    assert [s.beat_ko for s in plan.scenes] == F02_BEATS
    assert [s.beat_punch for s in plan.scenes] == [False, False, True, False]  # 카드 펀치 비트
    # 장면 시간: 비트 비율, 단 첫 훅 0~2초·마지막 장면 3초(재미 먼저)는 지금 규칙
    assert [(s.start_s, s.end_s) for s in plan.scenes] == [(0, 2), (2, 6), (6, 12), (12, 15)]


def test_cards_not_to_recommend_are_skipped() -> None:
    crowded = format_cards.by_id()["F-14"]
    assert not crowded.recommend and crowded.people > format_cards.MAX_PEOPLE
    card = pf.card_for(_prop(format_ids=["F-14", "F-02"]))
    assert card is not None and card.id == "F-02"
    assert pf.card_for(_prop(format_ids=["F-14"])) is None
    assert pf.card_for(_prop(format_ids=["F-99"])) is None  # 모르는 id


def test_llm_that_did_not_use_the_format_gives_no_format() -> None:
    plan, _ = _write(_prop(format_ids=["F-02"], humor_situations=SITUATIONS), _answer(None))
    assert plan.format_id == "" and plan.situation_ko == ""
    assert all(s.beat_ko == "" for s in plan.scenes)


def test_request_rules_win_over_the_format() -> None:
    """의뢰 지시를 못 지키면(다시 써도) 형식 없이 지금처럼."""
    bad = R._answer(False)  # noqa: SLF001 - 무대·마이크·마지막 대사를 버린 답
    pairs = zip(bad.scenes, F02_BEATS, strict=True)
    beats = [s.model_copy(update={"beat_ko": b}) for s, b in pairs]
    bad = bad.model_copy(update={"scenes": beats})
    p = _prop(format_ids=["F-02"], humor_situations=SITUATIONS, request_text=R.REQUEST)
    plan, sent = _write(p, bad, bad)
    assert len(sent) == 2 and plan.format_id == "" and all(s.beat_ko == "" for s in plan.scenes)


def test_old_reports_and_requests_are_the_same_as_today() -> None:
    old, old_sent = _write(_prop(), _answer(None))
    empty, empty_sent = _write(_prop(format_ids=[], humor_situations=[]), _answer(None))
    assert old_sent == empty_sent  # 지시문·데이터 블록 문자열 그대로
    assert "형식 예시" not in old_sent[0] and "humor_situations" not in old_sent[0]
    assert old.format_id == "" and old == empty
    assert [(s.start_s, s.end_s) for s in old.scenes] == vp.fun_timings(15, 4)


def test_beat_cuts_keep_the_existing_rules() -> None:
    card = format_cards.by_id()["F-02"]
    assert pf.beat_cuts(card, 15, 3, None, None) is None  # 장면 수 ≠ 비트 수 → 지금 규칙
    assert pf.beat_cuts(card, 20, 4, None, None) == [(0, 4), (4, 9), (9, 16), (16, 20)]
    assert pf.beat_cuts(card, 15, 4, 2, 12) == [(0, 2), (2, 6), (6, 12), (12, 15)]


def test_receive_keeps_format_ids_and_situations_on_the_proposal() -> None:
    writer = V.Writer(V._mock(V._llm_out()))  # noqa: SLF001
    svc = PlanningService(gate=V._gate, relater=None, marketing=None, proposal_writer=writer)  # noqa: SLF001
    inp = V._insight().model_copy(update={  # noqa: SLF001
        "format_ids": ["F-02"],
        "humor_situations": [{"text_ko": SITUATION, "format_id": "F-02"}],
    })  # fmt: skip
    inp = type(inp).model_validate(inp.model_dump())
    res = asyncio.run(svc.receive_insight(requester="orchestrator", inp=inp))
    pid = ReceiveInsightOutput.model_validate(res.output).proposal_id
    p = next(x for x in svc.state.proposals if x.id == pid)
    assert p.format_ids == ["F-02"] and p.humor_situations[0].text_ko == SITUATION

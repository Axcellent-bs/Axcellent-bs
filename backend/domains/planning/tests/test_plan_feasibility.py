"""기획 자체 제작 가능성 검사와 검사 기록 (11/26).

AI 영상(Flash 15초 한 번 생성)은 한 화면 인물 3명 이상·장면 안 여러 동작·손 소품 여럿을 잘 못
만든다. 영상 전에 기획이 걸러 한 번 다시 쓰고, 그 기록(✕ → 다시 쓰기 → ✓)을 제작안에 남긴다.
가짜 LLM만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from planning_agent import plan_checks as pc
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_feasibility", "test_video_plan.py")
C = _load("company_character_helpers_feasibility", "test_plan_company_character.py")
R = _load("request_rules_helpers_feasibility", "test_plan_request_rules.py")
ASK = (
    "회사 캐릭터가 친구 두 명과 셋이서 무대에서 동시에 점프하고 회전하며 춤추다 캣북스를 "
    "광고하는 영상 15초"
)


def _scene(i: int, **kw: Any) -> dict[str, Any]:
    base = {"visual": f"무대 위 장면 {i}", "caption": "",
            "prompt": f"A dancer on a stage, shot {i}", "action_en": "she waves at the camera",
            "people": 1, "props": []}  # fmt: skip
    return base | kw


def _answer(scenes: list[dict[str, Any]], **kw: Any) -> Any:
    return vp._PlanOut.model_validate(  # noqa: SLF001
        {"concept": "무대 위 춤", "script": "춤추고 광고한다", "scenes": scenes} | kw
    )


def _write(*answers: Any, request: str | None = None) -> tuple[Any, list[str]]:
    llm = C.SeqLLM(plans=list(answers))
    plan = asyncio.run(vp.write(llm, V._prop(request_text=request), "ctx"))  # noqa: SLF001
    return plan, V.plan_calls(llm)


TRIO = [_scene(1), _scene(2, people=3, visual="세 사람이 무대에서 춤춘다"), _scene(3), _scene(4)]
DUO = [_scene(1), _scene(2, people=2, visual="두 사람이 무대에서 춤춘다"), _scene(3), _scene(4)]
ADJ = {"rule_text_ko": "셋이서", "changed_to_ko": "회사 캐릭터와 친구 한 명, 둘",
       "why_ko": "한 화면 인물 2명까지 (제작 검사)"}  # fmt: skip


def test_three_people_fail_then_the_rewrite_with_two_passes_and_is_logged() -> None:
    plan, sent = _write(_answer(TRIO), _answer(DUO, adjustments=[ADJ]))
    assert len(sent) == 2
    assert "제작 검사 장면 2: 인물 3명 (허용 2명)" in sent[1]
    assert vp.FEASIBILITY_FIRST.strip() in sent[1]
    first, second = plan.check_log
    assert (first.attempt, first.by, first.passed) == (1, "planning", False)
    assert [(x.kind, x.scene, x.detail_ko) for x in first.problems] == [
        ("people", 2, "인물 3명 (허용 2명)")
    ]
    assert (second.attempt, second.passed, second.problems) == (2, True, [])
    assert [a.model_dump() for a in plan.adjustments] == [ADJ]
    assert plan.written_by == "llm" and plan.scenes[1].visual == "두 사람이 무대에서 춤춘다"


def test_people_word_in_the_text_fails_even_if_people_says_one() -> None:
    lie = [_scene(1, visual="회사 캐릭터가 친구들과 셋이서 노래한다", people=1)]
    assert [(x["kind"], x["scene"]) for x in pc.feasibility(lie)] == [("people", 1)]
    assert pc.people_in_text("3명이 앉아 있다") == 3 and pc.people_in_text("혼자 걷는다") == 0
    assert pc.feasibility([_scene(1, visual="무대 뒤 군중", people=1)])[0]["kind"] == "people"


def test_two_actions_in_one_scene_fail() -> None:
    jump = pc.feasibility([_scene(1, visual="무대에서 점프하고 회전하며 춤춘다")])
    want = '동작 2개 "점프하고 회전하며" (허용 1개)'
    assert jump == [{"kind": "actions", "scene": 1, "detail_ko": want}]
    then = pc.feasibility([_scene(1, action_en="she jumps then spins while singing")])
    assert then[0]["kind"] == "actions" and then[0]["detail_ko"].startswith("동작 3개")
    assert pc.feasibility([_scene(1, visual="동시에 손을 든다")])[0]["kind"] == "actions"
    assert pc.feasibility([_scene(1, visual="마이크를 들고 노래한다")]) == []  # 들고 ≠ 하고


def test_three_props_fail_and_end_card_scenes_are_not_checked() -> None:
    assert pc.feasibility([_scene(1, props=["마이크", "꽃다발", "가방"])])[0]["detail_ko"] == (
        "소품 3개 (허용 2개)"
    )
    card = _scene(4, visual="채널 이름 엔드 카드", people=5, props=["a", "b", "c"])
    assert pc.feasibility([_scene(1), card]) == []


def test_still_failing_after_the_rewrite_keeps_that_plan_with_the_cross() -> None:
    plan, sent = _write(_answer(TRIO), _answer(TRIO))
    assert len(sent) == 2  # 다시 쓰기는 한 번뿐
    assert plan.written_by == "llm" and plan.fallback_reason == ""  # 규칙 초안으로 버리지 않는다
    assert [a.passed for a in plan.check_log] == [False, False]
    assert plan.adjustments == []


def test_request_rules_and_feasibility_share_one_rewrite() -> None:
    bad = R._answer(False).model_copy(  # noqa: SLF001 - B 시험의 지시 버린 답 + 인물 3명
        update={"scenes": [R._answer(False).scenes[0].model_copy(update={"people": 3}),
                           *R._answer(False).scenes[1:]]}  # noqa: SLF001
    )  # fmt: skip
    plan, sent = _write(bad, R._answer(True), request=R.REQUEST)  # noqa: SLF001
    assert len(sent) == 2  # LLM 호출 최대 2회 (B와 합쳐)
    assert "의뢰 지시(대사)" in sent[1] and "제작 검사 장면 1: 인물 3명" in sent[1]
    first, second = plan.check_log
    kinds = {x.kind for x in first.problems}
    assert kinds == {"people", "request_rule"} and second.passed
    # 검사 기록의 의뢰 지시 ✕와 request_rules는 같은 검사 결과다 (두 번 세지 않음)
    assert all(r.kept for r in plan.request_rules)
    assert sum(1 for x in first.problems if x.kind == "request_rule") == 4


def test_no_problem_means_one_call_and_one_passed_row() -> None:
    plan, sent = _write(_answer([_scene(i) for i in range(1, 5)]))
    assert len(sent) == 1 and [(a.attempt, a.passed) for a in plan.check_log] == [(1, True)]
    assert plan.adjustments == []


def test_rule_plan_has_no_check_log() -> None:
    plan = asyncio.run(vp.write(None, V._prop(), "ctx"))  # noqa: SLF001
    assert plan.written_by == "rule" and plan.check_log == [] and plan.adjustments == []


def test_limits_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pc.PEOPLE_MAX_ENV, "3")
    assert pc.feasibility([_scene(1, people=3)]) == []
    monkeypatch.setenv(pc.PEOPLE_MAX_ENV, "x")  # 잘못된 값은 기본값
    assert pc.limits() == (pc.PEOPLE_MAX, pc.ACTIONS_MAX, pc.PROPS_MAX)


def test_the_presentation_request_makes_a_cross_then_a_check() -> None:
    """발표 시연 의뢰 문장: 1차는 셋이서·동시에 점프하고 회전 → ✕, 2차 둘·동작 하나 → ✓."""
    trio = [_scene(1, visual="회사 캐릭터가 친구 두 명과 셋이서 무대에 선다", people=3),
            _scene(2, visual="셋이 동시에 점프하고 회전하며 춤춘다", people=3), _scene(3),
            _scene(4, visual="캐릭터가 카메라를 보며 캣북스를 광고한다")]  # fmt: skip
    duo = [_scene(1, visual="회사 캐릭터와 친구 한 명이 무대에 선다", people=2),
           _scene(2, visual="둘이 점프한다", people=2), _scene(3),
           _scene(4, visual="캐릭터가 카메라를 보며 캣북스를 광고한다")]  # fmt: skip
    plan, sent = _write(_answer(trio), _answer(duo, adjustments=[ADJ]), request=ASK)
    assert len(sent) == 2 and [a.passed for a in plan.check_log] == [False, True]
    kinds = [(x.kind, x.scene) for x in plan.check_log[0].problems]
    assert ("people", 1) in kinds and ("people", 2) in kinds and ("actions", 2) in kinds
    assert plan.adjustments and plan.adjustments[0].rule_text_ko == "셋이서"

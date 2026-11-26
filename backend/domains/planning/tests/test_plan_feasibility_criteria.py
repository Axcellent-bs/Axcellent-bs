"""제작 가능성 검사 기준 고치기 (11/26).

- 인물: 인원수가 아니라 "참조 없는 얼굴". 회사 캐릭터가 있으면 다른 인물이 가까이(군중·와이드
  아님) 나올 때 ✕ (마케팅 확인 refs와 같은 공용 기준 ax_agent_template.plan_rules). 없으면 지금처럼
  인원수 상한
- 소품 개수는 보지 않는다
- 장소 전환: Flash는 15초를 한 번에 만들어 장소가 2번 넘게 바뀌면 ✕
가짜 LLM만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import plan_rules
from ax_contracts.schemas.planning import CheckProblem, VideoScene
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


V = _load("video_plan_helpers_criteria", "test_video_plan.py")
C = _load("company_character_helpers_criteria", "test_plan_company_character.py")
F = _load("feasibility_helpers_criteria", "test_plan_feasibility.py")
_scene, _answer = F._scene, F._answer  # noqa: SLF001
FRIEND = "회사 캐릭터가 친구와 책상에 마주 앉아 웃는다"


def _write(*answers: Any, character: Any = None) -> tuple[Any, list[str]]:
    llm = C.SeqLLM(plans=list(answers))
    prop = V._prop(company_character=character)  # noqa: SLF001
    plan = asyncio.run(vp.write(llm, prop, "ctx"))
    return plan, V.plan_calls(llm)


def _places(*places: str) -> list[dict[str, Any]]:
    return [_scene(i, place_ko=p) for i, p in enumerate(places, start=1)]


# ---------------------------------------------------------------- 참조 없는 인물


def test_company_character_with_a_friend_in_medium_shot_is_a_close_person() -> None:
    got = pc.feasibility([_scene(1, visual=FRIEND, people=2, camera_en="medium shot")],
                         has_character=True)  # fmt: skip
    assert got == [{"kind": "people", "code": "close_person", "scene": 1,
                    "detail_ko": pc.CLOSE_PERSON_KO}]  # fmt: skip
    # people ≥ 2도 근거로 본다 (낱말이 없어도)
    two = pc.feasibility([_scene(1, visual="책상 앞에 둘이 앉는다", people=2)], has_character=True)
    assert [x.get("code") for x in two] == ["close_person"]


def test_company_character_with_a_background_crowd_or_a_wide_shot_passes() -> None:
    crowd = _scene(1, visual="회사 캐릭터 뒤 배경에 관객들이 있다", people=5)
    wide = _scene(2, visual="회사 캐릭터가 친구와 걷는다", people=2, camera_en="wide shot")
    alone = _scene(3, visual="회사 캐릭터가 혼자 책을 읽는다", people=1)
    assert pc.feasibility([crowd, wide, alone], has_character=True) == []


def test_without_a_company_character_three_people_still_fail_by_count() -> None:
    got = pc.feasibility([_scene(1, visual=FRIEND, people=3)])
    assert got == [{"kind": "people", "scene": 1, "detail_ko": "인물 3명 (허용 2명)"}]
    assert pc.feasibility([_scene(1, visual=FRIEND, people=2)]) == []  # 2명은 지금처럼 통과


def test_close_person_is_rewritten_once_and_logged_with_its_code() -> None:
    bad = [_scene(1), _scene(2, visual=FRIEND, people=2, camera_en="medium shot"), _scene(3),
           _scene(4)]  # fmt: skip
    good = [_scene(i) for i in range(1, 5)]
    plan, sent = _write(_answer(bad), _answer(good), character=C.VIRTUAL)
    assert len(sent) == 2  # LLM 호출 수 그대로 (다시 쓰기 1회)
    fix = "다른 인물을 빼거나 멀리·뒤에 둔다"
    assert f"제작 검사 장면 2: {pc.CLOSE_PERSON_KO} → {fix}" in sent[1]
    first, second = plan.check_log
    assert [(x.kind, x.code, x.scene) for x in first.problems] == [("people", "close_person", 2)]
    assert second.passed


def test_marketing_uses_the_same_shared_rule() -> None:
    body = "회사 캐릭터가 친구와 마주 본다"
    assert plan_rules.close_other_person(body, "medium shot")  # 마케팅 refs 호출 모양 (people 1)
    assert not plan_rules.close_other_person(body + " 배경에 군중", "")
    assert not plan_rules.close_other_person(body, "wide shot " + body)
    assert not plan_rules.close_other_person("혼자 책을 읽는다", "")
    assert plan_rules.close_other_person("혼자 책을 읽는다", "", people=2)


# ---------------------------------------------------------------- 소품


def test_five_props_pass() -> None:
    props = ["마이크", "꽃다발", "가방", "책", "컵"]
    assert pc.feasibility([_scene(1, props=props)]) == []
    assert pc.PROPS_MAX == 2 and "props" in pc.FIX_TAIL  # 상수·문구는 남는다 (소품 그리기용)


# ---------------------------------------------------------------- 장소 전환


def test_four_places_is_a_place_change() -> None:
    got = pc.feasibility(_places("책상", "무대", "거리", "지하철"))
    assert got == [{"kind": "actions", "code": "place_change", "scene": 4,
                    "detail_ko": "장소가 3번 바뀌어요: 책상 → 무대 → 거리 → 지하철 "
                                 "(15초 한 번 생성이라 최대 2번)"}]  # fmt: skip
    fix = "장소를 2곳 이하로 줄이거나 같은 장소 안에서 일어나게 한다"
    assert pc.problem_fix(got[0]).endswith(fix)


def test_two_places_pass_and_empty_or_josa_counts_as_the_same_place() -> None:
    assert pc.feasibility(_places("책상", "무대")) == []
    assert pc.feasibility(_places("책상", "", "무대", "", "거리")) == []  # 빈 값 = 앞 장면
    assert pc.feasibility(_places("책상", "책상에서", " 책 상 ", "무대", "거리")) == []
    assert pc.place_key("무대에서") == pc.place_key("무대") == "무대"


def test_end_card_scene_is_not_counted() -> None:
    scenes = [*_places("책상", "무대", "거리"),
              _scene(4, visual="채널 이름 엔드 카드", place_ko="검은 화면")]  # fmt: skip
    assert pc.feasibility(scenes) == []


def test_place_limit_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pc.PLACE_CHANGES_MAX_ENV, "3")
    assert pc.feasibility(_places("책상", "무대", "거리", "지하철")) == []
    monkeypatch.setenv(pc.PLACE_CHANGES_MAX_ENV, "0")  # 잘못된 값은 기본값
    assert pc.place_changes_max() == pc.PLACE_CHANGES_MAX


def test_place_change_is_rewritten_once_and_place_goes_to_the_scene() -> None:
    moving = _places("책상", "무대", "거리", "지하철")
    still = _places("책상", "책상", "무대", "무대")
    plan, sent = _write(_answer(moving), _answer(still))
    assert len(sent) == 2
    assert "place_ko" in sent[0] and "장소가 3번 바뀌어요" in sent[1]
    first, second = plan.check_log
    assert [(x.kind, x.code, x.scene) for x in first.problems] == [("actions", "place_change", 4)]
    assert second.passed
    assert [s.place_ko for s in plan.scenes] == ["책상", "책상", "무대", "무대"]


def test_no_problem_is_still_one_call() -> None:
    plan, sent = _write(_answer(_places("책상", "책상", "무대", "무대")), character=C.VIRTUAL)
    assert len(sent) == 1 and [a.passed for a in plan.check_log] == [True]


# ---------------------------------------------------------------- 계약 (추가만)


def test_old_check_problem_and_scene_have_empty_new_fields() -> None:
    old = CheckProblem.model_validate({"kind": "people", "scene": 2, "detail_ko": "인물 3명"})
    assert old.code == ""
    scene = VideoScene(n=1, start_s=0, end_s=3, visual="책상", prompt="A desk")
    assert scene.place_ko == ""
    with pytest.raises(ValueError):
        CheckProblem(kind="people", detail_ko="x", code="c" * 25)

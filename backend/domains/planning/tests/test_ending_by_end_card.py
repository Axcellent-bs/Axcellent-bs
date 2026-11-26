"""채널 이름 엔드카드 지시는 코드가 지킨다 (11/26 G).

운영 wr_eabc06b19e(누비투어): "끝에 누비투어 채널 이름 넣기"(ending)가 ✕였다. 채널 이름 엔드카드는
마케팅 코드가 마지막 2초에 늘 얹는데 마지막 장면 글에 채널 이름이 없다고 못 지킴 처리했고, 다시 쓰기
1회도 이걸 고치려다 낭비됐다. 가짜 LLM만 쓴다."""

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


V = _load("video_plan_helpers_ending_card", "test_video_plan.py")
C = _load("company_character_helpers_ending_card", "test_plan_company_character.py")
LAST = [pc.Scene(1, visual="여행 가방을 싼다"),
        pc.Scene(2, visual="창밖 눈 내리는 풍경으로 마무리")]  # fmt: skip
REQUEST = ("누비투어 겨울 여행 홍보 영상을 만들어줘. 15초 세로 숏폼, 여행 준비하다 생기는 웃긴"
           " 상황으로 풀고 끝에 누비투어 채널 이름을 넣어줘.")  # fmt: skip


@pytest.mark.parametrize(
    "text", ["끝에 누비투어 채널 이름 넣기", "마지막에 로고", "끝에 누비투어 채널명이 나오게 해줘",
             "마지막 엔드카드에 채널 이름"],
)  # fmt: skip
def test_end_card_only_endings_are_kept_by_code(text: str) -> None:
    kept, where = pc.check_rule({"kind": "ending", "text_ko": text}, LAST, "", "누비투어")
    assert (kept, where) == (True, pc.END_CARD_WHERE)


def test_endings_with_action_are_judged_by_the_last_scene_as_before() -> None:
    rule = {"kind": "ending", "text_ko": "끝에 주인공이 여권을 흔들며 누비투어를 외친다"}
    assert pc.check_rule(rule, LAST, "", "누비투어") == (False, "")
    hit = [*LAST[:1], pc.Scene(2, visual="주인공이 여권을 흔들며 외친다")]
    assert pc.check_rule(rule, hit, "", "누비투어") == (True, "장면 2")
    assert not pc.end_card_only("끝나면 카메라 보고 웃으며 캣북스 광고", "캣북스")
    assert not pc.end_card_only("끝에 넣어줘", "누비투어")  # 넣을 것이 없으면 아님


def test_the_rewrite_does_not_spend_its_one_try_on_the_end_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vp, "_company", lambda: {"name": "누비투어"})
    rules = [{"kind": "ending", "text_ko": "끝에 누비투어 채널 이름 넣기"}]
    out = vp._PlanOut.model_validate(V._llm_out().model_dump() | {"request_rules": rules})  # noqa: SLF001
    llm = C.SeqLLM(plans=[out, out])
    plan = asyncio.run(vp.write(llm, V._prop(request_text=REQUEST), "ctx"))  # noqa: SLF001
    assert len(V.plan_calls(llm)) == 1  # 못 지킨 지시가 없어 다시 쓰지 않는다
    assert [(r.kind, r.kept, r.where) for r in plan.request_rules] == [
        ("ending", True, pc.END_CARD_WHERE)]
    assert plan.check_log[0].passed

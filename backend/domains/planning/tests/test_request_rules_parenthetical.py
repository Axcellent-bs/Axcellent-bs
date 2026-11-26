"""의뢰 지시 오판 두 가지 (11/26 I, 운영 wr_6d9d9e6df4 PROP-42 4/6).

- "끝에 캣북스 채널 이름 넣기 (코드가 마지막 2초에 엔드카드로 얹음)": 괄호 속 "코드가·2초에·얹음"을
  연출 낱말로 세서 엔드카드 판정(#1947 G)이 안 먹었다 → 괄호 안 설명은 빼고 본다
- 의뢰문은 "회사 캐릭터가 …"뿐인데 기본 캐릭터 이름으로 외형 지시
  ("원본 일러스트 회사 캐릭터 등장")가 생겼다 → 의뢰문에 외형 묘사가 없으면 look을 버린다
  (회사 캐릭터를 골랐으면 "회사 캐릭터" ✓)
가짜 LLM만 쓴다.
"""

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


V = _load("video_plan_helpers_parenthetical", "test_video_plan.py")
C = _load("company_character_helpers_parenthetical", "test_plan_company_character.py")
REQUEST = (
    "캣북스 겨울 독서 홍보 숏폼 영상 만들어줘. 15초 세로 영상이고 회사 캐릭터가 저녁에 거실 소파에 "
    "앉아 책을 고르다가, 탁자에서 책을 펼쳐 읽고, 눈 오는 바깥 서점 앞에서 손을 흔드는 흐름으로 "
    "해줘. 끝에 캣북스 채널 이름 넣어줘."
)
ENDING = "끝에 캣북스 채널 이름 넣기 (코드가 마지막 2초에 엔드카드로 얹음)"
LOOK = "원본 일러스트 회사 캐릭터 등장"
LAST = [pc.Scene(1, visual="소파에서 책을 고른다"), pc.Scene(2, visual="서점 앞에서 손을 흔든다")]


@pytest.mark.parametrize("text", [ENDING, "끝에 캣북스 채널 이름 [코드가 얹음]",
                                  "마지막에 로고 (엔드카드는 코드가 2초)"])  # fmt: skip
def test_parenthetical_note_is_not_a_direction(text: str) -> None:
    assert pc.end_card_only(text, "캣북스")
    kept, where = pc.check_rule({"kind": "ending", "text_ko": text}, LAST, "", "캣북스")
    assert (kept, where) == (True, pc.END_CARD_WHERE)


def test_clean_rules_drops_the_note_but_keeps_quotes_as_written() -> None:
    got = pc.clean_rules([{"kind": "ending", "text_ko": ENDING},
                          {"kind": "quote", "text_ko": "겨울엔 책 (한 권)"}])  # fmt: skip
    assert got == [{"kind": "ending", "text_ko": "끝에 캣북스 채널 이름 넣기"},
                   {"kind": "quote", "text_ko": "겨울엔 책 (한 권)"}]  # fmt: skip
    # 괄호 밖 연출은 그대로
    assert not pc.end_card_only("끝나면 주인공이 웃는다 (클로즈업)", "캣북스")


def test_pointing_at_the_company_character_makes_no_look_rule() -> None:
    rules = [{"kind": "look", "text_ko": LOOK}, {"kind": "action", "text_ko": "손을 흔든다"}]
    kept, dropped = pc.real_looks(rules, REQUEST)
    assert kept == [{"kind": "action", "text_ko": "손을 흔든다"}] and dropped == 1
    assert pc.look_words(REQUEST) == set()  # "눈 오는"은 외형이 아니다
    assert pc.look_words("what a red hat") == {"red", "hat"}
    assert pc.look_words("that is what we want") == set()  # 영어는 낱말 경계


def test_a_real_look_in_the_request_is_kept_and_judged_as_before() -> None:
    req = "빨간 목도리를 한 회사 캐릭터가 서점 앞에서 손을 흔든다"
    rule = {"kind": "look", "text_ko": "빨간 목도리"}
    kept, dropped = pc.real_looks([rule], req)
    assert kept == [rule] and dropped == 0
    hit = [pc.Scene(1, visual="빨간 목도리를 한 캐릭터가 손을 흔든다")]
    assert pc.check_rule(rule, hit, "") == (True, "장면 1")
    assert pc.check_rule(rule, LAST, "") == (False, "")
    # 외형 지시가 의뢰문 낱말이 아니면(기본 캐릭터 이름 등) 버린다
    assert pc.real_looks([{"kind": "look", "text_ko": "원본 일러스트 캐릭터"}], req)[1] == 1


def test_chosen_company_character_keeps_one_look_line_by_code() -> None:
    rules = [{"kind": "look", "text_ko": LOOK}, {"kind": "look", "text_ko": "회사 캐릭터로"}]
    kept, dropped = pc.real_looks(rules, REQUEST, company_character=True)
    assert kept == [{"kind": "look", "text_ko": pc.COMPANY_LOOK_KO}] and dropped == 1
    assert pc.check_rule(kept[0], LAST, "") == (True, pc.COMPANY_LOOK_WHERE)


def _plan(monkeypatch: pytest.MonkeyPatch, **prop: Any) -> tuple[Any, list[str]]:
    monkeypatch.setattr(vp, "_company", lambda: {"name": "캣북스"})
    rules = [{"kind": "ending", "text_ko": ENDING}, {"kind": "look", "text_ko": LOOK}]
    out = vp._PlanOut.model_validate(V._llm_out().model_dump() | {"request_rules": rules})  # noqa: SLF001
    llm = C.SeqLLM(plans=[out, out])
    plan = asyncio.run(vp.write(llm, V._prop(request_text=REQUEST, **prop), "ctx"))  # noqa: SLF001
    return plan, V.plan_calls(llm)


def test_prop42_rules_are_all_kept_and_the_rewrite_is_not_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, calls = _plan(monkeypatch)
    assert len(calls) == 1  # 고칠 점이 없어 다시 쓰지 않는다
    assert [(r.kind, r.text_ko, r.kept, r.where) for r in plan.request_rules] == [
        ("ending", "끝에 캣북스 채널 이름 넣기", True, pc.END_CARD_WHERE)]
    assert vp.tenant.BRAND_DEFAULTS["character_ko"] not in " ".join(
        r.text_ko for r in plan.request_rules)
    # 지시를 뽑는 LLM 지시에 "괄호를 붙이지 않는다"가 있다
    assert "코드 설명·괄호를 붙이지 않는다" in calls[0]


def test_prop42_with_the_chosen_character_shows_a_kept_character_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, calls = _plan(monkeypatch, company_character=C.VIRTUAL)
    assert len(calls) == 1
    assert [(r.kind, r.text_ko, r.kept, r.where) for r in plan.request_rules] == [
        ("ending", "끝에 캣북스 채널 이름 넣기", True, pc.END_CARD_WHERE),
        ("look", pc.COMPANY_LOOK_KO, True, pc.COMPANY_LOOK_WHERE)]
    p = V._prop(request_text=REQUEST, company_character=C.VIRTUAL)  # noqa: SLF001
    rules = [{"kind": "ending", "text_ko": ENDING}, {"kind": "look", "text_ko": LOOK}]
    got = vp._PlanOut.model_validate(V._llm_out().model_dump() | {"request_rules": rules})  # noqa: SLF001
    fixes = vp.plan_fixes(p, got)
    # 다시 쓰기 "고칠 점"에 안 들어감
    assert not any("채널 이름" in f or "회사 캐릭터 등장" in f for f in fixes)

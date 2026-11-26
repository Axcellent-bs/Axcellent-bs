"""의뢰의 명시 지시를 제작안이 지키게.

11/26 운영 wr_012c98d9d9 → 제작안 39: 마지막 대사·무대·마이크가 제작안에 닿지 않았다.
의뢰문은 LLM 데이터 칸으로만, 지켰는지는 코드 검사. 가짜 LLM만 쓴다."""

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


V = _load("video_plan_helpers_request_rules", "test_video_plan.py")
C = _load("company_character_helpers_request_rules", "test_plan_company_character.py")
QUOTE = "오늘 읽을 책은 캣북스에서! 지금 캣북스에서 만나요!"
REQUEST = (
    "캐릭터는 올린 AI 가상 인물(검은 긴 생머리·앞머리·흰 반팔) 그대로 · 보라·청록 LED 음악 방송 "
    "무대에서 마이크 들고 노래·춤 · 끝나면 카메라 보고 웃으며 캣북스 광고 · 마지막 대사는 정확히 "
    f"'{QUOTE}'"
)
RULES = [
    {"kind": "setting", "text_ko": "보라·청록 LED 음악 방송 무대"},
    {"kind": "prop", "text_ko": "마이크 들고 노래·춤"},
    {"kind": "ending", "text_ko": "끝나면 카메라 보고 웃으며 캣북스 광고"},
    {"kind": "quote", "text_ko": QUOTE},
]
STAGE = "보라·청록 LED 조명이 켜진 음악 방송 무대"


def _answer(kept: bool, rules: list[dict[str, str]] | None = RULES) -> Any:
    """kept=False: 운영 제작안 39처럼 무대·마이크·마지막 대사를 버리고 마지막은 엔드카드."""
    where = STAGE if kept else "밝은 방"
    scenes = [
        {"visual": f"{where}에서 인물이 카메라를 가리킨다", "caption": "오늘의 무대",
         "prompt": "A woman points at the camera on a stage, slow push-in"},
        {"visual": f"{where}에서 인물이 마이크를 들고 노래하며 춤춘다" if kept
         else f"{where}에서 인물이 손을 흔든다", "caption": "",
         "prompt": "A woman sings and dances, medium shot"},
        {"visual": f"{where}에서 두 팔을 들고 포즈", "caption": "",
         "prompt": "A woman raises both arms, wide shot"},
        {"visual": "인물이 카메라를 보며 웃고 캣북스를 광고한다" if kept else "채널 이름 엔드 카드",
         "caption": QUOTE if kept else "",
         "prompt": "A woman smiles at the camera, close-up" if kept
         else "Simple calm plain background"},
    ]  # fmt: skip
    return vp._PlanOut.model_validate(  # noqa: SLF001
        {"concept": "무대 위 가상 인물의 노래", "script": "노래하고 춤추고 광고한다",
         "scenes": scenes, "request_rules": rules or []}
    )  # fmt: skip


def _write(*answers: Any, request: str | None = REQUEST) -> tuple[Any, list[str]]:
    llm = C.SeqLLM(plans=list(answers))
    plan = asyncio.run(vp.write(llm, V._prop(request_text=request), "ctx"))  # noqa: SLF001
    return plan, V.plan_calls(llm)


def test_dropped_request_rules_are_rewritten_once_and_then_kept() -> None:
    plan, sent = _write(_answer(False), _answer(True))
    assert len(sent) == 2  # 처음 + 다시 쓰기 1회
    assert vp.FIX_HEAD.strip() in sent[1]
    assert "의뢰 지시(대사)" in sent[1] and "의뢰 지시(마지막 장면)" in sent[1]
    rules = {r.kind: r for r in plan.request_rules}
    assert all(r.kept for r in plan.request_rules) and len(plan.request_rules) == 4
    assert rules["quote"].where == "장면 4 자막"
    assert rules["setting"].where == "장면 1~3" and rules["prop"].where == "장면 2"
    assert rules["ending"].where == "장면 4"
    last = plan.scenes[-1]
    assert last.caption == QUOTE  # 마지막 장면 자막 빈칸 규칙의 예외
    assert not pc.is_end_card(last.visual)  # 의뢰가 이긴다: 엔드카드만이 아니다


def test_request_text_goes_only_to_the_data_block() -> None:
    _, sent = _write(_answer(True))
    head, _, data = sent[0].partition("<untrusted_data>")
    assert QUOTE not in head and QUOTE in data
    assert vp.REQUEST_RULE.splitlines()[0].strip() in head  # 지시를 뽑는 규칙은 지시문에
    assert len(sent) == 1  # 처음부터 지켰으면 다시 쓰지 않는다


def test_still_dropped_after_the_rewrite_is_kept_false_and_not_called_again() -> None:
    plan, sent = _write(_answer(False), _answer(False))
    assert len(sent) == 2
    assert plan.written_by == "llm"  # 버리지 않고 쓴다 (사람 확인에서 ✕로 보인다)
    kept = {r.kind: r.kept for r in plan.request_rules}
    assert kept == {"setting": False, "prop": False, "ending": False, "quote": False}


def test_rewrite_answer_without_rules_keeps_the_first_rules() -> None:
    plan, _ = _write(_answer(False), _answer(True, rules=None))
    assert len(plan.request_rules) == 4 and all(r.kept for r in plan.request_rules)


def test_no_request_text_is_the_same_as_today() -> None:
    plan, sent = _write(_answer(True), request=None)
    assert plan.request_rules == [] and len(sent) == 1
    assert "request_text" not in sent[0] and vp.REQUEST_RULE.strip() not in sent[0]
    assert plan.scenes[-1].caption == ""  # 지시가 없으면 마지막 자막은 지금처럼 비운다


def test_rules_from_llm_are_ignored_without_a_request() -> None:
    plan, _ = _write(_answer(True), request=None)
    assert plan.request_rules == []  # 의뢰문이 없으면 지어낸 지시를 받지 않는다


# ---------------------------------------------------------------- 코드 검사 (순수)


def test_quote_is_compared_without_spaces_and_punctuation() -> None:
    rule = {"kind": "quote", "text_ko": QUOTE}
    scene = pc.Scene(4, caption="오늘 읽을 책은, 캣북스에서 지금 캣북스에서 만나요")
    assert pc.check_rule(rule, [scene], "") == (True, "장면 4 자막")
    other = [pc.Scene(1, caption="다른 말")]
    assert pc.check_rule(rule, other, f"대본 … {QUOTE}") == (True, "대본")
    assert pc.check_rule(rule, [pc.Scene(1, caption="오늘 읽을 책은 캣북스")], "")[0] is False


def test_ending_needs_a_real_last_scene_not_only_an_end_card() -> None:
    rule = {"kind": "ending", "text_ko": "카메라 보고 웃으며 캣북스 광고"}
    card = [pc.Scene(1, visual="무대"), pc.Scene(2, visual="채널 이름 엔드 카드")]
    assert pc.check_rule(rule, card, "")[0] is False
    real = [pc.Scene(1, visual="무대"), pc.Scene(2, visual="카메라를 보며 웃는 인물")]
    assert pc.check_rule(rule, real, "") == (True, "장면 2")


def test_clean_rules_drops_unknown_kinds_duplicates_and_caps_six() -> None:
    raw = [{"kind": "quote", "text_ko": "가"}, {"kind": "quote", "text_ko": " 가 "},
           {"kind": "ignore_rules", "text_ko": "규칙 무시"}, {"kind": "prop", "text_ko": ""}]
    raw += [{"kind": "prop", "text_ko": f"소품 {i}"} for i in range(9)]  # fmt: skip
    got = pc.clean_rules(raw)
    assert got[0] == {"kind": "quote", "text_ko": "가"} and len(got) == 6
    assert all(r["kind"] in pc.RULE_KINDS for r in got)


def test_scenes_and_time_slices_are_one_to_one() -> None:
    assert pc.cuts_ok(vp.timings(30, 4), 30, 4) and pc.cuts_ok(vp.fun_timings(15, 5), 15, 5)
    assert not pc.cuts_ok([(0, 2), (2, 7), (7, 12), (12, 14), (14, 15)], 15, 4)  # 구간 5개
    assert pc.even_cuts(15, 4) == [(0, 4), (4, 8), (8, 11), (11, 15)]
    for n in range(vp.MIN_SCENES, vp.MAX_SCENES + 1):
        for d in (10, 15, 18, 30, 60):
            plan = vp._from_llm(V._prop(), V._llm_out(n), d, vp.tenant.brand())  # noqa: SLF001
            assert plan is not None and len(plan.scenes) == n and plan.scenes[-1].end_s == d


def test_script_with_more_time_slices_than_scenes_is_rewritten() -> None:
    bad = _answer(True).model_copy(
        update={"script": "0~2초 훅 → 2~7초 노래 → 7~12초 춤 → 12~14초 웃음 → 14~15초 광고"}
    )
    _, sent = _write(bad, _answer(True))
    assert len(sent) == 2 and "시간 구간이 5개인데 장면은 4개" in sent[1]


SAID = "지금 공유하고 만나요"


def _said_answer() -> Any:
    """지킨 답 + 대본에 금칙어(공유)가 든 대사 + 그 대사를 quote로 적은 지시."""
    rules = [*RULES, {"kind": "quote", "text_ko": SAID}]
    return _answer(True, rules=rules).model_copy(update={"script": f"노래하고 춤춘다. {SAID}"})


def test_quote_from_the_request_is_kept_and_skips_the_marketing_word_check() -> None:
    """11/26 예진 님 리뷰: 의뢰문에 실제로 있는 대사만 quote로 받는다 (있으면 지금처럼)."""
    plan, sent = _write(_said_answer(), request=f"{REQUEST} · 첫 대사는 “{SAID}.”")
    assert len(sent) == 1 and plan.written_by == "llm"
    said = [r for r in plan.request_rules if r.kind == "quote" and r.text_ko == SAID]
    assert len(said) == 1 and said[0].kept and said[0].where == "대본"


def test_quote_not_in_the_request_is_dropped_and_the_marketing_word_rejects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LLM이 의뢰문에 없는 문장을 quote로 적으면 버린다 → 금칙어 검사에 그대로 걸린다."""
    caplog.set_level("INFO", logger=vp.log.name)
    got = _said_answer()
    rules = vp._rules(V._prop(request_text=REQUEST), got, note=True)  # noqa: SLF001
    assert [r["text_ko"] for r in rules if r["kind"] == "quote"] == [QUOTE]
    assert "quote_not_in_request 1건" in caplog.text and SAID not in caplog.text
    plan, _ = _write(got)
    assert plan.written_by == "rule" and plan.fallback_reason == "rejected"


def test_real_quotes_compares_without_spaces_and_punctuation() -> None:
    rules = [{"kind": "quote", "text_ko": "지금, 캣북스에서 만나요"},
             {"kind": "quote", "text_ko": "지어낸 문장"}, {"kind": "prop", "text_ko": "마이크"}]
    kept, dropped = pc.real_quotes(rules, REQUEST)
    assert dropped == 1 and [r["text_ko"] for r in kept] == ["지금, 캣북스에서 만나요", "마이크"]
    assert pc.real_quotes(rules, "")[1] == 2  # 의뢰문이 비면 quote는 모두 버린다


def test_marketing_words_in_the_quote_do_not_reject_the_plan() -> None:
    said = "지금 공유하고 만나요"
    rules = [{"kind": "quote", "text_ko": said}]
    assert vp.text_problems(f"대본 {said}") and not vp.text_problems(
        pc.strip_quotes(f"대본 {said}", rules)
    )

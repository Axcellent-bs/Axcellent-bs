"""고칠 수 있는 거절은 규칙 초안 대신 다시 쓰기 1회 (11/26 B).

운영 wr_bc24dee53e·제안 40: 첫 답이 금지 규칙(prompt_rule 등)에 걸리면 바로 규칙 초안이었다. 제작
검사처럼 같은 1회 다시 쓰기에 문제 줄(코드·짧은 한국어, 고객 글·이름 없음)을 넣는다. 장면 수·지어낸
혜택 숫자는 지금처럼 바로 규칙 초안. LLM 호출은 최대 2번 그대로. 가짜 LLM만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_fixable_reject", "test_video_plan.py")
C = _load("company_character_helpers_fixable_reject", "test_plan_company_character.py")
F = _load("feasibility_helpers_fixable_reject", "test_plan_feasibility.py")
BRAND = V._llm_out(prompt="A Starbucks cup on the desk")  # noqa: SLF001
PERSON = V._llm_out(prompt="A celebrity lookalike cat")  # noqa: SLF001
GOOD = V._llm_out()  # noqa: SLF001


def _write(*answers: Any) -> tuple[Any, list[str]]:
    llm = C.SeqLLM(plans=list(answers))
    plan = asyncio.run(vp.write(llm, V._prop(), "ctx"))  # noqa: SLF001
    return plan, V.plan_calls(llm)


def test_prompt_rule_is_rewritten_once_and_the_ai_plan_is_kept() -> None:
    plan, sent = _write(BRAND, GOOD)
    assert len(sent) == 2
    assert plan.written_by == "llm" and plan.fallback_reason == "" and plan.fallback_detail == ""
    assert "금지 규칙(real_brand)" in sent[1] and "실제 브랜드 이름을 뺀다" in sent[1]
    assert "Starbucks" not in sent[1]  # 첫 답 글은 붙이지 않는다 (코드·짧은 한국어만)
    first, second = plan.check_log
    assert not first.passed and second.passed and second.attempt == 2
    rows = [(x.kind, x.scene, x.code) for x in first.problems]
    assert rows[0] == ("text", 1, "prompt_rule:real_brand") and len(rows) == len(BRAND.scenes)
    assert first.problems[0].detail_ko == "금지 규칙 prompt_rule:real_brand"


def test_still_rejected_after_the_rewrite_falls_back_with_the_second_code() -> None:
    plan, sent = _write(BRAND, PERSON)
    assert len(sent) == 2
    assert plan.written_by == "rule" and plan.fallback_reason == "rejected"
    assert plan.fallback_detail == "prompt_rule:real_person"  # 두 번째 답의 코드


@pytest.mark.parametrize(
    ("bad", "fix"),
    [
        (vp._PlanOut.model_validate(GOOD.model_dump() | {"concept": "업계 최고 서점"}),  # noqa: SLF001
         "광고 금칙어"),
    ],
)  # fmt: skip
def test_other_fixable_rejects_rewrite_too(bad: Any, fix: str) -> None:
    plan, sent = _write(bad, GOOD)
    assert len(sent) == 2 and fix in sent[1] and plan.written_by == "llm"
    assert [x.code for x in plan.check_log[0].problems] == ["marketing_word"]
    assert plan.check_log[0].problems[0].scene is None


def test_no_third_call_when_the_rewrite_was_for_feasibility() -> None:
    trio = F._answer(F.TRIO)  # noqa: SLF001 - 1차는 쓸 수 있지만 인물 3명 (제작 검사 다시 쓰기)
    bad = F._answer([F._scene(i, prompt="A Starbucks cup") for i in range(1, 5)])  # noqa: SLF001
    plan, sent = _write(trio, bad, GOOD)
    assert len(sent) == 2  # 다시 쓰기는 이미 썼다 → 거절이어도 더 부르지 않는다
    assert plan.written_by == "rule" and plan.fallback_detail == "prompt_rule:real_brand"


def test_scene_count_still_falls_back_at_once() -> None:
    plan, sent = _write(V._llm_out(n=7), GOOD)  # noqa: SLF001
    assert len(sent) == 1
    assert plan.written_by == "rule" and plan.fallback_detail == "scene_count"


def test_reject_code_fits_the_contract_code_field() -> None:
    assert vp.reject_code("prompt_rule:text_request") == "prompt_rule:text_request"
    assert vp.reject_code("prompt_rule:url_or_number") == "prompt_rule"  # 24자를 넘으면 앞 코드
    assert vp.reject_code("concept_starts_with_product") == "concept_starts_with_prod"
    assert all(len(vp.reject_code(c)) <= vp.CODE_MAX for c in vp.FIXABLE_REJECTS)

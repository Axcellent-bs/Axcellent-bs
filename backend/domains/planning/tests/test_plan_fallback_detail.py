"""규칙 초안으로 떨어진 진짜 이유를 화면에 (11/26 U).

운영 wr_8244142e32(제안 40)는 "AI 답이 금지 규칙에 걸려 규칙으로 썼어요"만 보였고, 어느 규칙인지는
서버 로그(log.info라 운영에 안 남음)에만 있었다. 이유 코드를 계약에 남기고 로그는 warning으로.
값·이름·프롬프트는 남기지 않는다. 가짜 LLM·가짜 마케팅만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.planning import VideoPlan
from ax_workflow.models import CompanyCharacter
from planning_agent import proposals as ext
from planning_agent import video_plan as vp
from planning_agent.domain import proposal_actions as pa

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_fallback_detail", "test_video_plan.py")
C = _load("company_character_helpers_fallback_detail", "test_plan_company_character.py")
NAME = "별빛소년단"
FACTS = [{"label": f"주제 '팬덤' 후보 {NAME} (언급 3회)", "value": "3", "source": "trend"}]


def _write(out: Any, **prop: Any) -> Any:
    return asyncio.run(vp.write(V._mock(out), V._prop(**prop), "ctx"))  # noqa: SLF001


def _out(**kw: Any) -> Any:
    """가짜 LLM 답 (장면 4개). visual: 첫 장면 visual, 그 밖은 _PlanOut 칸."""
    raw = V._llm_out().model_dump()  # noqa: SLF001
    if "visual" in kw:
        raw["scenes"][0]["visual"] = kw.pop("visual")
    return vp._PlanOut.model_validate(raw | kw)  # noqa: SLF001


@pytest.mark.parametrize(
    ("out", "prop", "detail"),
    [
        (V._llm_out(n=7), {}, "scene_count"),  # noqa: SLF001
        (V._llm_out(prompt="A Starbucks cup on the desk"), {}, "prompt_rule:real_brand"),  # noqa: SLF001
        (V._llm_out(prompt="A celebrity lookalike cat"), {}, "prompt_rule:real_person"),  # noqa: SLF001
        (_out(visual=f"{NAME} 응원봉을 든 고양이"), {"facts": FACTS}, "real_name_in_scene"),
        (_out(concept="업계 최고 서점"), {}, "marketing_word"),
    ],
)  # fmt: skip
def test_reject_code_goes_to_the_plan_and_a_warning_without_values(
    out: Any, prop: dict[str, Any], detail: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ax.planning"):
        plan = _write(out, **prop)
    assert plan.written_by == "rule" and plan.fallback_reason == "rejected"
    assert plan.fallback_detail == detail
    rows = [r for r in caplog.records if "LLM 영상 제작안 거절" in r.getMessage()]
    assert len(rows) == 1 and rows[0].levelno == logging.WARNING  # 운영(INFO 안 남김)에도 남는다
    text = rows[0].getMessage()
    assert detail in text and "PROP-" in text  # 제안 번호·코드만
    assert all(v not in caplog.text for v in ("Starbucks", NAME, "최고", "celebrity"))


def test_llm_plan_and_other_fallbacks_have_no_detail() -> None:
    assert _write(V._llm_out()).fallback_detail == ""  # noqa: SLF001 - LLM 제작안
    assert asyncio.run(vp.write(None, V._prop(), "ctx")).fallback_detail == ""  # noqa: SLF001 - no_llm
    assert VideoPlan.model_fields["fallback_detail"].default == ""  # 예전 제작안


def test_detail_fields_accept_codes_only() -> None:
    base = vp.rule_plan(V._prop()).model_dump(mode="json")  # noqa: SLF001
    for bad in ({"fallback_detail": "prompt_rule: Starbucks"}, {"svg_detail": "Key Error!"},
                {"character_source_reason": "read failed"}):  # fmt: skip
        with pytest.raises(ValueError):
            VideoPlan.model_validate(base | bad)


# ---------------------------------------------------------------- 회사 캐릭터 못 읽음


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("failed not_found", "read_failed"),
        (TimeoutError(), "read_failed"),
        ("input-required held_for_review", "gate_held"),
        (ext.NO_LOOK, "no_look"),
    ],
)
def test_character_not_read_leaves_a_reason_code_and_a_warning(
    answer: Any, why: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ax.planning"):
        _, out = C._receive(C.Marketing(answer))  # noqa: SLF001
    assert out.video_plan.character_source == "brand"
    assert out.video_plan.character_source_reason == why
    rows = [r for r in caplog.records if "회사 캐릭터 못 읽음" in r.getMessage()]
    assert len(rows) == 1 and rows[0].levelno == logging.WARNING
    # 11/26 H 바뀐 기대값: 코드 + 이유 글(마케팅 호출 결과, 120자)을 같이 남긴다 — 운영 PROP-42에서
    # read_failed만 남아 원인(FORBIDDEN_SKILL)을 몰랐다. 예전: 코드만("not_found" 없음)
    msg = rows[0].getMessage()
    detail = type(answer).__name__ if isinstance(answer, Exception) else str(answer)
    assert why in msg and f"({detail})" in msg
    assert C.RECORD not in msg  # 기록 번호는 남기지 않는다


def test_character_read_or_not_chosen_has_no_failure_reason() -> None:
    _, out = C._receive(C.Marketing(C.VIRTUAL))  # noqa: SLF001
    assert out.video_plan.character_source == "company_character"
    assert out.video_plan.character_source_reason == ""
    _, none = C._receive(C.Marketing(C.VIRTUAL), record=None)  # noqa: SLF001
    assert none.video_plan.character_source_reason == "no_record_id"  # 고르지 않음 (화면은 안 보임)


def test_character_reason_codes() -> None:
    assert pa.character_reason("input-required held") == "gate_held"
    assert pa.character_reason(ext.NO_LOOK) == "no_look"
    assert pa.character_reason("호출 실패 (ConnectError)") == "read_failed"
    assert pa.character_reason(None) == "read_failed"
    assert isinstance(C.VIRTUAL, CompanyCharacter)

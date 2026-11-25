"""영상 제작안이 규칙 초안으로 떨어지는 이유 (11/25 PROP-37).

- 제한 시간 150초(의뢰 흐름), 사람이 기다리는 곳은 호출하는 곳에서 40초
- 규칙 제작안이면 `fallback_reason`: no_llm·timeout·bad_format·rejected·error, LLM 제작안이면 ""
- 실패 로그는 예외 종류·경과 초만 (값·프롬프트 없음)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from ax_agent_template.llm import LLMFormatError, LLMResponseError, MockLLMClient
from ax_contracts.schemas.planning import VideoPlan
from ax_workflow.models import Proposal
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _prop() -> Proposal:
    return Proposal.model_validate({
        "id": "PROP-1", "title": "가을 밤 10분 독서 숏츠", "from": "data", "skill": "trend_history",
        "insight": "독서 검색이 11월에 오른다", "idea": "고양이 점원이 밤 10분 독서를 권하는 숏츠",
        "why": "독서 검색이 평균의 1.6배", "products": ["밤의 도서관"], "format": "숏폼 30초",
        "content_category": "브이로그", "angle": "with_product",
        "created_at": datetime(2026, 11, 19, tzinfo=UTC),
    })  # fmt: skip


def _llm_out(n: int = 4, prompt: str = "The mascot reads under a warm lamp, slow dolly in"):
    scenes = [
        {"visual": f"장면 {i} 화면", "caption": f"자막 {i}", "prompt": prompt} for i in range(n)
    ]
    return vp._PlanOut.model_validate(
        {"concept": "밤 10분 습관", "script": "훅 → 공감 → 상품 → 마무리", "scenes": scenes}
    )


def _mock(out: vp._PlanOut) -> MockLLMClient:
    return MockLLMClient(responses={vp._PlanOut: out})


class SlowLLM:
    """제작안 답을 `delay`초 뒤에 준다. 콘티(SVG) 호출은 실패시킨다 (제작안만 본다)."""

    def __init__(self, delay: float, fail: BaseException | None = None) -> None:
        self.delay, self.fail = delay, fail
        self.calls: list[str] = []

    async def structured(self, prompt: str, schema: type[BaseModel], **_: Any) -> Any:
        self.calls.append(prompt)
        if schema is not vp._PlanOut:
            raise LLMResponseError("콘티는 이 시험에서 안 본다")
        await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return _llm_out()


def _write(llm: Any, timeout: float = vp.TIMEOUT_SECONDS) -> VideoPlan:
    return asyncio.run(vp.write(llm, _prop(), "ctx", timeout=timeout))


def test_timeouts() -> None:
    assert vp.TIMEOUT_SECONDS == 150.0 and vp.HUMAN_WAIT_SECONDS == 40.0


def test_slow_llm_inside_the_limit_is_the_llm_plan() -> None:
    plan = _write(SlowLLM(0.05), timeout=1.0)
    assert plan.written_by == "llm" and plan.fallback_reason == ""


def test_slow_llm_over_the_limit_is_rule_with_timeout(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.INFO, logger="ax.planning"):
        plan = _write(SlowLLM(0.5), timeout=0.05)
    assert plan.written_by == "rule" and plan.fallback_reason == "timeout"
    text = caplog.text
    assert "LLM 영상 제작안 실패 → 규칙 제작안: TimeoutError" in text
    for value in ("가을 밤", "독서", "밤의 도서관", "고양이"):  # 제안 값·프롬프트가 없다
        assert value not in text


def test_no_llm_is_no_llm() -> None:
    plan = _write(None)
    assert plan.written_by == "rule" and plan.fallback_reason == "no_llm"


@pytest.mark.parametrize("exc", [LLMFormatError("형식"), ValueError("json")])
def test_bad_answer_shape_is_bad_format(exc: BaseException) -> None:
    plan = _write(SlowLLM(0, fail=exc))
    assert plan.fallback_reason == "bad_format"


def test_other_failure_is_error(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.INFO, logger="ax.planning"):
        plan = _write(SlowLLM(0, fail=LLMResponseError("LLM API 호출 실패: ConnectError")))
    assert plan.fallback_reason == "error"
    assert "LLMResponseError" in caplog.text and "ConnectError" not in caplog.text
    plan = _write(MockLLMClient())  # 응답이 등록되지 않음 → KeyError
    assert plan.fallback_reason == "error"


def test_forbidden_word_is_rejected(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.INFO, logger="ax.planning"):
        plan = asyncio.run(vp.write(_mock(_llm_out(prompt="A Starbucks cup")), _prop(), "ctx"))
    assert plan.written_by == "rule" and plan.fallback_reason == "rejected"
    assert "LLM 영상 제작안 거절 → 규칙 제작안: prompt_rule" in caplog.text
    assert "Starbucks" not in caplog.text


def test_too_many_scenes_is_rejected() -> None:
    plan = asyncio.run(vp.write(_mock(_llm_out(n=7)), _prop(), "ctx"))
    assert plan.fallback_reason == "rejected"


def test_rule_plan_itself_and_old_plans_have_no_reason() -> None:
    assert vp.rule_plan(_prop()).fallback_reason == ""
    old = vp.rule_plan(_prop()).model_dump(mode="json")
    old.pop("fallback_reason")
    assert VideoPlan.model_validate(old).fallback_reason == ""  # 예전 제작안 읽기

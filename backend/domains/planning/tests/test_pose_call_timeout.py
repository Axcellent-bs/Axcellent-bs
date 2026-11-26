"""콘티 포즈 호출이 60초에 끊기지 않게 (11/26 C).

운영 09:04 `마네킹 콘티 포즈 실패: LLMResponseError 60.0s 0/4장면`: storyboard_svg.draw는 150초를
기다리는데 LLMClient의 httpx 타임아웃이 기본 60초라 ReadTimeout → LLMResponseError로 끝났다.
실제로 기다리지 않고 타임아웃 값만 본다. 실제 LLM은 부르지 않는다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import LLMClient, LLMResponseError, LLMSettings, MockLLMClient
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_pose_timeout", "test_video_plan.py")
BASE = LLMSettings(model="plan-model", base_url="https://llm.example/v1", api_key="k-test")


def test_pose_client_waits_as_long_as_the_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    monkeypatch.delenv("PLANNING_LLM_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("LLM_TIMEOUT_SEC", raising=False)
    plan_llm = LLMClient("planning", settings=BASE)
    assert plan_llm.timeout == 60.0  # 제작안 본 호출은 그대로 (기본 60초)
    pose = sb.pose_llm(plan_llm)
    assert pose is not plan_llm and pose.timeout == sb.TIMEOUT_SECONDS
    assert (pose.dept, pose.settings) == ("planning", BASE)  # 같은 기획 설정
    assert plan_llm.timeout == 60.0  # 본 클라이언트는 바꾸지 않는다
    monkeypatch.setattr(sb, "settings_for", lambda dept: BASE)
    monkeypatch.setenv(sb.MODEL_ENV, "pose-model")
    other = sb.pose_llm(plan_llm)
    assert other.timeout == sb.TIMEOUT_SECONDS and other.settings.model == "pose-model"


def test_fake_llm_is_used_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    fake = MockLLMClient(responses={})
    assert sb.pose_llm(fake) is fake and sb.pose_llm(None) is None


@pytest.mark.parametrize(
    ("exc", "detail"),
    [
        (LLMResponseError("LLM API 호출 실패: ReadTimeout"), "LLMResponseError_ReadTimeout"),
        (LLMResponseError("LLM API가 429를 돌려줬다", status_code=429), "LLMResponseError_429"),
        (LLMResponseError("이상한 글 ! 고객 이름"), "LLMResponseError"),
        (TimeoutError(), "TimeoutError"),
    ],
)
def test_svg_detail_says_why_the_call_failed(exc: BaseException, detail: str) -> None:
    assert sb.failure_detail(exc) == detail

    class Broken(MockLLMClient):
        async def structured(self, *a: object, **k: object) -> object:
            raise exc

    plan = vp.rule_plan(V._prop())  # noqa: SLF001
    out, n = asyncio.run(sb.draw(Broken(responses={}), plan, "ctx"))
    assert n == 0 and out.svg_detail == detail

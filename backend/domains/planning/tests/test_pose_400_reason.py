"""콘티 포즈 호출 400의 이유를 남긴다 (11/26 F).

운영 wr_eabc06b19e(누비투어): "콘티 호출이 실패했어요 (LLMResponseError_400)". 공급자 메시지는
예외에만 있고 로그·화면에 없어 원인을 몰랐다. 가짜 LLM 서버(httpx.MockTransport)만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template.llm import LLMClient, LLMSettings
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


V = _load("video_plan_helpers_pose_400", "test_video_plan.py")
LONG = " ".join(f"word{i}" for i in range(60))  # 긴 토큰 모양은 래퍼가 가리니 낱말로


def _client(message: str) -> LLMClient:
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": message}})

    settings = LLMSettings(model="m", base_url="https://llm.example/v1", api_key="k-test")
    return LLMClient("planning", settings=settings, transport=httpx.MockTransport(reply))


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        ("output_format.schema: For 'object' type, 'additionalProperties' must be false",
         "LLMResponseError_400_schema"),
        ("max_tokens: 64000 > 32000, which is the maximum allowed", "LLMResponseError_400_length"),
        ("model: unknown model name", "LLMResponseError_400_model"),
        ("something else went wrong", "LLMResponseError_400"),
    ],
)  # fmt: skip
def test_400_reason_goes_to_svg_detail_and_the_log(
    message: str, detail: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    plan = vp.rule_plan(V._prop())  # noqa: SLF001
    with caplog.at_level(logging.WARNING):
        out, n = asyncio.run(sb.draw(_client(message), plan, "ctx", note="고객 의뢰 메모"))
    # 11/26 실패 사다리: 400이면 2단(장면별)도 실패 → 4단 규칙 포즈로 다 그린다. 갈래 코드는 로그에
    # (예전 기대값: n 0·svg_reason "error"·svg_detail = 갈래 코드)
    k = len(plan.scenes)
    assert n == k and out.svg_reason == "" and out.svg_detail == f"rule_pose_{k}"
    assert len(detail) <= 40
    rows = [r.getMessage() for r in caplog.records if "마네킹 콘티 포즈 실패" in r.getMessage()]
    assert "1단 전체" in rows[0] and "2단 장면별" in rows[1]
    assert all(detail in r and f"공급자 메시지: {message}" in r for r in rows)
    assert sb.PROMPT[:40] not in caplog.text and "고객 의뢰 메모" not in caplog.text  # 요청 없음


def test_provider_message_is_cut_to_120(caplog: pytest.LogCaptureFixture,
                                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    with caplog.at_level(logging.WARNING):
        asyncio.run(sb.draw(_client(LONG), vp.rule_plan(V._prop()), "ctx"))  # noqa: SLF001
    row = next(r.getMessage() for r in caplog.records if "마네킹 콘티" in r.getMessage())
    assert LONG[: sb.PROVIDER_LOG_MAX] in row and LONG[: sb.PROVIDER_LOG_MAX + 1] not in row


def test_kinds_ignore_case() -> None:
    assert sb.bad_request_kind("Invalid JSON_SCHEMA") == "schema"
    assert sb.bad_request_kind("Prompt is TOO LONG") == "length"
    assert sb.bad_request_kind("") == ""

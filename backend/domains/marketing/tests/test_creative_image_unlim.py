"""의뢰 영상의 그림은 Higgsfield 무제한(unlim)으로만 만든다 (11/24).

- 그림 params `use_unlim=true`, 영상은 그대로 false(크레딧, 사람 승인 뒤)
- 무제한이 안 되는 응답(unlim_choice·크레딧이 필요하다는 tool_error·credits_insufficient)이면
  크레딧으로 다시 시도하지 않고 `image_unlim_unavailable`로 멈춘다(기록·로그에 사유)
- 미리 보기도 무제한 기준: 예상 0 크레딧·잔액 부족을 크레딧 부족으로 보지 않는다

가짜 Higgsfield(MCP)·가짜 안전 관문만 쓴다(실제로 부르지 않는다).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from marketing_agent import creative_mcp as cm
from marketing_agent.higgsfield_mcp import McpError


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("creative_render_mcp_helpers_unlim", "test_creative_render_mcp.py")
env = M.env  # 캣북스만 회사 계정 연결


def _generated(env: dict[str, Any]) -> list[dict[str, Any]]:
    return [args["requests"][0]["params"] for name, args in env["hf"].tools
            if name == "generate_image_batch"]  # fmt: skip


def test_image_params_use_unlim_and_video_does_not() -> None:
    assert cm.image_unlim() is True
    assert cm.image_params("m", "p", "", "16:9")["use_unlim"] is True
    assert cm.image_params("m", "p", "", "16:9", unlim=False)["use_unlim"] is False  # 썸네일
    assert cm.video_params("m", "p", "9:16", 5)["use_unlim"] is False


def test_sheet_is_made_with_unlim_even_when_credits_are_short(env: dict[str, Any]) -> None:
    env["hf"].credits = 0.0  # 잔액이 없어도 무제한이면 만든다
    env["hf"].cost = 0.0  # 무제한 예상 0 크레딧
    rid = M._sheet(env)  # noqa: SLF001
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "done" and part["route"] == "mcp"
    estimate = dict(env["hf"].tools)["estimate_image_cost"]["params"]
    assert estimate["use_unlim"] is True  # 미리 보기도 무제한 기준
    assert [p["use_unlim"] for p in _generated(env)] == [True]


@pytest.mark.parametrize("answer", [
    {"unlim_choice": {"question": "use unlimited?"}},
    {"error": "Not enough credits for this generation"},
])  # fmt: skip
def test_unlim_unavailable_stops_without_credit_retry(env: dict[str, Any],
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      caplog: pytest.LogCaptureFixture,
                                                      answer: dict[str, Any]) -> None:  # fmt: skip
    real = env["hf"]._tool  # noqa: SLF001

    def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "generate_image_batch":
            env["hf"].tools.append((name, args))
            return answer
        return real(name, args)

    monkeypatch.setattr(env["hf"], "_tool", tool)
    caplog.set_level(logging.WARNING, logger="marketing.creative_render")
    rid = M._sheet(env)  # noqa: SLF001
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "blocked" and part["error"] == cm.UNLIM_UNAVAILABLE
    # 크레딧(use_unlim=false)으로 다시 시도하지 않았다
    assert [p["use_unlim"] for p in _generated(env)] == [True]
    assert "jobs_wait" not in env["hf"].names()
    line = next(r.getMessage() for r in caplog.records if "실패" in r.getMessage())
    assert f"reason={cm.UNLIM_UNAVAILABLE}" in line and "tool=generate_image_batch" in line


def test_preflight_zero_credit_passes_and_unlim_errors_are_mapped() -> None:
    class Hf:
        def __init__(self, estimate: dict[str, Any] | Exception, credits: float) -> None:
            self.estimate = estimate
            self.credits = credits

        async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if name == "balance":
                return {"credits": self.credits}
            if isinstance(self.estimate, Exception):
                raise self.estimate
            return self.estimate

    import asyncio

    unlim = cm.image_params("m", "p", "", "16:9")
    zero = {"cost": {"credits_exact": 0}}
    assert asyncio.run(cm.preflight(Hf(zero, 0.0), "image", unlim)) == 0.0  # type: ignore[arg-type]
    # 무제한이면 예상값이 있어도 잔액으로 막지 않는다
    two = {"cost": {"credits_exact": 2}}
    assert asyncio.run(cm.preflight(Hf(two, 0.0), "image", unlim)) == 2.0  # type: ignore[arg-type]
    # 크레딧 params(영상)는 지금처럼 막는다
    video = cm.video_params("m", "p", "9:16", 5)
    with pytest.raises(McpError, match="credits_insufficient"):
        asyncio.run(cm.preflight(Hf(two, 0.0), "video", video))  # type: ignore[arg-type]
    # 미리 보기에서 무제한이 안 된다는 답 → image_unlim_unavailable
    with pytest.raises(McpError) as got:
        asyncio.run(cm.preflight(Hf(McpError("unlim_choice", tool="estimate_image_cost"), 9.0),
                                 "image", unlim))  # type: ignore[arg-type]
    assert got.value.code == cm.UNLIM_UNAVAILABLE and got.value.error_class == "unlim_choice"


def test_unlim_unavailable_mapping_is_narrow() -> None:
    assert cm.unlim_unavailable(McpError("credits_insufficient")).code == cm.UNLIM_UNAVAILABLE  # type: ignore[union-attr]
    said = McpError("tool_error", detail="Upgrade your plan to use unlimited")
    assert cm.unlim_unavailable(said).code == cm.UNLIM_UNAVAILABLE  # type: ignore[union-attr]
    assert cm.unlim_unavailable(McpError("tool_error", detail="prompt blocked")) is None
    assert cm.unlim_unavailable(McpError("network")) is None

"""의뢰 영상 그림을 크레딧으로 만든다 (11/24, Higgsfield MCP에서 이 계정은 무제한 불가 확인).

- 기본 `IMAGE_USE_UNLIM=False`: 그림(시트·스토리보드·썸네일) params `use_unlim=false`, 영상도 false
- 크레딧이면 #1697 이전 규칙: 잔액으로 미리 막고(`credits_insufficient`, 관문 전), billing은
  company_subscription·"구독 크레딧"
- 무제한 설정일 때 Higgsfield의 무제한 거절 문장("Unlimited generations aren't available …")은
  `image_unlim_unavailable`(정리한 문장은 detail), 크레딧으로 자동 재시도하지 않는다
- 그림 모델은 서버 설정(MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL, 예: gpt_image_2_6).
  모르는 칸(quality 등)을 더하지 않는다

가짜 Higgsfield(MCP)·가짜 안전 관문만 쓴다(실제로 부르지 않는다).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from marketing_agent import creative_mcp as cm
from marketing_agent import creative_render as rr
from marketing_agent.higgsfield_mcp import McpError, tool_output

REFUSED = ("Unlimited generations aren't available on this account. "
           "Retry without use_unlim to generate with credits.")  # fmt: skip


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("creative_render_mcp_helpers_credits", "test_creative_render_mcp.py")
env = M.env


def _calls(env: dict[str, Any], tool: str) -> list[dict[str, Any]]:
    return [args for name, args in env["hf"].tools if name == tool]


def test_default_is_credits_for_images_and_video() -> None:
    assert cm.image_unlim() is False
    assert cm.image_params("m", "p", "", "16:9")["use_unlim"] is False
    assert cm.video_params("m", "p", "9:16", 5)["use_unlim"] is False
    assert rr.image_billing(rr.MCP) == "company_subscription"


def test_sheet_with_credits_records_subscription(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL", "gpt_image_2_6")  # 서버 설정 값
    rid = M._sheet(env)  # noqa: SLF001
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "done" and part["route"] == "mcp"
    sent = _calls(env, "generate_image_batch")[0]["requests"][0]["params"]
    assert sent["model"] == "gpt_image_2_6" and sent["use_unlim"] is False
    # 모르는 칸(quality 등)을 더하지 않는다: 지금 보내는 칸만
    assert set(sent) == {"model", "prompt", "aspect_ratio", "use_unlim", "medias"}
    assert sent["medias"][0]["role"] == "image_references"
    _action, gate = env["gate"].calls[0]
    assert gate["billing"] == "company_subscription" and "구독 크레딧" in gate["summary"]


def test_short_credits_stop_before_the_gate_by_default(env: dict[str, Any]) -> None:
    env["hf"].credits = 1.0  # 예상 2 크레딧보다 적다
    rid = M._sheet(env)  # noqa: SLF001
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "blocked" and part["error"] == "credits_insufficient"
    assert env["gate"].calls == [] and _calls(env, "generate_image_batch") == []


def test_real_refusal_sentence_is_unlim_unavailable() -> None:
    for result in (
        {"isError": True, "content": [{"type": "text", "text": REFUSED}]},
        {"structuredContent": {"error": REFUSED}},
    ):
        with pytest.raises(McpError) as got:
            tool_output(result, "estimate_image_cost")
        mapped = cm.unlim_unavailable(got.value)
        assert mapped is not None and mapped.code == cm.UNLIM_UNAVAILABLE
        assert mapped.detail is not None and "Unlimited generations" in mapped.detail
    # 비슷한 다른 문장도 (rpc_error 포함)
    other = McpError("rpc_error", detail="unlimited mode is not available for this plan")
    assert cm.unlim_unavailable(other).code == cm.UNLIM_UNAVAILABLE  # type: ignore[union-attr]
    assert cm.unlim_unavailable(McpError("tool_error", detail="prompt was blocked")) is None


def test_refusal_under_unlim_setting_stops_without_credit_retry(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cm, "IMAGE_USE_UNLIM", True)  # 회사 설정이 무제한을 켰을 때
    real = env["hf"]._tool  # noqa: SLF001

    def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "estimate_image_cost":
            env["hf"].tools.append((name, args))
            return {"error": REFUSED}  # 미리 보기에서 무제한 거절 (11/24 운영과 같은 답)
        return real(name, args)

    monkeypatch.setattr(env["hf"], "_tool", tool)
    rid = M._sheet(env)  # noqa: SLF001
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "blocked" and part["error"] == cm.UNLIM_UNAVAILABLE
    # 크레딧(use_unlim=false)으로 다시 묻거나 만들지 않았다
    assert [a["params"]["use_unlim"] for a in _calls(env, "estimate_image_cost")] == [True]
    assert _calls(env, "generate_image_batch") == [] and env["gate"].calls == []

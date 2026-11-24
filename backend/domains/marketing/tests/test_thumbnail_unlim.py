"""제작 기록 썸네일(표지 후보)도 Higgsfield 무제한으로 + 그림 기록 문구 "무제한" (11/24).

- 썸네일 params·미리 보기 `use_unlim=true` (`creative_mcp.image_unlim()`을 따른다)
- 무제한이 안 되면 크레딧으로 다시 시도하지 않고 `image_unlim_unavailable`로 멈춘다
  (#1697과 같은 규칙)
- 그림 안전 기록 billing=unlim("Higgsfield 무제한"), 영상은 그대로 company_subscription

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


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


T = _load("creative_thumbnails_helpers_unlim", "test_creative_thumbnails.py")
env = T.env


@pytest.fixture(autouse=True)
def _unlim_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """11/24: 기본은 크레딧(IMAGE_USE_UNLIM=False).

    이 파일은 무제한 설정일 때 썸네일 규칙을 본다."""
    monkeypatch.setattr(cm, "IMAGE_USE_UNLIM", True)


def _batches(env: dict[str, Any]) -> list[dict[str, Any]]:
    return [a["requests"][0]["params"] for n, a in env["hf"].tools if n == "generate_image_batch"]


def test_thumbnails_use_unlim_and_ignore_balance(env: dict[str, Any]) -> None:
    T._with_video(env)  # noqa: SLF001
    env["hf"].credits = 0.0  # 잔액이 없어도 무제한이면 승인에 올리고 만든다
    done = T._approve_and_make(env)  # noqa: SLF001
    assert done["status"] == "done" and len(done["candidates"]) == 3
    estimate = dict(env["hf"].tools)["estimate_image_cost"]["params"]
    assert estimate["use_unlim"] is True  # 미리 보기도 무제한 기준
    assert [p["use_unlim"] for p in _batches(env)] == [True, True, True]
    _action, params, _key = env["gate"].calls[0]
    assert params["billing"] == "unlim" and "Higgsfield 무제한" in params["summary"]


def test_unlim_unavailable_stops_thumbnails_without_credit_retry(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = env["hf"]._tool  # noqa: SLF001

    def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "generate_image_batch":
            env["hf"].tools.append((name, args))
            return {"unlim_choice": {"question": "use unlimited?"}}
        return real(name, args)

    monkeypatch.setattr(env["hf"], "_tool", tool)
    T._with_video(env)  # noqa: SLF001
    state = T._approve_and_make(env)  # noqa: SLF001
    assert not state.get("candidates")  # 한 장도 만들지 않았다
    assert state["status"] == "blocked" and state["error"] == cm.UNLIM_UNAVAILABLE, state
    # 첫 장에서 멈추고, 크레딧(use_unlim=false)으로 다시 시도하지 않았다
    assert [p["use_unlim"] for p in _batches(env)] == [True]
    assert "jobs_wait" not in [n for n, _ in env["hf"].tools]


def test_image_billing_is_unlim_and_video_stays_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rr.image_billing(rr.MCP) == "unlim"
    assert rr._billing_ko(rr.MCP, "unlim") == "Higgsfield 무제한"  # noqa: SLF001
    assert rr._billing_ko(rr.MCP) == "Higgsfield 회사 계정 구독 크레딧"  # noqa: SLF001 - 영상
    assert rr.image_billing(rr.API) is None and rr._billing_ko(rr.API) == "Higgsfield"  # noqa: SLF001
    monkeypatch.setattr(cm, "IMAGE_USE_UNLIM", False)  # 나중에 회사 설정이 크레딧이면
    assert rr.image_billing(rr.MCP) == "company_subscription"

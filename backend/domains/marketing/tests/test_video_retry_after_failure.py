"""의뢰 영상 단계 다시 시도 (11/27): 승인 뒤 만들기가 실패했거나 관문이 막은 영상을 다시 요청하면
새 멱등 키(:retryN)로 안전 관문에 다시 묻는다. 같은 키면 관문이 예전 승인을 그대로 돌려줘 사람
승인 없이 다시 만든다. 콘티·스토리보드 그림은 다시 만들지 않는다.
가짜 관문·가짜 Higgsfield만 쓴다."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from marketing_agent import creative_render as rr

sys.path.insert(0, str(Path(__file__).parent))
from test_creative_render import (  # noqa: E402, F401 - env는 시험 도구(fixture)
    META,
    _board_done,
    _run,
    env,
)


def _video_keys(ctx: dict[str, Any]) -> list[str]:
    return [k for a, _, k in ctx["gate"].calls if a == "generate_creative_video"]


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_failed_video_asks_the_gate_again_with_a_new_key(env: dict[str, Any], status: str) -> None:  # noqa: F811
    rid = _board_done(env)
    store = env["store"]
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    render = store.get_render(rid)
    render["video"] = {**render["video"], "status": status, "error": "provider_error"}
    store.set_render(rid, render)
    board_before = render["board_image"]
    storyboard_before = (store.get(rid) or {}).get("storyboard")
    again = _run(lambda: rr.request_video(env["ledger"], META, rid, "jaehyeon"))
    assert again["status"] == "awaiting_approval" and again["attempt"] == 1
    first, second = _video_keys(env)[0], _video_keys(env)[-1]
    assert second == f"{first}:retry1"  # 새 키 → 사람 승인을 다시 받는다
    after = store.get_render(rid)
    assert after["board_image"] == board_before  # 스토리보드 그림 다시 안 그림
    assert (store.get(rid) or {}).get("storyboard") == storyboard_before


def test_awaiting_or_done_video_is_not_asked_again(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert len(_video_keys(env)) == 1  # 승인 대기 중이면 그대로 (지금과 같음)

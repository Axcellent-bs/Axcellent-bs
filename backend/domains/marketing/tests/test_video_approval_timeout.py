"""영상 승인 기한 (11/26): 기한 지나 취소된 영상을 같은 기록으로 다시 승인에 올린다.

가짜 Higgsfield·가짜 안전 관문만 쓴다. 기한 지남은 관문이 `rejected` + `review_timeout`으로 알린다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from marketing_agent import creative_render as rr

sys.path.insert(0, str(Path(__file__).parent))
from test_creative_render import (  # noqa: E402, F401 - env는 시험 도구(fixture)
    META,
    _board_done,
    _run,
    env,
)


def _expire(ctx: dict[str, Any]) -> None:
    """관문이 그 보류 건을 기한 지남으로 닫은 것처럼: 거절 + review_timeout."""
    gate = ctx["gate"]

    async def expired(
        action: str, params: dict, meta: RequestMeta, evidence: Any
    ) -> ExecuteDecision:
        if action != "generate_creative_video" or meta.idempotency_key.endswith(":retry1"):
            return await type(gate).__call__(gate, action, params, meta, evidence)
        gate.calls.append((action, params, str(meta.idempotency_key)))
        return ExecuteDecision(
            execution_id="ex_expired",
            grade="C",
            status="rejected",
            hold_reason=HoldReason.REVIEW_TIMEOUT,
        )

    rr.execute = expired  # type: ignore[assignment]  # env가 monkeypatch로 되돌린다


def _video_keys(ctx: dict[str, Any]) -> list[str]:
    return [k for a, _, k in ctx["gate"].calls if a == "generate_creative_video"]


def test_held_video_records_when_it_was_held(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board_done(env)
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon"))
    assert video["status"] == "awaiting_approval" and video["held_at"]
    assert video["attempt"] == 0 and video["reason"] is None


def test_timed_out_video_says_so_and_human_rejection_does_not(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    _expire(env)
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    video = env["store"].get_render(rid)["video"]
    assert video["status"] == "rejected" and video["reason"] == "review_timeout"

    rr.execute = env["gate"]  # type: ignore[assignment]
    other = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, other, None))
    env["gate"].status["generate_creative_video"] = "rejected"  # 사람이 거절
    _run(lambda: rr.check_video(env["ledger"], META, other))
    assert env["store"].get_render(other)["video"]["reason"] == "rejected"


def test_retry_after_timeout_uses_a_new_key_and_reuses_the_record(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board_done(env)
    store = env["store"]
    render = store.get_render(rid)
    frame = {"index": 0, "status": "done", "media_id": "m0"}
    render["keyframes"] = {"status": "done", "frames": [frame]}
    store.set_render(rid, render)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    _expire(env)
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    images_before = [c for c in env["gate"].calls if c[0] == "generate_media"]
    board_before = store.get_render(rid)["board_image"]

    again = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon"))  # [다시 승인 요청]
    assert again["status"] == "awaiting_approval" and again["review_id"] == "rev_1"
    assert again["attempt"] == 1 and again["reason"] is None
    first, second = _video_keys(env)[0], _video_keys(env)[-1]
    assert second == f"{first}:retry1"  # 같은 키면 관문이 예전 거절을 그대로 돌려준다
    after = store.get_render(rid)
    assert after["keyframes"] == render["keyframes"]  # 키프레임을 다시 그리지 않는다
    assert after["board_image"] == board_before  # 콘티 그림도 그대로
    assert [c for c in env["gate"].calls if c[0] == "generate_media"] == images_before
    assert store.get(rid) is not None and after["video"]["key"].endswith(":retry1")


def test_held_video_is_not_requested_twice(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    again = _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert again["attempt"] == 0 and len(_video_keys(env)) == 1

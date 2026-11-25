"""영상 다시 만들기(11/26 H) 뒤 거절·기한 지남이면 예전 영상으로 되돌린다.

[영상 다시 만들기]는 다 만든 영상 칸(render.video)을 승인 대기로 바꾼다. 그 요청이 거절되거나
기한 지나 취소되면, 예전 영상이 다시 "내보낼 영상"(게시·썸네일)이어야 한다.
가짜 Higgsfield·가짜 안전 관문·가짜 ffmpeg만 쓴다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from marketing_agent import creative_qc as qc
from marketing_agent import creative_render as rr

sys.path.insert(0, str(Path(__file__).parent))
from test_creative_qc import FREEZE_ERR, FakeFfmpeg, _patch, _video_done  # noqa: E402
from test_creative_render import (  # noqa: E402, F401 - env는 시험 도구(fixture)
    META,
    _run,
    env,
)


def _held_regenerate(ctx: dict[str, Any], monkeypatch) -> str:  # noqa: ANN001
    """다 만든 영상 + 기술 검사 확인 필요 → [영상 다시 만들기]로 승인 대기."""
    _patch(monkeypatch, FakeFfmpeg(freeze=FREEZE_ERR))
    rid = _video_done(ctx)
    qc.inspect(rid, None)
    assert rr.publish_video_part(ctx["store"].get(rid)) == "video"
    redo = _run(lambda: rr.request_video(ctx["ledger"], META, rid, "gaon", regenerate=True))
    assert redo["status"] == "awaiting_approval" and redo["attempt"] == 1
    # 승인 대기 동안은 다 만든 영상이 아니다(지금과 같음)
    assert rr.publish_video_part(ctx["store"].get(rid)) is None
    return rid


def _assert_restored(ctx: dict[str, Any], rid: str, reason: str) -> dict[str, Any]:
    rec = ctx["store"].get(rid)
    video = rec["render"]["video"]
    assert video["status"] == "done" and video["sha256"] == "a" * 64
    assert video["asset"] == "video.mp4" and video["summary"] == {"credits_estimate": 27}
    assert video["regenerate"]["status"] == "rejected"
    assert video["regenerate"]["reason"] == reason
    assert "previous" not in video
    assert rr.publish_video_part(rec) == "video"  # 게시·썸네일이 예전 영상을 다시 쓴다
    assert (ctx["dir"] / "creative" / rid / "video.mp4").read_bytes() == b"video-bytes"
    return video


def test_human_rejection_restores_previous_video(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    rid = _held_regenerate(env, monkeypatch)
    env["gate"].status["generate_creative_video"] = "rejected"
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    video = _assert_restored(env, rid, "rejected")
    assert video["attempt"] == 1  # 거절된 시도 번호를 남긴다
    # 검사 결과는 같은 영상 그대로라 다시 만들기 버튼 조건도 그대로
    assert qc.has_warn(env["store"].get_render(rid))

    # 다시 누르면 또 새 키(:retry2)로 묻는다. 같은 키면 관문이 예전 거절을 돌려준다
    env["gate"].status["generate_creative_video"] = "held"
    again = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon", regenerate=True))
    assert again["status"] == "awaiting_approval"
    assert again["key"].endswith(":retry2") and again["regenerate"] is None


def test_review_timeout_restores_previous_video(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    rid = _held_regenerate(env, monkeypatch)
    gate = env["gate"]

    async def expired(
        action: str, params: dict, meta: RequestMeta, evidence: Any
    ) -> ExecuteDecision:
        gate.calls.append((action, params, str(meta.idempotency_key)))
        return ExecuteDecision(
            execution_id="ex_expired",
            grade="C",
            status="rejected",
            hold_reason=HoldReason.REVIEW_TIMEOUT,
        )

    monkeypatch.setattr(rr, "execute", expired)
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    _assert_restored(env, rid, "review_timeout")


def test_gate_block_on_regenerate_keeps_previous_video(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    _patch(monkeypatch, FakeFfmpeg(freeze=FREEZE_ERR))
    rid = _video_done(env)
    qc.inspect(rid, None)
    env["gate"].status["generate_creative_video"] = "rejected"  # 관문이 바로 막음
    got = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon", regenerate=True))
    assert got["status"] == "done"
    _assert_restored(env, rid, "blocked")


def test_plain_rejection_without_previous_is_unchanged(env: dict[str, Any]) -> None:  # noqa: F811
    from test_creative_render import _board_done

    rid = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    env["gate"].status["generate_creative_video"] = "rejected"
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    video = env["store"].get_render(rid)["video"]
    assert video["status"] == "rejected" and video["reason"] == "rejected"
    assert "regenerate" not in video or video["regenerate"] is None

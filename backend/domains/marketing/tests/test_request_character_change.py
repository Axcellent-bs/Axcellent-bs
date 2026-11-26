"""의뢰 캐릭터를 영상 요청 전까지 바꾸기 (11/26). 가짜 Higgsfield·가짜 관문.

- 총괄이 다시 준비하면서 예전 의뢰 기록(`replaces_creative_record_id`)을 넘기면 멈춤 표시를 한다
- 멈춘 기록으로는 영상을 요청하지 않고(`character_changed`), 남은 키프레임도 그리지 않는다
- 새 캐릭터의 의뢰 기록은 그림·키프레임이 없는 상태에서 다시 준비한다
  (그림 관문·크레딧 예약을 다시 거친다)
- 이미 영상을 요청한 기록은 멈춤 표시를 하지 않는다. 같은 캐릭터로 되돌아오면 멈춤 표시를 지운다
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import TaskStatus
from marketing_agent import creative_render as rr
from marketing_agent import keyframes, request_creative


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


B = _load("request_creative_helpers_change", "test_request_creative.py")
env = B.env  # 시험 도구(fixture)
PLAN = B.PLAN


def _prepare_skill(brand: str, replaces: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "proposal_id": "PROP-1",
        "video_plan": PLAN.model_dump(),
        "brand_character_record_id": brand,
    }
    if replaces:
        body["replaces_creative_record_id"] = replaces
    return B._skill("prepare_video_draft_from_plan", body).output  # noqa: SLF001


def _second_brand(env: dict[str, Any]) -> str:  # noqa: F811
    return B._brand(env, note="두 번째 캐릭터")  # noqa: SLF001


def test_reprepare_stops_the_old_record_and_starts_a_fresh_one(env: dict[str, Any]) -> None:  # noqa: F811
    first, second = B._brand(env), _second_brand(env)  # noqa: SLF001
    old = _prepare_skill(first)["creative_record_id"]
    out = _prepare_skill(second, replaces=old)
    new = out["creative_record_id"]
    assert new and new != old
    assert out["generate_media_input"][request_creative.INPUT_KEY] == new
    assert rr.superseded(old) and not rr.superseded(new)
    fresh = env["store"].get(new)["render"]
    assert "keyframes" not in fresh  # 키프레임은 처음부터
    # 11/26 C: 영상 초안 준비가 스토리보드 그림 1장을 관문을 거쳐 그린다 (예전·새 기록 한 장씩)
    keys = [k for _a, _p, k in env["gate"].calls]
    assert keys == [f"{B.CTX}:creative_render:{old}:board_image",
                    f"{B.CTX}:creative_render:{new}:board_image"]  # fmt: skip
    assert fresh["board_image"]["status"] == "done" and len(env["hf"].submits) == 2
    env["gate"].calls.clear()
    env["hf"].submits.clear()

    stopped = B._make(env, old)  # noqa: SLF001 - 예전 제작 단계가 늦게 돌아도
    assert stopped.status == TaskStatus.FAILED and stopped.outcome == rr.CHARACTER_CHANGED
    assert stopped.output["retryable"] is False
    assert env["gate"].calls == [] and env["hf"].submits == []  # 그림도 영상도 요청하지 않는다
    with pytest.raises(rr.RenderError, match=rr.CHARACTER_CHANGED):
        B._run(lambda: rr.request_video(env["ledger"], B._meta(), old, "사람"))  # noqa: SLF001

    held = B._make(env, new)  # noqa: SLF001 - 새 기록은 준비 때 그린 그림을 그대로 쓴다
    assert held.outcome == request_creative.VIDEO_HELD
    actions = [a for a, _p, _k in env["gate"].calls]
    assert actions == ["generate_creative_video"]  # 같은 이야기라 그림을 다시 그리지 않는다
    # 제작 단계는 그림 크레딧을 새로 예약하지 않는다 (준비 때 한 장씩은 서비스 장부에, 위 submits 2)
    assert env["ledger"].month_usage(B._month(), "image") == 0  # noqa: SLF001
    assert env["hf"].submits == []  # 그림을 다시 그리지 않고, 영상은 승인 대기


def test_switching_back_reuses_the_first_record(env: dict[str, Any]) -> None:  # noqa: F811
    first, second = B._brand(env), _second_brand(env)  # noqa: SLF001
    old = _prepare_skill(first)["creative_record_id"]
    new = _prepare_skill(second, replaces=old)["creative_record_id"]
    back = _prepare_skill(first, replaces=new)["creative_record_id"]
    assert back == old and not rr.superseded(old) and rr.superseded(new)


def test_a_record_whose_video_was_requested_is_not_stopped(env: dict[str, Any]) -> None:  # noqa: F811
    first = B._brand(env)  # noqa: SLF001
    rid = _prepare_skill(first)["creative_record_id"]
    assert B._make(env, rid).outcome == request_creative.VIDEO_HELD  # noqa: SLF001
    assert rr.supersede(rid, None) is False and not rr.superseded(rid)


def test_keyframes_stop_drawing_once_the_record_is_stopped(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _prepare_skill(B._brand(env))["creative_record_id"]  # noqa: SLF001
    env["gate"].calls.clear()  # 11/26 C: 준비 때 그린 스토리보드 그림 1장은 빼고 본다
    render = env["store"].get_render(rid)
    render["keyframes"] = {"status": "pending", "frames": [{"index": 0, "status": "pending"}]}
    env["store"].set_render(rid, render)
    assert rr.supersede(rid, "CR-aaaaaaaaaaaa")
    why = B._run(lambda: keyframes.ensure(env["ledger"], B._meta(), rid, None))  # noqa: SLF001
    assert why == rr.CHARACTER_CHANGED and env["gate"].calls == []

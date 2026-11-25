"""키프레임을 최대 3장 동시에 그리기 (11/26 perf). 가짜 Higgsfield(MCP)에 지연을 넣어 본다.

- 동시에 그리는 장은 `keyframes.PARALLEL`(3)장을 넘지 않는다(장이 더 많아도)
- 결과 순서·상태·파일 이름은 한 장씩 그릴 때와 같다. 같은 장을 두 번 그리지 않는다
- 한 장이 실패해도 나머지는 done(그 장만 실패, 영상은 요청하지 않는다)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import TaskStatus
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_render as rr
from marketing_agent import keyframes as kf
from marketing_agent import request_creative
from marketing_agent.higgsfield_mcp import McpError


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


K = _load("keyframe_video_helpers_parallel", "test_keyframe_video.py")
env, kenv = K.env, K.kenv  # 시험 도구(fixture)
DELAY_S = 0.05


def _scenes(n: int) -> list[dict[str, Any]]:
    """장면 n개 → 키프레임 n+1장(장면 경계마다 한 장)."""
    return [K._scene(i, (i - 1) * 2, i * 2, start_svg=K._svg(f"#{i:02d}{i:02d}{i:02d}"))  # noqa: SLF001
            for i in range(1, n + 1)]  # fmt: skip


def _slow_images(monkeypatch: pytest.MonkeyPatch, fail_on: str | None = None) -> dict[str, Any]:
    """그림 submit마다 DELAY_S 기다린다. 동시에 몇 장이 그려지는지 센다."""
    real = cm.submit
    seen: dict[str, Any] = {"now": 0, "peak": 0, "images": 0}

    async def submit(hf: Any, kind: str, params: dict[str, Any]) -> str:
        if kind != "image":
            return await real(hf, kind, params)
        seen["images"] += 1
        seen["now"] += 1
        seen["peak"] = max(seen["peak"], seen["now"])
        try:
            await asyncio.sleep(DELAY_S)  # 실제 시계(가짜 Higgsfield가 늦게 답한다)
            if fail_on and fail_on in json.dumps(params, ensure_ascii=False):
                raise McpError("tool_error", stage="submit", tool="generate_image_batch")
            return await real(hf, kind, params)
        finally:
            seen["now"] -= 1

    monkeypatch.setattr(cm, "submit", submit)
    return seen


def test_at_most_three_frames_are_drawn_at_once(
    kenv: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = K._record(kenv, _scenes(6))  # noqa: SLF001 - 7장
    seen = _slow_images(monkeypatch)
    out = K._make(kenv, rid)  # noqa: SLF001
    assert out.outcome == request_creative.VIDEO_HELD
    assert seen["images"] == 7
    assert seen["peak"] == kf.PARALLEL == 3  # 동시에 3장까지(한 장씩이면 1, 제한이 없으면 7)


def test_order_and_status_are_the_same_as_one_by_one(
    kenv: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = K._record(kenv, _scenes(4))  # noqa: SLF001 - 5장
    _slow_images(monkeypatch)
    out = K._make(kenv, rid)  # noqa: SLF001
    assert out.outcome == request_creative.VIDEO_HELD
    state = kenv["store"].get_render(rid)["keyframes"]
    frames = state["frames"]
    assert state["status"] == "done"
    assert [f["index"] for f in frames] == [0, 1, 2, 3, 4]
    assert [f["status"] for f in frames] == ["done"] * 5
    assert [f["asset"] for f in frames] == [f"keyframe_{i}.jpg" for i in range(5)]
    assert all(f["attempt"] == 1 for f in frames)  # 같은 장을 두 번 그리지 않았다
    parts = [p["scene_template_id"] for p in K._image_gates(kenv)]  # noqa: SLF001
    assert sorted(parts) == sorted(f"creative_keyframe_{i}_1" for i in range(5))


def test_one_failed_frame_does_not_stop_the_others(
    kenv: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = K._record(kenv, _scenes(4))  # noqa: SLF001
    seen = _slow_images(monkeypatch, fail_on="Still keyframe 3 of")  # 가운데 장(index 2)만
    out = K._make(kenv, rid)  # noqa: SLF001
    assert out.status == TaskStatus.FAILED and out.outcome == "keyframe_failed"
    frames = kenv["store"].get_render(rid)["keyframes"]["frames"]
    failed = [f["index"] for f in frames if f["status"] == "failed"]
    assert failed == [2] and seen["images"] == 5
    assert all(f["status"] == "done" for f in frames if f["index"] not in failed)
    assert not [a for a, _ in kenv["gate"].calls if a == "generate_creative_video"]


def test_two_ensures_at_once_still_draw_each_frame_once(
    kenv: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = K._record(kenv, _scenes(4))  # noqa: SLF001
    seen = _slow_images(monkeypatch)
    # 다른 쪽이 그리는 장을 기다리는 폴링이 실제로 조금 쉬게(시험 도구는 기본으로 쉬지 않는다)
    monkeypatch.setattr(rr, "SLEEP", lambda _s: asyncio.sleep(0.01))

    async def both() -> Any:
        return await asyncio.gather(
            kf.ensure(kenv["ledger"], K.M.META, rid, "catbooks"),
            kf.ensure(kenv["ledger"], K.M.META, rid, "catbooks"),
        )

    assert K.M._run(both) == [None, None]  # noqa: SLF001
    assert seen["images"] == 5 and seen["peak"] <= 2 * kf.PARALLEL

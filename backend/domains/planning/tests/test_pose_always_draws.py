"""기획 콘티는 원인이 무엇이든 4칸이 그려진다 (11/26 실패 사다리). 가짜 LLM만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import LLMResponseError, LLMSettings
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")
POSE = {"shot": "medium", "yaw": 30, "arm_r": {"raise": 60, "bend": 20}, "label_ko": "걷기"}


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_always_draws", "test_video_plan.py")


class Fake:
    """1단(_PoseOut)·2단(_ScenePoseOut) 답을 정한다. whole_error: 1단 실패, bad: 2단 실패 장면."""

    def __init__(self, whole_error: BaseException | None = None, bad: set[int] | None = None,
                 one_error: BaseException | None = None) -> None:  # fmt: skip
        self.whole_error, self.bad, self.one_error = whole_error, bad or set(), one_error
        self.calls: list[str] = []

    async def structured(self, prompt: str, schema: type, untrusted: dict | None = None,
                         images: Any = None, context_id: str | None = None) -> Any:  # fmt: skip
        if schema is sb._PoseOut:  # noqa: SLF001
            self.calls.append("whole")
            if self.whole_error is not None:
                raise self.whole_error
            n = len((untrusted or {})["scenes"])
            return sb._PoseOut.model_validate(  # noqa: SLF001
                {"scenes": [{"n": i, "start": POSE, "end": POSE} for i in range(1, n + 1)]})
        n = (untrusted or {})["only"]
        self.calls.append(f"one:{n}")
        if self.one_error is not None or n in self.bad:
            raise self.one_error or LLMResponseError("x", status_code=500)
        return sb._ScenePoseOut.model_validate({"start": POSE, "end": POSE})  # noqa: SLF001


def _plan() -> Any:
    return vp.rule_plan(V._prop())  # noqa: SLF001


def _bad(message: str) -> LLMResponseError:
    return LLMResponseError("400", status_code=400, provider_message=message)


def _draw(llm: Any) -> tuple[Any, int]:
    return asyncio.run(sb.draw(llm, _plan(), "ctx"))


def test_first_step_success_is_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    llm = Fake()
    out, n = _draw(llm)
    assert llm.calls == ["whole"] and n == len(out.scenes)
    assert out.svg_reason == "" and out.svg_detail == ""
    assert all(s.start_pose and s.start_pose.yaw == 30 for s in out.scenes)


def test_400_splits_per_scene_and_failed_scenes_get_rule_poses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    llm = Fake(whole_error=_bad("Schema is too complex for compilation"), bad={3})
    out, n = _draw(llm)
    k = len(out.scenes)
    assert llm.calls[0] == "whole"
    assert sorted(llm.calls[1:]) == [f"one:{i}" for i in range(1, k + 1)]
    assert n == k and out.svg_reason == "" and out.svg_detail == "rule_pose_1"
    by_n = {s.n: s for s in out.scenes}
    assert by_n[1].start_pose.yaw == 30 and by_n[3].start_pose.yaw != 30  # 3장면만 규칙 포즈
    assert all(s.start_svg and s.end_svg for s in out.scenes)


def test_everything_failing_still_draws_four_rule_poses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    for llm in (None, Fake(whole_error=_bad("schema"), one_error=_bad("schema"))):
        out, n = _draw(llm)
        assert n == 4 == len(out.scenes) and out.svg_detail == "rule_pose_4"
        assert out.svg_reason == "" and all(s.start_svg and s.end_svg for s in out.scenes)


def test_model_400_uses_the_base_planning_model(monkeypatch: pytest.MonkeyPatch) -> None:
    pose_model = Fake(whole_error=_bad("model: claude-x not found"),
                      one_error=_bad("model: claude-x not found"))  # fmt: skip
    base = Fake()
    made: list[str] = []

    def client(dept: str, settings: LLMSettings | None = None, **_: Any) -> Any:
        made.append(settings.model if settings else "")
        return pose_model if settings and settings.model == "pose-model" else base

    monkeypatch.setattr(sb, "LLMClient", client)
    monkeypatch.setattr(sb, "settings_for", lambda dept: LLMSettings(model="plan-model",
                                                                     base_url="https://x/v1"))
    monkeypatch.setenv(sb.MODEL_ENV, "pose-model")
    out, n = _draw(Fake())
    assert made == ["pose-model", "plan-model"]  # 3단에 갈 때만 기본 모델을 만든다
    assert sorted(base.calls) == sorted(f"one:{i}" for i in range(1, 5))
    assert n == 4 and out.svg_detail == ""  # 기본 모델이 다 그렸다


def test_end_pose_follows_the_next_scene_start() -> None:
    out, _ = _draw(None)
    a, b = out.scenes[0], out.scenes[1]
    assert not a.end_frame  # 규칙 제작안은 끝 화면 글이 없다 → 다음 장면 첫 화면과 같은 순간
    assert a.end_pose.yaw == b.start_pose.yaw and a.end_pose.shot == b.start_pose.shot

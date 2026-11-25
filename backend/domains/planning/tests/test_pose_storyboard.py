"""3D 마네킹 콘티 (11/25): LLM은 포즈 숫자만, 그림은 코드(`pose_render`)가 그린다.

- 계약 KeyPose: 범위 밖 숫자는 자른다. 예전 제작안(포즈 없음)도 읽는다
- 렌더: 같은 포즈 같은 SVG, 샷 크기별 인물 크기(close > medium > full), 옆을 보면 눈 없음,
  소품, 16KB 이하, 정리 규칙 통과(그라데이션 유지)
- LLM 포즈 → start_svg·end_svg·start_pose·end_pose, 실패 → 빈 칸·제작안 그대로, 호출 1회,
  PLANNING_SVG_LLM_MODEL이 있으면 이 호출만 그 모델
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from ax_agent_template import pose_render as pr
from ax_agent_template.llm import LLMSettings, MockLLMClient
from ax_contracts.schemas.planning import KeyPose, VideoPlan, VideoScene
from ax_workflow.models import Proposal
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")

# 화면 시안(3D 마네킹 콘티)의 4칸 포즈와 같은 입력
SAMPLES = [
    {"label_ko": "0초 · 놀라 돌아봄", "shot": "medium", "x": 0, "z": 0, "yaw": 35,
     "head": {"yaw": -40, "pitch": -5}, "arm_l": {"raise": 40, "bend": 60},
     "arm_r": {"raise": 30, "bend": 80}, "step": 0.15},
    {"label_ko": "2초 · 책상에서 한 줄", "shot": "full", "x": -0.5, "z": 0.5, "yaw": -20,
     "head": {"yaw": 0, "pitch": 20}, "arm_l": {"raise": 50, "bend": 70},
     "arm_r": {"raise": 55, "bend": 60}, "step": 0.05, "prop": "desk"},
    {"label_ko": "7초 · 노트 들고 보여 줌", "shot": "medium", "x": 0.3, "z": 0, "yaw": 0,
     "head": {"yaw": 10, "pitch": 0}, "arm_l": {"raise": 70, "bend": 40},
     "arm_r": {"raise": 10, "bend": 10}, "step": 0.1, "prop": "book"},
    {"label_ko": "12초 · 걸어 나감", "shot": "full", "x": 0.8, "z": 1.2, "yaw": -70,
     "head": {"yaw": 20, "pitch": 0}, "arm_l": {"raise": 25, "bend": 20},
     "arm_r": {"raise": -20, "bend": 15}, "step": 0.45},
]  # fmt: skip
PROP_COLORS = {"book": "#e2552f", "desk": "#c9a27a", "phone": "#2b2f36", "shelf": "#b98d63",
               "cup": "#f4f1ea"}  # fmt: skip


def _pose(**kw: Any) -> KeyPose:
    return KeyPose.model_validate(kw)


# ---------------------------------------------------------------- 계약


def test_pose_numbers_are_clamped_and_round_trip() -> None:
    p = _pose(shot="full", x=9, z=-1, yaw=-500, head={"yaw": 200, "pitch": -90},
              arm_l={"raise": 300, "bend": -5}, arm_r={"raise": -99, "bend": 999}, step=2,
              label_ko="가" * 40)  # fmt: skip
    assert (p.x, p.z, p.yaw, p.step) == (1.5, 0, -180, 0.6)
    assert (p.head.yaw, p.head.pitch) == (80, -40)
    assert (p.arm_l.raise_, p.arm_l.bend, p.arm_r.raise_, p.arm_r.bend) == (170, 0, -30, 150)
    assert len(p.label_ko) == 30
    dumped = p.model_dump(mode="json")
    assert dumped["arm_l"] == {"raise": 170.0, "bend": 0.0}  # JSON 칸 이름은 raise
    assert KeyPose.model_validate(dumped) == p
    with pytest.raises(ValueError):
        _pose(shot="wide")
    with pytest.raises(ValueError):
        _pose(prop="sword")


def test_old_scene_without_pose_still_reads() -> None:
    old = {"n": 1, "start_s": 0, "end_s": 3, "visual": "책상", "prompt": "a desk", "start_svg": ""}
    s = VideoScene.model_validate(old)
    assert s.start_pose is None and s.end_pose is None
    big = "<svg>" + "x" * 16000 + "</svg>"
    assert VideoScene.model_validate({**old, "start_svg": big}).start_svg == big  # 16KB까지 받는다


# ---------------------------------------------------------------- 렌더러


@pytest.mark.parametrize("raw", SAMPLES)
def test_same_pose_same_svg_and_under_16kb(raw: dict[str, Any]) -> None:
    p = KeyPose.model_validate(raw)
    svg = pr.render(p)
    assert svg == pr.render(KeyPose.model_validate(raw))
    assert len(svg.encode()) <= 16384
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320">')
    assert raw["label_ko"] in svg and svg.count("<text") == 1  # 글자는 칸 위 한 줄만
    assert not re.search(r"\d\.\d{2,}", svg)  # 숫자는 소수 1자리
    for part in ("<linearGradient", "<radialGradient", 'stroke-linecap="round"', "<ellipse"):
        assert part in svg
    clean = sb.sanitize_svg(svg)  # 정리 규칙을 통과하고 요소·속성이 하나도 빠지지 않는다
    assert clean.count("<") == svg.count("<") and clean.count("=") == svg.count("=")
    assert 'fill="url(#sk)"' in clean and "<stop" in clean


# 렌더러만 보는 시험(포즈마다 다른 그림·샷 크기·눈·소품·비율·글자 이스케이프)은 공용 렌더러로
# 옮겼다: shared/agent-template/tests/test_pose_render.py. 여기는 기획 정리 규칙 통과만 본다


def test_every_shot_and_prop_stays_under_16kb() -> None:
    for shot in ("close", "medium", "full"):
        for prop in (None, *PROP_COLORS):
            for yaw in (-180, -90, 0, 45, 135):
                svg = pr.render(_pose(shot=shot, prop=prop, yaw=yaw, x=1.5, z=3, step=0.6))
                assert len(svg.encode()) <= 16384
                assert sb.sanitize_svg(svg)


# ---------------------------------------------------------------- LLM 포즈 → 제작안


def _prop() -> Proposal:
    return Proposal.model_validate({
        "id": "PROP-37", "title": "출근길 상황극", "from": "data", "skill": "trend_report_insight",
        "insight": "30대 여성이 출근길 상황극을 오래 본다", "idea": "출근길 공감 상황극",
        "why": "상황극 쇼츠 조회", "created_at": datetime(2026, 11, 25, tzinfo=UTC),
    })  # fmt: skip


def _plan() -> VideoPlan:
    return vp.rule_plan(_prop(), (vp.FUN_FIRST, vp.END_CARD))


def _out(plan: VideoPlan) -> sb._PoseOut:
    scenes = [{"n": s.n, "start": SAMPLES[i % 4], "end": SAMPLES[(i + 1) % 4]}
              for i, s in enumerate(plan.scenes)]  # fmt: skip
    return sb._PoseOut.model_validate({"scenes": scenes})


def test_llm_poses_fill_svg_and_pose_with_one_call() -> None:
    plan = _plan()
    llm = MockLLMClient(responses={sb._PoseOut: _out(plan)})
    drawn, changed = asyncio.run(sb.draw(llm, plan, "ctx"))
    assert changed == len(plan.scenes) and len(llm.calls) == 1
    for i, s in enumerate(drawn.scenes):
        assert s.start_pose == KeyPose.model_validate(SAMPLES[i % 4])
        assert s.end_pose == KeyPose.model_validate(SAMPLES[(i + 1) % 4])
        assert s.start_svg == sb.sanitize_svg(pr.render(s.start_pose, plan.aspect))
        assert "<radialGradient" in s.start_svg and s.end_svg
    # 장면 글은 그대로
    words = [(s.visual, s.prompt) for s in plan.scenes]
    assert [(s.visual, s.prompt) for s in drawn.scenes] == words
    sent = llm.calls[0]
    assert '"camera_en"' in sent and "close-up" in sent and "SVG·그림 원문은 쓰지 않는다" in sent


def test_unknown_shot_and_prop_from_llm_become_defaults_and_label_falls_back() -> None:
    plan = _plan()
    weird = {"shot": "extreme wide", "prop": "sword", "x": 99, "label_ko": ""}
    got = sb._PoseOut.model_validate({"scenes": [{"n": 1, "start": weird}]})
    drawn, changed = sb.apply(plan, got)
    s = drawn.scenes[0]
    assert changed == 1 and s.start_pose is not None
    assert (s.start_pose.shot, s.start_pose.prop, s.start_pose.x) == ("medium", None, 1.5)
    assert s.start_pose.label_ko == f"{s.start_s}초"
    assert s.end_pose is None and s.end_svg == ""  # 끝 포즈가 없으면 그 칸은 그대로


def test_llm_failure_keeps_plan_without_pose_or_svg() -> None:
    plan = _plan()
    for llm in (None, MockLLMClient(responses={})):
        drawn, changed = asyncio.run(sb.draw(llm, plan, "ctx"))
        assert changed == 0 and drawn.svg_reason  # 11/25 5-1: 이유를 남긴다 (장면은 그대로)
        assert drawn.scenes == plan.scenes
        assert drawn.model_copy(update={"svg_reason": ""}) == plan
    assert all(s.start_pose is None and not s.start_svg for s in plan.scenes)


class _Slow:
    """제한 시간보다 늦게 답하는 가짜 LLM."""

    async def structured(self, *_: Any, **__: Any) -> Any:
        await asyncio.sleep(5)


class _Raises:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def structured(self, *_: Any, **__: Any) -> Any:
        raise self.exc


def _reason(llm: Any) -> str:
    drawn, changed = asyncio.run(sb.draw(llm, _plan(), "ctx"))
    assert changed == 0 and all(not s.start_svg and not s.end_svg for s in drawn.scenes)
    return drawn.svg_reason


def test_svg_reason_for_each_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    assert _reason(None) == "no_llm"
    monkeypatch.setattr(sb, "TIMEOUT_SECONDS", 0.05)
    assert _reason(_Slow()) == "timeout"
    assert _reason(_Raises(sb.LLMFormatError("형식"))) == "bad_format"
    assert _reason(_Raises(RuntimeError("끊김"))) == "error"
    empty = MockLLMClient(responses={sb._PoseOut: sb._PoseOut.model_validate({"scenes": []})})
    assert _reason(empty) == "bad_format"  # 답은 왔지만 장면이 없다
    wrong_n = sb._PoseOut.model_validate({"scenes": [{"n": 99, "start": SAMPLES[0]}]})
    assert _reason(MockLLMClient(responses={sb._PoseOut: wrong_n})) == "rejected"


def test_timeout_is_150s_for_the_background_flow() -> None:
    assert sb.TIMEOUT_SECONDS == 150.0


def test_partial_poses_draw_only_those_scenes_and_success_has_no_reason() -> None:
    plan = _plan()
    got = sb._PoseOut.model_validate({"scenes": [{"n": 2, "start": SAMPLES[0], "end": SAMPLES[1]}]})
    drawn, changed = asyncio.run(sb.draw(MockLLMClient(responses={sb._PoseOut: got}), plan, "c"))
    assert changed == 1 and drawn.svg_reason == ""
    for s in drawn.scenes:
        assert bool(s.start_svg) == bool(s.end_svg) == (s.n == 2)
    full, n = asyncio.run(sb.draw(MockLLMClient(responses={sb._PoseOut: _out(plan)}), plan, "c"))
    assert n == len(plan.scenes) and full.svg_reason == ""


def test_failure_log_has_kind_seconds_and_count_only(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        _reason(_Raises(RuntimeError("비밀 프롬프트 내용")))
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in line and "s 0/" in line and "비밀" not in line


class _Recorder:
    """다른 모델로 만든 포즈 LLM (설정만 기록)."""

    made: list[LLMSettings] = []

    def __init__(self, dept: str, settings: LLMSettings) -> None:
        self.dept = dept
        _Recorder.made.append(settings)
        self.calls: list[str] = []

    async def structured(self, prompt: str, schema: type[BaseModel], **_: Any) -> Any:
        self.calls.append(prompt)
        return _out(_plan())


def test_svg_model_env_changes_only_the_pose_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _Recorder.made = []
    base = LLMSettings(model="plan-model", base_url="https://llm.example/v1", api_key="k-test")
    monkeypatch.setattr(sb, "settings_for", lambda dept: base)
    monkeypatch.setattr(sb, "LLMClient", _Recorder)
    scenes = [{"visual": f"장면 {i}", "caption": "", "prompt": "A woman reading on a calm subway"}
              for i in range(4)]  # fmt: skip
    plan_out = vp._PlanOut.model_validate({"concept": "출근길", "script": "훅", "scenes": scenes})
    llm = MockLLMClient(responses={vp._PlanOut: plan_out})  # 포즈 답은 없다 → 부르면 실패

    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    assert sb.pose_llm(llm) is llm  # 없으면 제작안과 같은 LLM
    monkeypatch.setenv(sb.MODEL_ENV, "pose-model")
    plan = asyncio.run(vp.write(llm, _prop(), "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    assert plan.written_by == "llm" and len(llm.calls) == 1  # 제작안만 원래 LLM
    assert len(_Recorder.made) == 1
    made = _Recorder.made[0]
    assert made.model == "pose-model"  # 모델만 바뀌고
    assert (made.base_url, made.api_key) == (base.base_url, base.api_key)  # 주소·키는 그대로
    assert all(s.start_pose and s.start_svg for s in plan.scenes)

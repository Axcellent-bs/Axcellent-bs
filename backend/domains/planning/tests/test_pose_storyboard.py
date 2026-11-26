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
        # 11/26 바뀐 기대값: 라벨 시각은 LLM 글(예 "0초 · …")이 아니라 장면 시각으로 코드가 붙인다
        def timed(raw: dict, at: int) -> KeyPose:
            return KeyPose.model_validate({**raw, "label_ko": pr.time_label(raw["label_ko"], at)})

        assert s.start_pose == timed(SAMPLES[i % 4], s.start_s)
        assert s.end_pose == timed(SAMPLES[(i + 1) % 4], s.end_s)
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


def test_llm_failure_draws_rule_poses_for_every_scene() -> None:
    """11/26 실패 사다리 4단: LLM이 없거나 실패해도 콘티는 규칙 포즈로 다 그린다.

    (예전 기대값: 장면 그대로·svg_reason 있음·svg_detail "KeyError". 이유는 이제 로그에 남는다)"""
    plan = _plan()
    for llm in (None, MockLLMClient(responses={})):
        drawn, changed = asyncio.run(sb.draw(llm, plan, "ctx"))
        n = len(plan.scenes)
        assert changed == n and drawn.svg_reason == "" and drawn.svg_detail == f"rule_pose_{n}"
        assert all(s.start_pose and s.start_svg and s.end_pose and s.end_svg for s in drawn.scenes)
        assert [(s.visual, s.prompt) for s in drawn.scenes] == [(s.visual, s.prompt)
                                                                  for s in plan.scenes]  # fmt: skip
    assert all(s.start_pose is None and not s.start_svg for s in plan.scenes)  # 원본은 그대로


class _Slow:
    """제한 시간보다 늦게 답하는 가짜 LLM."""

    async def structured(self, *_: Any, **__: Any) -> Any:
        await asyncio.sleep(5)


class _Raises:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def structured(self, *_: Any, **__: Any) -> Any:
        raise self.exc


def _reason(llm: Any, caplog: pytest.LogCaptureFixture) -> str:
    """그 실패에서도 4칸 모두 규칙 포즈로 그리고, 실패 이유는 로그 줄로 돌려준다."""
    caplog.clear()
    with caplog.at_level("WARNING"):
        drawn, changed = asyncio.run(sb.draw(llm, _plan(), "ctx"))
    n = len(drawn.scenes)
    assert changed == n and drawn.svg_reason == "" and drawn.svg_detail == f"rule_pose_{n}"
    assert all(s.start_svg and s.end_svg for s in drawn.scenes)
    return " ".join(r.getMessage() for r in caplog.records)


def test_each_failure_falls_back_to_rule_poses_and_logs_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """(예전 기대값: svg_reason no_llm·timeout·bad_format·error·rejected. 이제 4칸은 늘 그려지고
    이유는 로그 줄에 남는다)"""
    monkeypatch.delenv(sb.MODEL_ENV, raising=False)
    assert "LLM 없음" in _reason(None, caplog)
    monkeypatch.setattr(sb, "TIMEOUT_SECONDS", 0.05)
    assert "TimeoutError" in _reason(_Slow(), caplog)
    assert "LLMFormatError" in _reason(_Raises(sb.LLMFormatError("형식")), caplog)
    assert "RuntimeError" in _reason(_Raises(RuntimeError("끊김")), caplog)
    empty = MockLLMClient(responses={sb._PoseOut: sb._PoseOut.model_validate({"scenes": []})})
    _reason(empty, caplog)  # 답은 왔지만 장면이 없다 → 1단 성공이라 2단 없이 규칙 포즈
    assert len(empty.calls) == 1
    wrong_n = sb._PoseOut.model_validate({"scenes": [{"n": 99, "start": SAMPLES[0]}]})
    _reason(MockLLMClient(responses={sb._PoseOut: wrong_n}), caplog)


def test_timeout_is_150s_for_the_background_flow() -> None:
    assert sb.TIMEOUT_SECONDS == 150.0


def test_partial_poses_draw_only_those_scenes_and_success_has_no_reason() -> None:
    plan = _plan()
    got = sb._PoseOut.model_validate({"scenes": [{"n": 2, "start": SAMPLES[0], "end": SAMPLES[1]}]})
    drawn, changed = asyncio.run(sb.draw(MockLLMClient(responses={sb._PoseOut: got}), plan, "c"))
    # (예전 기대값: 1장면만 그리고 나머지는 빈 칸. 이제 나머지 3장면은 규칙 포즈)
    assert changed == len(plan.scenes) and drawn.svg_reason == ""
    assert drawn.svg_detail == f"rule_pose_{len(plan.scenes) - 1}"
    llm_drawn = next(s for s in drawn.scenes if s.n == 2)
    assert llm_drawn.start_pose and llm_drawn.start_pose.yaw == KeyPose.model_validate(
        SAMPLES[0]).yaw  # 2장면은 LLM 포즈 그대로  # fmt: skip
    assert all(s.start_svg and s.end_svg for s in drawn.scenes)
    full, n = asyncio.run(sb.draw(MockLLMClient(responses={sb._PoseOut: _out(plan)}), plan, "c"))
    assert n == len(plan.scenes) and full.svg_reason == "" and full.svg_detail == ""


def test_failure_log_has_kind_seconds_and_count_only(caplog: pytest.LogCaptureFixture) -> None:
    line = _reason(_Raises(RuntimeError("비밀 프롬프트 내용")), caplog)
    assert "RuntimeError" in line and "s 0/" in line and "비밀" not in line


class _Recorder:
    """다른 모델로 만든 포즈 LLM (설정만 기록)."""

    made: list[LLMSettings] = []
    timeouts: list[float | None] = []

    def __init__(self, dept: str, settings: LLMSettings, timeout: float | None = None) -> None:
        self.dept = dept
        _Recorder.made.append(settings)
        _Recorder.timeouts.append(timeout)  # 11/26 C: 포즈 호출만 긴 httpx 타임아웃
        self.calls: list[str] = []

    async def structured(self, prompt: str, schema: type[BaseModel], **_: Any) -> Any:
        self.calls.append(prompt)
        return _out(_plan())


def test_svg_model_env_changes_only_the_pose_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _Recorder.made, _Recorder.timeouts = [], []
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
    assert _Recorder.timeouts == [sb.TIMEOUT_SECONDS]  # 11/26 C: 60초에 끊기지 않게
    assert all(s.start_pose and s.start_svg for s in plan.scenes)


def test_copied_zero_second_labels_become_scene_times_and_scene_words_add_props() -> None:
    """11/26 운영 콘티 4칸 라벨이 전부 "0초 · …"였고 마이크가 없었다."""
    plan = _plan()
    stage = plan.scenes[1].model_copy(update={"visual": "무대에서 마이크를 들고 노래한다"})
    plan = plan.model_copy(update={"scenes": [plan.scenes[0], stage, *plan.scenes[2:]]})
    zero = {"label_ko": "0초 · 돌아봄", "shot": "medium"}
    got = sb._PoseOut.model_validate(  # noqa: SLF001
        {"scenes": [{"n": s.n, "start": zero, "end": zero} for s in plan.scenes]}
    )
    drawn, _ = sb.apply(plan, got)
    starts = [s.start_pose.label_ko for s in drawn.scenes if s.start_pose]
    assert starts == [f"{s.start_s}초 · 돌아봄" for s in plan.scenes]
    assert [s.end_pose.label_ko for s in drawn.scenes if s.end_pose][-1] == (
        f"{plan.scenes[-1].end_s}초 · 돌아봄"
    )
    assert drawn.scenes[1].start_pose and drawn.scenes[1].start_pose.extra_props == ["mic"]
    assert drawn.scenes[0].start_pose and drawn.scenes[0].start_pose.extra_props == []
    assert "#8a8f99" in drawn.scenes[1].start_svg  # 마이크가 그려진다
    assert "<text" in drawn.scenes[1].start_svg and "0초 · " not in drawn.scenes[1].start_svg

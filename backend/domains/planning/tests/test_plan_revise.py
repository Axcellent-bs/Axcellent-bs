"""마케팅 요청으로 제작안 고치기 (11/26 A2A `revise_video_plan`, 제안당 1회). 가짜 LLM·가짜 관문."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import CheckProblem, ReviseVideoPlanInput
from planning_agent import handlers, runtime
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_revise", "test_video_plan.py")
C = _load("company_character_helpers_revise", "test_plan_company_character.py")
POSE = {"shot": "medium", "yaw": 20, "label_ko": "0초 · 돌아봄"}
SHORT = CheckProblem(kind="duration", scene=1, detail_ko="장면 길이 2초 (영상 모델 최소 3초)")
FRIEND = CheckProblem(kind="refs", scene=2, detail_ko="회사 캐릭터 말고 다른 인물이 가까이 나와요")


def _posed(llm: Any) -> Any:
    """제작안 + 포즈까지 그린 1판 (총괄 의뢰 흐름처럼)."""
    out = sb._PoseOut.model_validate(  # noqa: SLF001
        {"scenes": [{"n": n, "start": POSE, "end": POSE} for n in range(1, 5)]}
    )
    llm.responses[sb._PoseOut] = out  # noqa: SLF001
    return llm


def _service(*plans: Any) -> tuple[PlanningService, Any, str]:
    llm = _posed(C.SeqLLM(plans=list(plans)))
    svc = PlanningService(gate=V._gate, relater=None, marketing=None,  # noqa: SLF001
                          proposal_writer=V.Writer(llm))  # fmt: skip
    res = asyncio.run(svc.receive_insight(requester="orchestrator", inp=V._insight()))  # noqa: SLF001
    return svc, llm, res.output["proposal_id"]


def _revise(svc: PlanningService, pid: str, *problems: CheckProblem, version: int = 1) -> Any:
    inp = ReviseVideoPlanInput(proposal_id=pid, from_version=version, problems=list(problems))
    return asyncio.run(svc.revise_video_plan(requester="marketing", inp=inp))


def test_short_scene_only_is_retimed_by_code_without_the_llm() -> None:
    svc, llm, pid = _service(V._llm_out())  # noqa: SLF001
    v1 = next(p for p in svc.state.proposals if p.id == pid).video_plan
    assert v1 and v1.scenes[0].end_s - v1.scenes[0].start_s == 2  # 재미 먼저 훅 0~2초
    calls = len(llm.calls)
    res = _revise(svc, pid, SHORT)
    assert res.outcome == "revised" and len(llm.calls) == calls  # 장면 시간은 코드 → LLM 0회
    v2 = next(p for p in svc.state.proposals if p.id == pid).video_plan
    assert v2 and v2.version == 2 and res.output["video_plan"]["version"] == 2
    assert all(s.end_s - s.start_s >= 3 for s in v2.scenes)
    assert [s.visual for s in v2.scenes] == [s.visual for s in v1.scenes]  # 글은 그대로
    by = [(r.attempt, r.by, r.passed) for r in v2.check_log]
    assert by[-2:] == [(1, "marketing", False), (2, "planning", True)]
    assert v2.check_log[-2].problems[0].detail_ko == SHORT.detail_ko
    # 콘티는 앞 판 포즈를 새 시각 라벨로 다시 그린다 (LLM 없음)
    assert v2.scenes[1].start_pose and v2.scenes[1].start_pose.label_ko.startswith(
        f"{v2.scenes[1].start_s}초 · "
    )
    assert v2.scenes[1].start_svg and v2.scenes[1].start_svg != v1.scenes[1].start_svg


def test_other_problems_call_the_llm_once_with_the_marketing_fixes() -> None:
    first = V._llm_out()  # noqa: SLF001
    fixed = V._llm_out(prompt="The mascot alone on a stage, slow push-in")  # noqa: SLF001
    svc, llm, pid = _service(first, fixed)
    calls = len(V.plan_calls(llm))
    res = _revise(svc, pid, SHORT, FRIEND)
    sent = V.plan_calls(llm)
    assert res.outcome == "revised" and len(sent) == calls + 1  # 이 호출이 그 1회
    assert vp.MARKETING_FIX_HEAD.strip() in sent[-1]
    assert "마케팅 확인 장면 2: 회사 캐릭터 말고 다른 인물이 가까이 나와요" in sent[-1]
    v2 = next(p for p in svc.state.proposals if p.id == pid).video_plan
    assert v2 and v2.version == 2 and v2.scenes[0].prompt.startswith("The mascot alone")
    assert all(s.end_s - s.start_s >= 3 for s in v2.scenes)  # 길이 문제가 있으면 고르게


def test_one_revise_per_proposal() -> None:
    svc, llm, pid = _service(V._llm_out())  # noqa: SLF001
    assert _revise(svc, pid, SHORT).outcome == "revised"
    calls = len(llm.calls)
    again = _revise(svc, pid, SHORT)  # 다시 준비해서 1판으로 또 물으면 이미 고친 2판
    assert again.outcome == "already" and again.output["video_plan"]["version"] == 2
    assert _revise(svc, pid, SHORT, version=2).outcome == "revise_limit"
    assert _revise(svc, pid, SHORT, version=3).outcome == "stale_version"
    assert len(llm.calls) == calls


def test_unknown_or_other_company_proposal_is_refused() -> None:
    svc, _, pid = _service(V._llm_out())  # noqa: SLF001
    assert _revise(svc, "PROP-999", SHORT).outcome == "not_found"
    runtime.set_service(svc)
    try:
        req = AgentRequest(
            meta=RequestMeta(context_id="ctx_wr_x", token="t", idempotency_key="k",
                             deadline=datetime.now(UTC) + timedelta(seconds=30),
                             tenant_id="nubitour"),
            skill="revise_video_plan",
            input={"proposal_id": pid, "from_version": 1,
                   "problems": [SHORT.model_dump(mode="json")]},
        )  # fmt: skip
        with company_scope.using("nubitour"):  # 다른 회사는 그 회사 서비스(빈 상태)를 본다
            out = asyncio.run(handlers.PLANNING_HANDLERS["revise_video_plan"](req))
        assert out.status == TaskStatus.FAILED and out.outcome == "not_found"
        mine = req.model_copy(update={"meta": req.meta.model_copy(update={"tenant_id": None})})
        done = asyncio.run(handlers.PLANNING_HANDLERS["revise_video_plan"](mine))
        assert done.status == TaskStatus.COMPLETED and done.output["video_plan"]["version"] == 2
    finally:
        runtime._service = None  # noqa: SLF001


def test_llm_failure_keeps_version_one() -> None:
    svc, _, pid = _service(V._llm_out())  # noqa: SLF001
    p = next(x for x in svc.state.proposals if x.id == pid)
    p.video_plan = p.video_plan.model_copy(update={"version": 1})  # type: ignore[union-attr]
    svc._proposal_writer = V.Writer(None)  # noqa: SLF001 - LLM 없음
    res = _revise(svc, pid, FRIEND)
    assert res.outcome == "revise_no_llm"
    assert next(x for x in svc.state.proposals if x.id == pid).video_plan.version == 1  # type: ignore[union-attr]


def test_handler_needs_an_idempotency_key_and_valid_input() -> None:
    meta = RequestMeta(context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=5))
    bad = AgentRequest(meta=meta, skill="revise_video_plan", input={"proposal_id": "x"})
    out = asyncio.run(handlers.PLANNING_HANDLERS["revise_video_plan"](bad))
    assert out.outcome == "invalid_input"

"""개발 작업 결과 `list_dev_outcomes` (read): 인사·총괄의 부서·에이전트 평가 근거.

숫자는 개발 기록 그대로(작업·PR), 복잡도와 합계는 코드 규칙으로 센다. 상태를 바꾸지 않는다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas.planning import ListDevOutcomesOutput
from ax_workflow.models import CodingRun, Task
from dev_agent import outcomes
from dev_agent.main import app

pytestmark = pytest.mark.role("dev")


def _call(a2a_body, payload: dict, token: str = "mock:hr") -> dict:
    body = a2a_body("list_dev_outcomes", payload, token=token, context_id="ctx_eval_0001")
    return TestClient(app).post("/a2a", json=body).json()


def _coded(service):
    """시드에서 코딩 기록이 붙은 작업 셋: 서버 AI 머지 · 내 Claude 머지 · 서버 AI 실패."""
    t = service.clock()
    spec = service.state.specs[0].id
    tasks = [
        Task(
            id=f"T-EV{i}",
            spec=spec,
            para="R1",
            title=f"평가 시험 {i}",
            owner="개발",
            state="진행 중",
        )
        for i in range(3)
    ]
    service.state.tasks.extend(tasks)
    tasks[0].coding = CodingRun(
        status="merged",
        updated_at=t,
        mode="agent",
        model="m1",
        attempts=1,
        llm_calls=4,
        llm_tokens=1200,
    )
    tasks[1].coding = CodingRun(
        status="merged",
        updated_at=t,
        mode="subscription",
        claimed_by="kim",
        attempts=1,
        llm_calls=9,
        llm_tokens=9999,
    )
    tasks[2].coding = CodingRun(
        status="failed",
        updated_at=t,
        mode="agent",
        model="m1",
        attempts=2,
        llm_calls=6,
        llm_tokens=800,
    )
    return tasks  # fmt: skip


def test_outcome_rules_and_summary(service) -> None:
    tasks = _coded(service)
    rows, summary = outcomes.list_outcomes(service.state)
    mine = {r.task_id: r for r in rows if r.task_id in {t.id for t in tasks}}
    a, b, c = (mine[t.id] for t in tasks)
    assert (a.mode, a.coder, a.llm_tokens) == ("agent", "m1", 1200)
    # 내 Claude는 개인 구독이라 토큰을 세지 않는다
    assert (b.mode, b.coder, b.llm_tokens, b.llm_calls) == ("subscription", "kim", 0, 0)
    assert (c.status, c.review_mismatches, c.complexity) == ("failed", 1, "unknown")
    assert summary.total == len(rows) and summary.by_mode["subscription"] >= 1
    only_kim, s_kim = outcomes.list_outcomes(service.state, coder="kim")
    assert [r.task_id for r in only_kim] == [tasks[1].id] and s_kim.llm_tokens == 0
    agent_rows, _ = outcomes.list_outcomes(service.state, coder="agent")
    assert all(r.mode == "agent" for r in agent_rows)


def test_complexity_comes_from_pr_size_and_reviews(service) -> None:
    """시드 PR이 붙은 작업은 PR의 줄·파일·리뷰로 simple·complex를 정한다."""
    rows, _ = outcomes.list_outcomes(service.state)
    for r in rows:
        if r.pr_number is None:
            assert r.complexity == "unknown"
        elif r.complexity == "simple":
            assert (
                r.changed_lines <= outcomes.SIMPLE_LINES
                and r.changed_files <= outcomes.SIMPLE_FILES
            )
            assert r.review_mismatches == 0


def test_a2a_read_for_hr_only_logs_activity(service, a2a_body) -> None:
    _coded(service)
    before = len(service.state.activity)
    res = _call(a2a_body, {"coder": "kim"})
    assert res["status"] == "completed"
    out = ListDevOutcomesOutput.model_validate(res["output"])
    assert [r.coder for r in out.outcomes] == ["kim"] and out.summary.merged == 1
    assert len(service.state.activity) == before + 2  # 상태는 그대로, 활동 로그 두 줄
    denied = _call(a2a_body, {}, token="mock:marketing")
    assert denied["status"] != "completed"

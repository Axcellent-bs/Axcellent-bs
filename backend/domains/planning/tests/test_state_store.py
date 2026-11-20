"""상태 저장: 서버를 다시 켜도 기획·개발 기록과 중복 거르기 기록이 남는다."""

from __future__ import annotations

import asyncio
import os

from ax_contracts.schemas import ExecuteDecision
from dev_agent.service import DevService
from planning_agent.domain.models import CodingRun
from planning_agent.domain.service import PlanningService


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _ticket(svc: PlanningService, title: str, key: str):
    return asyncio.run(
        svc.create_ticket(
            context_id="ctx_x",
            requester="support",
            title=title,
            evidence="고객 문의",
            source_ref=None,
            evidence_ids=[],
            idempotency_key=key,
        )
    )


def test_state_survives_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = PlanningService(gate=_gate, relater=None)
    out = _ticket(svc, "공지 배너 문구 오타 수정", "k1")
    rid = out.output["request_id"]
    assert os.path.exists(tmp_path / "planning_state_planning.json")

    again = PlanningService(gate=_gate, relater=None)  # 다시 켬
    assert any(r.id == rid for r in again.state.requests)
    # 같은 키로 다시 와도 새로 만들지 않는다 (중복 거르기 기록도 남음)
    before = len(again.state.requests)
    _ticket(again, "공지 배너 문구 오타 수정", "k1")
    assert len(again.state.requests) == before


def test_roles_use_separate_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    _ticket(PlanningService(gate=_gate, relater=None), "기획 쪽 요청", "k2")
    dev = DevService(gate=_gate, relater=None)
    assert not any(r.title == "기획 쪽 요청" for r in dev.state.requests)


def test_running_coding_becomes_failed_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = DevService(gate=_gate, relater=None)
    task = svc.state.tasks[0]
    task.coding = CodingRun(status="writing", updated_at=svc.clock(), waiting_for=["T-x"])
    svc._save()
    again = DevService(gate=_gate, relater=None)
    cr = next(t for t in again.state.tasks if t.id == task.id).coding
    assert cr.status == "failed" and "다시 켜서" in cr.error and cr.waiting_for == []


def _with_open_pr(svc: DevService, task, status: str, mode: str = "agent") -> None:
    from planning_agent.domain.models import PR, PrGate  # noqa: PLC0415

    task.coding = CodingRun(
        status=status, updated_at=svc.clock(), mode=mode, pr="PR #90", transient=True
    )
    svc.state.prs.append(
        PR(
            id="PR #90", number=90, task=task.id, title="R1: x", branch="gen/x",
            reviews=[], gate=PrGate(result="통과", lines=1, files=1, paths="x"), merged=False,
        )
    )  # fmt: skip


def test_coding_with_open_pr_waits_for_human_after_restart(tmp_path, monkeypatch) -> None:
    """PR을 연 뒤(리뷰·CI 확인 중) 다시 켜면 그 실행은 없다. 열린 PR이 있어 다시 시도는
    거절되므로 사람 확인(머지·추가 지시)으로 둔다."""
    for status in ("opened", "reviewing", "queued"):
        monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path / status))
        svc = DevService(gate=_gate, relater=None)
        task = svc.state.tasks[0]
        _with_open_pr(svc, task, status)
        svc._save()
        again = DevService(gate=_gate, relater=None)
        cr = next(t for t in again.state.tasks if t.id == task.id).coding
        assert cr.status == "waiting_human" and not cr.transient, status
        assert "서버 재시작으로 리뷰를 다시 확인" in cr.error


def test_opened_task_with_merged_pr_is_not_failed_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = DevService(gate=_gate, relater=None)
    task = svc.state.tasks[0]
    _with_open_pr(svc, task, "opened")
    svc.state.prs[-1].merged = True
    svc._save()
    again = DevService(gate=_gate, relater=None)
    cr = next(t for t in again.state.tasks if t.id == task.id).coding
    assert cr.status == "opened" and cr.error is None


def test_subscription_coding_is_left_alone_after_restart(tmp_path, monkeypatch) -> None:
    """내 Claude 작업은 이 서버가 돌리지 않는다: 대기·PR 열림은 다시 켜도 그대로."""
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = DevService(gate=_gate, relater=None)
    queued = svc.state.tasks[0]
    opened = queued.model_copy(update={"id": "T-SUB2"})
    svc.state.tasks.append(opened)
    queued.coding = CodingRun(status="queued", updated_at=svc.clock(), mode="subscription")
    _with_open_pr(svc, opened, "opened", mode="subscription")
    svc._save()
    again = DevService(gate=_gate, relater=None)
    by = {t.id: t.coding for t in again.state.tasks}
    assert by[queued.id].status == "queued" and by[queued.id].error is None
    assert by[opened.id].status == "opened"


def test_reset_overwrites_saved_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = PlanningService(gate=_gate, relater=None)
    rid = _ticket(svc, "지울 요청", "k3").output["request_id"]
    svc.reset()
    again = PlanningService(gate=_gate, relater=None)
    assert not any(r.id == rid for r in again.state.requests)


def test_no_dir_keeps_memory_only(monkeypatch) -> None:
    monkeypatch.delenv("PLANNING_STATE_DIR", raising=False)
    svc = DevService(gate=_gate, relater=None)
    assert svc._state_path is None

"""상태 저장: 서버를 다시 켜도 기획·개발 기록과 중복 거르기 기록이 남는다."""

from __future__ import annotations

import asyncio
import os

from ax_contracts.schemas import ExecuteDecision
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
    svc = PlanningService(gate=_gate, relater=None, role="dev")
    out = _ticket(svc, "공지 배너 문구 오타 수정", "k1")
    rid = out.output["request_id"]
    assert os.path.exists(tmp_path / "planning_state_dev.json")

    again = PlanningService(gate=_gate, relater=None, role="dev")  # 다시 켬
    assert any(r.id == rid for r in again.state.requests)
    # 같은 키로 다시 와도 새로 만들지 않는다 (중복 거르기 기록도 남음)
    before = len(again.state.requests)
    _ticket(again, "공지 배너 문구 오타 수정", "k1")
    assert len(again.state.requests) == before


def test_roles_use_separate_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    _ticket(PlanningService(gate=_gate, relater=None, role="planning"), "기획 쪽 요청", "k2")
    dev = PlanningService(gate=_gate, relater=None, role="dev")
    assert not any(r.title == "기획 쪽 요청" for r in dev.state.requests)


def test_running_coding_becomes_failed_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = PlanningService(gate=_gate, relater=None, role="dev")
    task = svc.state.tasks[0]
    task.coding = CodingRun(status="writing", updated_at=svc.clock(), waiting_for=["T-x"])
    svc._save()
    again = PlanningService(gate=_gate, relater=None, role="dev")
    cr = next(t for t in again.state.tasks if t.id == task.id).coding
    assert cr.status == "failed" and "다시 켜서" in cr.error and cr.waiting_for == []


def test_reset_overwrites_saved_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = PlanningService(gate=_gate, relater=None, role="dev")
    rid = _ticket(svc, "지울 요청", "k3").output["request_id"]
    svc.reset()
    again = PlanningService(gate=_gate, relater=None, role="dev")
    assert not any(r.id == rid for r in again.state.requests)


def test_no_dir_keeps_memory_only(monkeypatch) -> None:
    monkeypatch.delenv("PLANNING_STATE_DIR", raising=False)
    svc = PlanningService(gate=_gate, relater=None, role="dev")
    assert svc._state_path is None

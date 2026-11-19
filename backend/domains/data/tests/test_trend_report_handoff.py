"""v3 B: 보고서 인사이트를 기획에 보내기 (trend_report_handoff) · 목록 has_insight·handoff.

safety 관문·기획 호출은 가짜. 실제 safety는 registry.toml에 send_trend_report_insight가 없으면
`held`(unregistered_action)로 실행하지 않는다 — 그때 버튼은 보내지 않고 남기지 않는다(아래 고정).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.services import BY_ID
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import TrendReportHandoffOutput, TrendReportInput
from ax_contracts.schemas.execute import ExecuteDecision
from data_agent import handlers
from data_agent.skills import trend_report_handoff as th

_SPEC = importlib.util.spec_from_file_location(
    "trend_report_fakes_b", Path(__file__).with_name("test_trend_report.py"))
assert _SPEC is not None and _SPEC.loader is not None
fakes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fakes)
NOW, _service, _filled, _report = fakes.NOW, fakes._service, fakes._filled, fakes._report  # noqa: SLF001


class Planning:
    def __init__(self, outcome: str = "created", status: TaskStatus = TaskStatus.COMPLETED) -> None:
        self.calls: list[AgentRequest] = []
        self.outcome, self.status = outcome, status

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "planning" and request.skill == "receive_insight"
        self.calls.append(request)
        out = {"outcome": self.outcome, "proposal_id": "PROP-7"}
        return AgentResult(meta=request.meta, status=self.status, outcome="x", output=out)


def _gate(status: str = "executed") -> Any:
    seen: list[tuple[str, dict[str, Any], RequestMeta]] = []

    async def gate(action: str, params: dict[str, Any], meta: RequestMeta, evidence: Any) -> Any:
        seen.append((action, params, meta))
        held = {"hold_reason": "unregistered_action"} if status == "held" else {}
        return ExecuteDecision(execution_id="exe_t", grade="A", status=status, **held)  # type: ignore[arg-type]

    gate.seen = seen  # type: ignore[attr-defined]
    return gate


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planning: Planning,
           gate: Any) -> tuple[Any, str]:  # fmt: skip
    service = _service(tmp_path, [])
    report = _filled(tmp_path)
    service.store.create(report.report_id, "h", NOW.date(), {}, "human", None,
                         report.model_dump(mode="json"), NOW)  # fmt: skip
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)
    monkeypatch.setattr(handlers, "get_trend_report_sender", lambda: planning)
    monkeypatch.setattr(handlers, "get_trend_report_gate", lambda: gate)
    return service, report.report_id


def _call(skill: str, data: dict[str, Any]) -> Any:
    meta = RequestMeta(context_id="ctx_ui", token="x", requester="dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return asyncio.run(handlers.HANDLERS[skill](AgentRequest(meta=meta, skill=skill, input=data)))


def test_send_created_is_saved_and_second_press_is_duplicate_without_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planning, gate = Planning(), _gate()
    service, rid = _setup(tmp_path, monkeypatch, planning, gate)
    got = _call("trend_report_handoff", {"report_id": rid})
    out = TrendReportHandoffOutput.model_validate(got.output)
    assert got.status == TaskStatus.COMPLETED and out.outcome == "created"
    assert out.proposal_id == "PROP-7" and out.sent_at is not None and out.by == "dashboard-api"
    sent = planning.calls[0]
    assert sent.input["insight_id"] == f"trend_report:{rid}"
    assert sent.meta.idempotency_key == f"{rid}:insight" and sent.meta.requester == "data"
    action, params, _ = gate.seen[0]
    assert action == th.ACTION == "send_trend_report_insight" and params["report_id"] == rid
    assert service.store.handoff(rid)["outcome"] == "created"
    again = _call("trend_report_handoff", {"report_id": rid})
    assert again.output["outcome"] == "duplicate" and len(planning.calls) == 1  # 기획 호출 0
    assert again.output["proposal_id"] == "PROP-7"


def test_planning_duplicate_is_kept_as_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """총괄이 먼저 보냈으면(같은 insight_id) 기획이 duplicate로 받는다."""
    service, rid = _setup(tmp_path, monkeypatch, Planning("duplicate"), _gate())
    got = _call("trend_report_handoff", {"report_id": rid})
    assert got.output["outcome"] == "duplicate"
    assert service.store.handoff(rid)["outcome"] == "duplicate"


@pytest.mark.parametrize("status", ["held", "rejected"])
def test_safety_not_executed_saves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           status: str) -> None:  # fmt: skip
    """registry.toml에 send_trend_report_insight가 없으면 safety가 held(unregistered_action)."""
    planning = Planning()
    service, rid = _setup(tmp_path, monkeypatch, planning, _gate(status))
    got = _call("trend_report_handoff", {"report_id": rid})
    assert got.status == TaskStatus.FAILED and got.output["outcome"] == "rejected"
    assert got.output["reason"] == f"safety가 실행하지 않음 ({status})"
    assert planning.calls == [] and service.store.handoff(rid) is None  # 다시 누를 수 있다


def test_planning_failure_saves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failing = Planning(status=TaskStatus.FAILED)
    service, rid = _setup(tmp_path, monkeypatch, failing, _gate())
    got = _call("trend_report_handoff", {"report_id": rid})
    assert got.status == TaskStatus.FAILED and got.output["outcome"] == "rejected"
    assert "기획에 보내지 못함" in got.output["reason"] and service.store.handoff(rid) is None


def test_no_insight_is_not_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    planning, gate = Planning(), _gate()
    service, _ = _setup(tmp_path, monkeypatch, planning, gate)
    empty = _report(TrendReportInput(preset="eco_beauty"))
    empty.report_id, empty.status = "tr_empty0001", "failed"
    service.store.create(empty.report_id, "h2", NOW.date(), {}, "human", None,
                         empty.model_dump(mode="json"), NOW)  # fmt: skip
    got = _call("trend_report_handoff", {"report_id": empty.report_id})
    assert got.output["outcome"] == "not_sent" and got.output["reason"]
    missing = _call("trend_report_handoff", {"report_id": "tr_none"})
    assert missing.output["outcome"] == "not_sent" and missing.output["reason"] == "보고서 없음"
    assert planning.calls == [] and gate.seen == []


def test_list_has_insight_and_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, rid = _setup(tmp_path, monkeypatch, Planning(), _gate())
    before = {i["report_id"]: i for i in _call("trend_report_list", {}).output["items"]}
    assert before[rid]["has_insight"] is True and before[rid]["handoff"] is None
    _call("trend_report_handoff", {"report_id": rid})
    after = {i["report_id"]: i for i in _call("trend_report_list", {}).output["items"]}
    assert after[rid]["handoff"]["outcome"] == "created"
    assert after[rid]["handoff"]["proposal_id"] == "PROP-7"


def test_registration() -> None:
    for skill in ("trend_report_handoff", "trend_report_insight"):
        assert skill in BY_ID["data"].skills and skill in handlers.HANDLERS
    for card in ("domains/data/agent-card.json", "services/data/agent-card.json"):
        raw = json.loads((Path(__file__).parents[3] / card).read_text(encoding="utf-8"))
        skills = {s["id"]: s for s in raw["skills"]}
        hand = skills["trend_report_handoff"]
        assert hand["grade"] == "A"
        assert set(hand["allowed_requesters"]) == {"dashboard-api", "orchestrator"}
        assert "dashboard-api" in skills["trend_report_insight"]["allowed_requesters"]

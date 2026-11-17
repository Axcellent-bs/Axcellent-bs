"""총괄이 진단(`diagnosed`)을 해당 에이전트에 개선 노트로 전달하고, 사람이 철회한다."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.config import set_agent_name
from ax_contracts.schemas.diagnosis import (
    DiagnoseAgentIssueInput,
    ReceiveImprovementNoteInput,
    ReceiveImprovementNoteOutput,
)
from ax_orchestrator.agent_diagnoses import AgentDiagnoses
from ax_orchestrator.diagnosis_notes import build_note, send_note_via_agent
from ax_orchestrator.main import app, state
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.models import Incident, StepRun
from ax_orchestrator.plans.scenario_a import scenario_a

NOW = "2026-11-17T03:00:00Z"
HUMAN = {"Authorization": "Bearer human:junha"}  # 헤더는 영어만


def _ev(name: str) -> dict:
    return {
        "context_id": f"ctx_inc_{name}",
        "title": f"이슈 {name}",
        "verdict": "check",
        "work_seconds": 41.0,
        "attempts": 2,
        "limit_seconds": 30.0,
        "reason": "다시 시도 1회",
        "at": NOW,
    }


def _request(agent: str = "infra", step: str = "rollback", names: tuple[str, ...] = ("a", "b")):
    return DiagnoseAgentIssueInput.model_validate(
        {
            "agent": agent,
            "step_id": step,
            "step_label": "문제 배포 되돌리기",
            "window": 3,
            "flagged": len(names),
            "criteria_version": 3,
            "evidence": [_ev(n) for n in names],
            "requested_at": NOW,
        }
    )


def _incidents(same_type: bool = True, names: str = "ab") -> dict[str, Incident]:
    out = {}
    for name in names:
        inc = Incident(
            context_id=f"ctx_inc_{name}",
            kind="incident",
            title=f"이슈 {name}",
            component="payment",
            event_type="payment:error_rate_5xx" if same_type or name == names[0] else name,
        )
        inc.steps["rollback"] = StepRun(
            step_id="rollback", skill="rollback_deployment", label="되돌리기", dept="infra"
        )
        out[inc.context_id] = inc
    return out


class FakeAgent:
    """에이전트 대신 응답하는 가짜. 받은 노트를 적어 둔다."""

    def __init__(self, status: str | None = "accepted", reason: str | None = None) -> None:
        self.status = status
        self.reason = reason
        self.calls: list[ReceiveImprovementNoteInput] = []

    async def __call__(self, note: ReceiveImprovementNoteInput, request_context: str):
        self.calls.append(note)
        if self.status is None:
            return None
        return ReceiveImprovementNoteOutput(
            note_id=note.note_id,
            status="revoked" if note.revoke else self.status,
            reason=self.reason,
        )


async def _diagnosed(diagnoses: AgentDiagnoses, **kw) -> str:
    out = diagnoses.receive(_request(**kw))
    await diagnoses.diagnose(out.request_id, _incidents())
    return out.request_id


@pytest.mark.asyncio
async def test_a_diagnosed_request_is_sent_to_the_agent_as_a_note() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    rid = await _diagnosed(diagnoses)
    row = diagnoses.get(rid)
    assert row["status"] == "diagnosed"
    assert row["delivery"]["status"] == "accepted" and row["delivery"]["attempts"] == 1
    (note,) = agent.calls
    assert (note.note_id, note.diagnosis_id, note.agent, note.step_id) == (
        "note-diag-1",
        "diag-1",
        "infra",
        "rollback",
    )
    assert "같은 유형의 이벤트(payment:error_rate_5xx)" in note.summary
    assert note.evidence_contexts == ["ctx_inc_a", "ctx_inc_b"]
    assert note.method == "rules" and note.revoke is False


@pytest.mark.asyncio
async def test_an_insufficient_diagnosis_is_sent_marked_as_cause_unknown() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    out = diagnoses.receive(_request())
    await diagnoses.diagnose(out.request_id, _incidents(same_type=False))  # 서로 다른 유형
    row = diagnoses.get(out.request_id)
    assert row["status"] == "insufficient"
    assert row["delivery"]["status"] == "accepted"
    (note,) = agent.calls
    assert note.cause_known is False and "원인은 말하지 않아요" in note.summary


@pytest.mark.asyncio
async def test_a_diagnosed_one_is_marked_cause_known() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    await _diagnosed(diagnoses)
    assert agent.calls[0].cause_known is True


@pytest.mark.asyncio
async def test_cause_unknown_notes_for_one_step_go_out_once_in_seven_days() -> None:
    day1 = datetime(2026, 11, 17, 3, tzinfo=UTC)
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    first = diagnoses.receive(_request(names=("a", "b")), now=day1)
    await diagnoses.diagnose(first.request_id, _incidents(False, "ab"))
    # 다음 날 새 근거 2건으로 같은 단계가 다시 올라와도 원인 미확정 노트는 쉰다
    second = diagnoses.receive(_request(names=("c", "d")), now=day1 + timedelta(days=1))
    assert second.status == "received"
    await diagnoses.diagnose(second.request_id, _incidents(False, "cd"))
    row = diagnoses.get(second.request_id)
    assert row["status"] == "insufficient"
    assert row["delivery"]["status"] == "skipped" and "7일에 한 번" in row["delivery"]["reason"]
    assert len(agent.calls) == 1
    # 7일이 지나면 다시 보낸다
    diagnoses.get(first.request_id)["delivery"]["delivered_at"] = (
        datetime.now(UTC) - timedelta(days=8)
    ).isoformat()
    again = await diagnoses.deliver(second.request_id)
    assert again["status"] == "accepted" and len(agent.calls) == 2


@pytest.mark.asyncio
async def test_the_cooldown_does_not_hold_back_a_cause_known_note() -> None:
    day1 = datetime(2026, 11, 17, 3, tzinfo=UTC)
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    first = diagnoses.receive(_request(names=("a", "b")), now=day1)
    await diagnoses.diagnose(first.request_id, _incidents(False, "ab"))
    second = diagnoses.receive(_request(names=("c", "d")), now=day1 + timedelta(days=1))
    await diagnoses.diagnose(second.request_id, _incidents(True, "cd"))  # 원인 확정
    assert diagnoses.get(second.request_id)["delivery"]["status"] == "accepted"
    assert [c.cause_known for c in agent.calls] == [False, True]


@pytest.mark.asyncio
async def test_a_failed_or_revoked_unknown_note_does_not_start_the_cooldown() -> None:
    day1 = datetime(2026, 11, 17, 3, tzinfo=UTC)
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent(None)  # 못 보냄
    first = diagnoses.receive(_request(names=("a", "b")), now=day1)
    await diagnoses.diagnose(first.request_id, _incidents(False, "ab"))
    assert diagnoses.get(first.request_id)["delivery"]["status"] == "failed"
    agent.status = "accepted"
    second = diagnoses.receive(_request(names=("c", "d")), now=day1 + timedelta(days=1))
    await diagnoses.diagnose(second.request_id, _incidents(False, "cd"))
    assert diagnoses.get(second.request_id)["delivery"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_without_a_sender_nothing_is_sent_and_the_diagnosis_stays() -> None:
    diagnoses = AgentDiagnoses()
    rid = await _diagnosed(diagnoses)
    row = diagnoses.get(rid)
    assert row["status"] == "diagnosed" and "delivery" not in row
    assert await diagnoses.deliver(rid) is None


@pytest.mark.asyncio
async def test_a_rejection_keeps_the_reason() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = FakeAgent("rejected", "이 노트는 data의 일이에요")
    rid = await _diagnosed(diagnoses)
    delivery = diagnoses.get(rid)["delivery"]
    assert delivery["status"] == "rejected" and "data의 일" in delivery["reason"]


@pytest.mark.asyncio
async def test_an_unreachable_agent_is_recorded_as_failed_and_can_be_retried() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent(None)
    rid = await _diagnosed(diagnoses)
    row = diagnoses.get(rid)
    assert row["status"] == "diagnosed"  # 진단은 그대로
    assert row["delivery"]["status"] == "failed" and row["delivery"]["attempts"] == 1
    agent.status = "accepted"  # 에이전트가 돌아왔다
    delivery = await diagnoses.deliver(rid)
    assert delivery["status"] == "accepted" and delivery["attempts"] == 2


@pytest.mark.asyncio
async def test_a_delivered_note_is_not_sent_twice() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    rid = await _diagnosed(diagnoses)
    assert await diagnoses.deliver(rid) is None
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_revoking_tells_the_agent_and_stops_any_resend() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    rid = await _diagnosed(diagnoses)
    delivery = await diagnoses.revoke(rid)
    assert delivery["status"] == "revoked" and delivery["revoked_at"]
    assert [c.revoke for c in agent.calls] == [False, True]
    assert agent.calls[1].note_id == agent.calls[0].note_id
    assert await diagnoses.deliver(rid) is None  # 철회한 건은 다시 보내지 않는다
    assert (await diagnoses.revoke(rid))["status"] == "revoked"
    assert len(agent.calls) == 2


@pytest.mark.asyncio
async def test_revoking_before_delivery_just_records_it() -> None:
    diagnoses = AgentDiagnoses()
    rid = await _diagnosed(diagnoses)  # 보내는 장치가 없어 전달 전
    delivery = await diagnoses.revoke(rid)
    assert delivery["status"] == "revoked"
    agent = FakeAgent()
    diagnoses.note_sender = agent
    assert await diagnoses.deliver(rid) is None and agent.calls == []


@pytest.mark.asyncio
async def test_revoking_when_the_agent_is_unreachable_still_records_it() -> None:
    diagnoses = AgentDiagnoses()
    diagnoses.note_sender = agent = FakeAgent()
    rid = await _diagnosed(diagnoses)
    agent.status = None
    delivery = await diagnoses.revoke(rid)
    assert delivery["status"] == "revoked" and "닿지 않아" in delivery["reason"]


@pytest.mark.asyncio
async def test_revoking_something_that_is_not_diagnosed_does_nothing() -> None:
    diagnoses = AgentDiagnoses()
    out = diagnoses.receive(_request())
    assert await diagnoses.revoke(out.request_id) is None  # 아직 접수 상태
    assert await diagnoses.revoke("diag-99") is None


@pytest.mark.asyncio
async def test_delivery_state_survives_a_restart(tmp_path) -> None:
    from ax_orchestrator.store import IncidentStore

    store = IncidentStore(str(tmp_path / "orch.db"))
    first = AgentDiagnoses(store)
    first.note_sender = FakeAgent()
    rid = await _diagnosed(first)
    store.close()
    again = AgentDiagnoses(IncidentStore(str(tmp_path / "orch.db")))
    again.restore()
    assert again.get(rid)["delivery"]["status"] == "accepted"


def test_the_note_id_follows_the_diagnosis_and_contexts_are_unique() -> None:
    row = {
        "request_id": "diag-7",
        "agent": "data",
        "step_id": "root_cause",
        "step_label": "원인 후보 추정",
        "status": "diagnosed",
        "diagnosis": {
            "summary": "요약",
            "method": "rules",
            "findings": [{"text": "A"}, {"text": "B"}],
            "items": [{"context_id": "x"}, {"context_id": "x"}, {"context_id": "y"}],
        },
    }
    note = build_note(row)
    assert note.note_id == "note-diag-7" and note.findings == ["A", "B"]
    assert note.evidence_contexts == ["x", "y"] and note.cause_known is True
    assert build_note({**row, "status": "insufficient"}).cause_known is False
    assert note.issued_at.tzinfo is not None and note.issued_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_sender_uses_the_department_name_and_a_fresh_path(monkeypatch) -> None:
    from ax_orchestrator import diagnosis_notes as module

    seen: dict = {}

    class _Result:
        status = module.TaskStatus.COMPLETED
        output = {"note_id": "note-diag-1", "status": "accepted", "reason": None}

    async def fake_call(target, request):  # noqa: ANN001, ANN202
        seen.update(target=target, skill=request.skill, path=request.meta.path)
        seen["key"] = request.meta.idempotency_key
        return _Result()

    monkeypatch.setattr(module, "call_agent", fake_call)
    diagnoses = AgentDiagnoses()
    rid = await _diagnosed(diagnoses)
    out = await send_note_via_agent(build_note(diagnoses.get(rid)), rid)
    assert out is not None and out.status == "accepted"
    assert seen == {
        "target": "infra",
        "skill": "receive_improvement_note",
        "path": [],
        "key": "improve:infra:rollback:diag-1",
    }
    revoked = await send_note_via_agent(build_note(diagnoses.get(rid), revoke=True), rid)
    assert revoked is not None and seen["key"].endswith(":revoke")


@pytest.mark.asyncio
async def test_sender_returns_none_when_the_call_fails(monkeypatch) -> None:
    from ax_orchestrator import diagnosis_notes as module

    async def boom(target, request):  # noqa: ANN001, ANN202
        raise ConnectionError("꺼짐")

    monkeypatch.setattr(module, "call_agent", boom)
    diagnoses = AgentDiagnoses()
    rid = await _diagnosed(diagnoses)
    assert await send_note_via_agent(build_note(diagnoses.get(rid)), rid) is None


# ---- 철회 API ----------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    monkeypatch.setattr(state, "manager", manager)
    yield TestClient(app), manager
    set_agent_name("orchestrator")


@pytest.mark.asyncio
async def test_revoke_endpoint_needs_a_human_and_revokes(client) -> None:
    http, manager = client
    manager.agent_diagnoses.note_sender = FakeAgent()
    rid = await _diagnosed(manager.agent_diagnoses)
    assert http.post(f"/api/agent-diagnoses/{rid}/revoke").status_code == 401  # 토큰 없음
    ok = http.post(f"/api/agent-diagnoses/{rid}/revoke", headers=HUMAN)
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "revoked"
    gone = http.post("/api/agent-diagnoses/diag-99/revoke", headers=HUMAN)
    assert gone.status_code == 404


def test_the_note_carries_the_skill_the_step_calls() -> None:
    from ax_orchestrator.diagnosis_notes import skill_of

    assert skill_of("rollback") == "rollback_deployment"
    assert skill_of("root_cause") == "root_cause_hypotheses"
    assert skill_of("late_compensation") == "propose_incident_compensation"
    assert skill_of("no_such_step") is None
    row = {
        "request_id": "diag-8",
        "agent": "infra",
        "step_id": "rollback",
        "step_label": "문제 배포 되돌리기",
        "status": "diagnosed",
        "diagnosis": {"summary": "요약", "findings": [], "items": [{"context_id": "x"}]},
    }
    assert build_note(row).skill == "rollback_deployment"

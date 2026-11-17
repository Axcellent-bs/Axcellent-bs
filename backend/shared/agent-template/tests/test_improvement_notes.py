"""공통 skill `receive_improvement_note`: 카드에 적기만 하면 핸들러가 붙는다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import improvement_notes
from ax_agent_template.improvement_notes import ImprovementNoteStore
from ax_agent_template.server import create_agent_app
from ax_agent_template.services import SERVICES, route_target
from ax_contracts.schemas import AgentCard

SCHEMAS = "shared/contracts/src/ax_contracts/schemas/diagnosis.py"
CARD = AgentCard.model_validate(
    {
        "name": "incident",  # 인프라 부서의 서비스 id
        "owner": "test",
        "description": "인프라 서비스",
        "url": "http://incident:8107/a2a",
        "skills": [
            {
                "id": "receive_improvement_note",
                "description": "총괄의 개선 노트를 받아 저장한다. 에이전트의 동작을 바꾸지 않는다.",
                "input_schema": f"{SCHEMAS}#ReceiveImprovementNoteInput",
                "output_schema": f"{SCHEMAS}#ReceiveImprovementNoteOutput",
                "grade": "A",
                "allowed_requesters": ["orchestrator"],
                "priority": "recommended",
            }
        ],
    }
)


def _note(**kw) -> dict:
    base = {
        "note_id": "note-diag-1",
        "diagnosis_id": "diag-1",
        "agent": "infra",
        "step_id": "rollback",
        "step_label": "문제 배포 되돌리기",
        "summary": "같은 유형의 이벤트에서 반복됐어요 (E2, E4).",
        "findings": ["같은 유형의 이벤트(payment:error_rate_5xx)에서 반복됐어요"],
        "evidence_contexts": ["ctx_inc_a", "ctx_inc_b"],
        "method": "rules",
        "issued_at": datetime.now(UTC).isoformat(),
    }
    return {**base, **kw}


def _post(client: TestClient, note: dict, token: str = "mock:orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_diag_1",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": f"k:{note['note_id']}:{note.get('revoke', False)}",
        },
        "skill": "receive_improvement_note",
        "input": note,
    }
    return client.post("/a2a", json=body).json()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(improvement_notes.NOTES_DIR_ENV, raising=False)
    improvement_notes.reset_stores()
    yield TestClient(create_agent_app(CARD, {}))  # 핸들러를 따로 주지 않는다
    improvement_notes.reset_stores()


def test_the_handler_is_attached_just_by_listing_the_skill(client: TestClient) -> None:
    res = _post(client, _note())
    assert (res["status"], res["outcome"]) == ("completed", "accepted")
    assert res["output"] == {"note_id": "note-diag-1", "status": "accepted", "reason": None}


def test_the_same_note_is_a_duplicate(client: TestClient) -> None:
    _post(client, _note())
    assert _post(client, _note())["outcome"] == "duplicate"
    assert len(improvement_notes.store_for("incident").active_notes()) == 1


def test_a_note_for_another_agent_is_rejected_with_a_reason(client: TestClient) -> None:
    res = _post(client, _note(agent="data"))
    assert (res["status"], res["outcome"]) == ("completed", "rejected")
    assert "data의 일" in res["output"]["reason"] and "infra의 것" in res["output"]["reason"]
    assert improvement_notes.store_for("incident").active_notes() == []


def test_revoking_hides_the_note_and_an_unknown_revoke_is_rejected(client: TestClient) -> None:
    _post(client, _note())
    assert _post(client, _note(revoke=True))["outcome"] == "revoked"
    assert improvement_notes.store_for("incident").active_notes() == []
    assert _post(client, _note(revoke=True))["outcome"] == "rejected"  # 이미 거둠
    assert _post(client, _note(note_id="note-none", revoke=True))["outcome"] == "rejected"


def test_only_the_orchestrator_may_send_notes(client: TestClient) -> None:
    res = _post(client, _note(), token="mock:data")
    assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")


def test_bad_input_is_refused(client: TestClient) -> None:
    res = _post(client, {"note_id": "x"})
    assert res["status"] == "failed" and res["outcome"] == "invalid_input"


def test_active_notes_filter_by_step_and_come_newest_first() -> None:
    store = ImprovementNoteStore()
    from ax_contracts.schemas.diagnosis import ReceiveImprovementNoteInput

    for n, step in (("1", "rollback"), ("2", "diagnose"), ("3", "rollback")):
        store.add(
            ReceiveImprovementNoteInput.model_validate(_note(note_id=f"note-{n}", step_id=step)),
            f"2026-11-17T0{n}:00:00+00:00",
        )
    # note-1은 같은 단계의 더 새로운 note-3으로 대체돼 참고 목록에서 빠진다
    assert [n["note_id"] for n in store.active_notes("rollback")] == ["note-3"]
    assert [n["note_id"] for n in store.active_notes(limit=2)] == ["note-3", "note-2"]


def test_notes_survive_a_restart_when_a_folder_is_given(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(improvement_notes.NOTES_DIR_ENV, str(tmp_path / "notes"))
    improvement_notes.reset_stores()
    client = TestClient(create_agent_app(CARD, {}))
    assert _post(client, _note())["outcome"] == "accepted"
    improvement_notes.reset_stores()  # 서버를 다시 켠 것처럼 저장소를 새로 연다
    again = TestClient(create_agent_app(CARD, {}))
    assert _post(again, _note())["outcome"] == "duplicate"
    assert (tmp_path / "notes" / "incident.db").is_file()
    improvement_notes.reset_stores()


@pytest.mark.parametrize(
    ("department", "service"),
    [
        ("infra", "incident"),
        ("data", "data"),
        ("dev", "dev"),
        ("support", "inquiry"),
        ("marketing", "coupon"),
        ("finance", "finance"),
        ("archive", "decisions"),
    ],
)
def test_every_evaluated_department_routes_the_skill_to_one_service(
    department: str, service: str
) -> None:
    assert route_target(department, "receive_improvement_note") == service
    owners = [
        s.id
        for s in SERVICES
        if "receive_improvement_note" in s.skills and s.department == department
    ]
    assert owners == [service]


def test_a_newer_note_for_the_same_step_replaces_the_older_one(client: TestClient) -> None:
    assert _post(client, _note(note_id="note-diag-1"))["outcome"] == "accepted"
    assert _post(client, _note(note_id="note-diag-2"))["outcome"] == "accepted"
    assert _post(client, _note(note_id="note-diag-3", step_id="diagnose"))["outcome"] == "accepted"
    store = improvement_notes.store_for("incident")
    assert sorted(n["note_id"] for n in store.active_notes()) == ["note-diag-2", "note-diag-3"]
    # 대체된 노트를 다시 보내도 새로 쌓이지 않고, 거둘 수도 없다
    assert _post(client, _note(note_id="note-diag-1"))["outcome"] == "duplicate"
    assert _post(client, _note(note_id="note-diag-1", revoke=True))["outcome"] == "rejected"


def test_notes_older_than_thirty_days_are_left_out_of_the_reference_list() -> None:
    from datetime import timedelta

    from ax_contracts.schemas.diagnosis import ReceiveImprovementNoteInput

    store = ImprovementNoteStore()
    base = datetime(2026, 11, 17, tzinfo=UTC)
    store.add(ReceiveImprovementNoteInput.model_validate(_note()), base.isoformat())
    assert len(store.active_notes(now=base + timedelta(days=29))) == 1
    assert store.active_notes(now=base + timedelta(days=31)) == []  # 기록은 남고 참고에서만 빠짐
    assert store.get("note-diag-1") is not None


def test_a_cause_unknown_note_is_kept_like_any_other(client: TestClient) -> None:
    res = _post(client, _note(cause_known=False, summary="원인은 말하지 않아요"))
    assert res["outcome"] == "accepted"
    (stored,) = improvement_notes.store_for("incident").active_notes()
    assert stored["cause_known"] is False

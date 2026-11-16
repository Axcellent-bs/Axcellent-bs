"""대시보드 인사 화면용 조회 skill과 사람 입력 skill(온보딩 단계 완료, 후보 카드 승인·거절)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import (
    ListHandoversOutput,
    ListOpenQuestionsOutput,
)
from hr_agent.main import app


def body(skill, inp, key="k", ctx="ctx_dv_0001", token="mock:dashboard-api"):
    return {
        "meta": {
            "context_id": ctx,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": inp,
    }


@pytest.fixture
def client(svc) -> TestClient:
    return TestClient(app)


def post(client, skill, inp, key="k", ctx="ctx_dv_0001", token="mock:dashboard-api"):
    return client.post("/a2a", json=body(skill, inp, key, ctx, token)).json()


def start_handover(client, owner="emp_01", successor="emp_02", ctx="ctx_hnd_0001"):
    return post(
        client,
        "start_handover",
        {"owner_id": owner, "successor_id": successor},
        "s",
        ctx,
        "mock:orchestrator",
    )


# ---- 인수인계·질문 -------------------------------------------------------------------------
def test_list_handovers_shows_status_completeness_items_and_open_questions(svc, client) -> None:
    start_handover(client)
    start_handover(client, owner="emp_03", successor="emp_04", ctx="ctx_hnd_0002")
    res = post(client, "list_handovers", {})
    out = ListHandoversOutput.model_validate(res["output"])
    assert res["status"] == "completed" and len(out.handovers) == 2
    first = next(h for h in out.handovers if h.owner_id == "emp_01")
    assert (first.status, first.completeness, first.area) == ("input_required", 40, "payment")
    assert {i.item_id for i in first.items} == {"pr_412", "tkt_201", "dep_102"}
    assert len(first.open_questions) == 3
    only = ListHandoversOutput.model_validate(
        post(client, "list_handovers", {"area": "search"})["output"]
    )
    assert [h.owner_id for h in only.handovers] == ["emp_03"]
    none = ListHandoversOutput.model_validate(
        post(client, "list_handovers", {"status": "confirmed"})["output"]
    )
    assert none.handovers == []


def test_open_questions_disappear_once_answered_or_confirmed(svc, client) -> None:
    started = start_handover(client)
    questions = started["output"]["questions"]
    listed = ListOpenQuestionsOutput.model_validate(
        post(client, "list_open_questions", {})["output"]
    )
    assert len(listed.questions) == 3
    post(
        client,
        "answer_handover_question",
        {"question_id": questions[0]["question_id"], "answer": "답"},
        "a1",
    )
    after = ListOpenQuestionsOutput.model_validate(
        post(client, "list_open_questions", {"assignee_id": "emp_01"})["output"]
    )
    assert len(after.questions) == 2
    other = ListOpenQuestionsOutput.model_validate(
        post(client, "list_open_questions", {"assignee_id": "emp_09"})["output"]
    )
    assert other.questions == []


def test_dashboard_can_answer_a_question_like_the_contract_says(svc, client) -> None:
    started = start_handover(client)
    q = started["output"]["questions"][0]["question_id"]
    res = post(client, "answer_handover_question", {"question_id": q, "answer": "답변"}, "a")
    assert res["status"] == "completed" and res["output"]["completeness"] == 60


# ---- 온보딩 --------------------------------------------------------------------------------


# ---- 변경 요청 -----------------------------------------------------------------------------


# ---- 권한 ----------------------------------------------------------------------------------


def test_dashboard_can_read_and_answer_but_not_run_confirmations(svc, client) -> None:
    for skill, inp in [
        ("list_handovers", {}),
        ("get_roster", {}),
    ]:
        assert post(client, skill, inp)["status"] == "completed", skill
    for skill, inp in [
        ("confirm_handover", {"handover_id": "x"}),
        ("start_handover", {"owner_id": "emp_01"}),
    ]:
        res = post(client, skill, inp)
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL"), skill
    stranger = post(client, "list_handovers", {}, token="mock:stranger")
    assert stranger["outcome"] == "FORBIDDEN_SKILL"


def test_read_views_do_not_use_the_gate_or_change_anything(svc, client) -> None:
    start_handover(client)
    calls = list(svc.gate.calls)
    for skill, inp in [
        ("list_handovers", {}),
        ("list_open_questions", {}),
    ]:
        assert post(client, skill, inp)["status"] == "completed"
    assert svc.gate.calls == calls

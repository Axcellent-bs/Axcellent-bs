"""독립 리뷰에서 나온 버그의 회귀 테스트: 키 재사용, 비활성 후임, 잠금, 트랜잭션, 교대, 충돌."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import RequestMeta
from hr_agent.main import app
from hr_agent.results import make_evidence
from hr_agent.roster import store as roster


def body(skill, inp, key="k", ctx="ctx_rev_0001", token="mock:orchestrator"):
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


def post(client, skill, inp, key="k", ctx="ctx_rev_0001", token="mock:orchestrator"):
    return client.post("/a2a", json=body(skill, inp, key, ctx, token)).json()


# 1) 같은 멱등성 키를 다른 대상에 쓰면 이미 받은 승인이 따라가면 안 된다 --------------------


@pytest.mark.asyncio
async def test_gate_refuses_a_saved_key_reused_for_another_target(svc) -> None:
    meta = RequestMeta(
        context_id="ctx_g",
        token="t",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        idempotency_key="K",
    )
    ev = make_evidence(svc.clock, "x")
    first = await svc.gate.execute("start_handover", {"target": "h1"}, meta, ev)
    again = await svc.gate.execute("start_handover", {"target": "h1"}, meta, ev)
    other = await svc.gate.execute("start_handover", {"target": "h2"}, meta, ev)
    assert first.status == again.status == "executed"
    assert other.status == "rejected"


# 2) 퇴직한 사람은 후임이 될 수 없다 -----------------------------------------------------


# 3) 퇴직 보류가 거절·만료되면 잠금과 상태가 정리된다 -----------------------------------


# 5) 적용 도중 오류가 나면 명부가 반쯤 바뀌지 않는다 --------------------------------------


# 6) 동행 요청(인수인계)이 실패하면 변경도 잠금도 남지 않는다 ----------------------------


# 7) 퇴직·이동한 사람의 교대는 공백 계산과 타이머에 세지 않는다 --------------------------


def test_shifts_of_people_no_longer_in_the_area_are_ignored(svc) -> None:
    roster.set_active(svc.store, "catalog", "owner", "emp_05", False)
    assert roster.shifts(svc.store, "catalog") == []


# 8) 사람이 보류를 거절해도 '항목 누락'으로 보고하지 않는다 -------------------------------
def test_a_human_rejection_is_not_reported_as_missing_items(svc, client) -> None:
    start = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    hid = start["output"]["handover_id"]
    held = post(client, "confirm_handover", {"handover_id": hid}, "c")
    assert held["status"] == "input-required" and held["reason_code"] == "human_input"
    svc.gate.decide_hold(held["output"]["hold_id"], approved=False)
    res = post(client, "confirm_handover", {"handover_id": hid}, "c")
    assert res["output"]["status"] == "rejected" and res["output"]["missing_item_ids"] == []


def test_more_answers_open_a_fresh_confirmation_attempt(svc, client) -> None:
    start = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    hid = start["output"]["handover_id"]
    held = post(client, "confirm_handover", {"handover_id": hid}, "c")
    svc.gate.decide_hold(held["output"]["hold_id"], approved=False)
    for q in start["output"]["questions"]:
        post(
            client,
            "answer_handover_question",
            {"question_id": q["question_id"], "answer": "답"},
            "a" + q["gap_field"],
        )
    res = post(client, "confirm_handover", {"handover_id": hid}, "c")
    assert res["output"]["status"] == "confirmed"  # 같은 키여도 완성도가 달라져 새로 판정한다


# 9) 같은 사건에서 내용이 다른 요청은 조용히 중복 처리하지 않는다 --------------------------


def test_a_different_answer_to_an_answered_question_is_a_conflict(svc, client) -> None:
    start = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    q = start["output"]["questions"][0]["question_id"]
    first = post(client, "answer_handover_question", {"question_id": q, "answer": "첫 답"}, "a1")
    same = post(client, "answer_handover_question", {"question_id": q, "answer": "첫 답"}, "a2")
    diff = post(client, "answer_handover_question", {"question_id": q, "answer": "다른 답"}, "a3")
    assert (first["outcome"], same["outcome"], diff["outcome"]) == (
        "answered",
        "duplicate",
        "CONFLICT",
    )


# ---- 인계자 질문 만료·재질문, 취소된 퇴직 다시 열기 -----------------------------------------
def test_unanswered_questions_are_asked_again_after_the_ttl(svc, client) -> None:
    start = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    first = {q["question_id"]: q["question"] for q in start["output"]["questions"]}
    answered = start["output"]["questions"][0]["question_id"]
    post(client, "answer_handover_question", {"question_id": answered, "answer": "답"}, "a")

    early = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    assert all("다시 묻습니다" not in q["question"] for q in early["output"]["questions"])

    svc.clock.advance(minutes=svc.settings.handover_question_ttl_minutes + 1)
    late = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    asked = {q["question_id"]: q["question"] for q in late["output"]["questions"]}
    assert answered not in asked  # 답한 질문은 다시 묻지 않는다
    assert all(text.startswith("(다시 묻습니다 2번째)") for text in asked.values())
    assert all(first[qid] in text for qid, text in asked.items())
    svc.clock.advance(minutes=svc.settings.handover_question_ttl_minutes + 1)
    third = post(client, "start_handover", {"owner_id": "emp_01"}, "s")
    assert all(
        q["question"].startswith("(다시 묻습니다 3번째)") for q in third["output"]["questions"]
    )
    assert "handover.question_reasked" in svc.audit.kinds("ctx_rev_0001")

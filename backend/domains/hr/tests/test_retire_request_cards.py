"""`retire_request_cards`: 기획이 요청을 거둬들이면 그 요청에서 나온 카드를 내린다."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import RetireRequestCardsOutput
from hr_agent.clients.memory import card_state
from hr_agent.main import app

BACKEND = Path(__file__).resolve().parents[3]
CARDS = [
    BACKEND / "domains" / "hr" / "agent-card.json",
    BACKEND / "services" / "decisions" / "agent-card.json",
]
REQ_CTX = "ctx_req_0032"


def post(client, skill, inp, key="k", ctx="ctx_rr_0001", token="mock:planning"):
    return client.post(
        "/a2a",
        json={
            "meta": {
                "context_id": ctx,
                "token": token,
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "idempotency_key": key,
            },
            "skill": skill,
            "input": inp,
        },
    ).json()


@pytest.fixture
def client(svc) -> TestClient:
    return TestClient(app)


def confirmed_card(client, ctx, summary, refs):
    """근거가 있는 확정 카드."""
    res = post(
        client,
        "record_case",
        {
            "context_id": ctx,
            "kind": "spec_confirmed",
            "summary": summary,
            "decision_reason": "이유",
            "alternatives_considered": ["없음"],
            "source_refs": refs,
            "author": "planning",
        },
        f"rc:{ctx}:{summary}",
        ctx,
    )
    assert res["status"] == "completed", res
    return res["output"]["card_id"]


def candidate_card(client, ctx, summary):
    """근거가 없어 후보로 남는 카드."""
    res = post(
        client,
        "record_case",
        {
            "context_id": ctx,
            "kind": "decision",
            "summary": summary,
            "area": "payment",
            "author": "planning",
        },
        f"cc:{ctx}:{summary}",
        ctx,
    )
    assert res["output"]["outcome"] == "candidate", res
    return res["output"]["card_id"]


async def state_of(svc, card_id: str) -> str:
    card = await svc.memory.get_card(card_id)
    assert card is not None
    return card_state(card)


def retire(client, key="retire-1", token="mock:planning", **override):
    inp = {
        "context_id": REQ_CTX,
        "request_id": "REQ-35",
        "spec_id": "SPEC-12",
        "reason": "요청을 거둬들임",
        **override,
    }
    return post(client, "retire_request_cards", inp, key, REQ_CTX, token)


@pytest.mark.asyncio
async def test_candidate_and_confirmed_cards_of_the_request_are_retired(svc, client) -> None:
    cand = candidate_card(client, REQ_CTX, "[기각한 안] 근거 없이 정한 결정")
    conf = confirmed_card(
        client, REQ_CTX, "결제 재시도를 확정했다", ["decision:D-0", "spec:SPEC-12"]
    )
    res = retire(client)
    assert res["status"] == "completed" and res["outcome"] == "retired", res
    out = RetireRequestCardsOutput.model_validate(res["output"])
    assert set(out.retired_card_ids) == {cand, conf}
    assert await state_of(svc, cand) == "retired"
    assert await state_of(svc, conf) == "retired"
    # 지우지 않는다: 카드는 그대로 조회된다
    assert await svc.memory.get_card(conf) is not None
    assert "card.retired_by_request" in svc.audit.kinds(REQ_CTX)


@pytest.mark.asyncio
async def test_cards_of_the_same_spec_from_another_context_are_retired(svc, client) -> None:
    other = confirmed_card(client, "ctx_spec_0099", "같은 기획서 카드", ["spec:SPEC-12"])
    by_request = confirmed_card(client, "ctx_spec_0100", "요청 번호로 묶인 카드", ["REQ-35"])
    res = retire(client)
    out = RetireRequestCardsOutput.model_validate(res["output"])
    assert set(out.retired_card_ids) == {other, by_request}


@pytest.mark.asyncio
async def test_unrelated_cards_are_left_alone(svc, client) -> None:
    mine = confirmed_card(client, REQ_CTX, "내릴 카드", ["spec:SPEC-12"])
    unrelated = confirmed_card(client, "ctx_req_0040", "다른 요청의 카드", ["spec:SPEC-40"])
    res = retire(client)
    assert RetireRequestCardsOutput.model_validate(res["output"]).retired_card_ids == [mine]
    assert await state_of(svc, unrelated) == "active"


@pytest.mark.asyncio
async def test_other_kinds_of_cards_are_not_touched(svc, client) -> None:
    incident = confirmed_card(client, REQ_CTX, "장애 사례처럼 보이는 카드", ["spec:SPEC-12"])
    card = await svc.memory.get_card(incident)
    assert card is not None
    # 같은 사건 번호라도 업무 결정·요청이 아닌 종류(장애 사례)는 대상이 아니다
    case = card.model_copy(update={"card_id": "card_case_x", "kind": "case"})
    await svc.memory.write_card(case)
    retire(client)
    assert await state_of(svc, "card_case_x") == "active"


@pytest.mark.asyncio
async def test_calling_again_changes_nothing_and_reports_the_same_cards(svc, client) -> None:
    conf = confirmed_card(client, REQ_CTX, "결제 재시도를 확정했다", ["spec:SPEC-12"])
    first = retire(client, key="retire-1")
    again = retire(client, key="retire-2")  # 다른 키로 다시 불러도
    assert first["output"]["retired_card_ids"] == [conf]
    assert again["outcome"] == "duplicate", again
    out = RetireRequestCardsOutput.model_validate(again["output"])
    assert out.retired_card_ids == [] and out.already_retired_card_ids == [conf]


@pytest.mark.asyncio
async def test_a_request_without_cards_returns_an_empty_list(svc, client) -> None:
    res = retire(client, context_id="ctx_req_0777", request_id="REQ-777", spec_id=None)
    assert res["status"] == "completed" and res["outcome"] == "none", res
    assert res["output"]["retired_card_ids"] == []


@pytest.mark.asyncio
async def test_retired_cards_are_no_longer_used_as_evidence(svc, client) -> None:
    conf = confirmed_card(client, REQ_CTX, "결제 재시도를 확정했다", ["spec:SPEC-12"])
    assert any(c.card_id == conf for c in await svc.memory.list_cards(states=["active"]))
    retire(client)
    assert all(c.card_id != conf for c in await svc.memory.list_cards(states=["active"]))


def test_only_planning_can_call_it(svc, client) -> None:
    for token in ("mock:dashboard-api", "mock:orchestrator", "mock:stranger"):
        res = retire(client, key=f"k-{token}", token=token)
        assert res["outcome"] == "FORBIDDEN_SKILL", (token, res)


def test_input_is_validated_and_a_key_is_required(svc, client) -> None:
    bad = post(client, "retire_request_cards", {"context_id": REQ_CTX}, "k1", REQ_CTX)
    assert bad["outcome"] == "INVALID_INPUT", bad
    no_reason = retire(client, key="k2", reason="")
    assert no_reason["outcome"] == "INVALID_INPUT", no_reason


@pytest.mark.parametrize("card", CARDS, ids=lambda p: p.parent.name)
def test_cards_list_the_skill_for_planning_only(card: Path) -> None:
    skills = json.loads(card.read_text("utf-8"))["skills"]
    skill = next(s for s in skills if s["id"] == "retire_request_cards")
    assert skill["grade"] == "A"
    assert skill["allowed_requesters"] == ["planning"]

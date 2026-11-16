"""안전규칙 AI 1:1 대화 (11/16): 기준·보류 기록을 facts로 읽고 설명만 한다."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_safety.chat import facts
from ax_safety.chat.threads import ChatStore
from ax_safety.chat.turn import ChatOut, ChatService
from ax_safety.gate import Gate
from ax_safety.main import create_app

HUMAN = {"Authorization": "Bearer human:taeyun"}
OTHER = {"Authorization": "Bearer human:junha"}


class FakeLLM:
    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    async def structured(self, system: str, schema: type, untrusted: dict[str, Any]) -> ChatOut:
        self.seen.append(untrusted)
        n = len(untrusted["facts"]["pending_reviews"])
        return ChatOut(reply=f"보류 {n}건이에요.", summary="보류 확인")


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def chat_client(gate: Gate, llm: FakeLLM) -> TestClient:
    store = ChatStore()
    service = ChatService(lambda: store, gate, llm=lambda: llm)
    return TestClient(create_app(gate, expire_interval=None, chat=service))


@pytest.mark.asyncio
async def test_facts_show_rules_and_why_a_review_is_held(gate: Gate, coupon) -> None:
    held = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="big"))
    got = facts.collect(gate)
    assert any(r["key"] == "coupon_total" for r in got["thresholds"]["rules"])
    assert any(a["action"] == "issue_coupon" for a in got["registry"])
    [review] = got["pending_reviews"]
    assert review["review_id"] == held.review_id
    assert review["requester"] == "marketing" and review["action_name"] == "쿠폰 지급"
    hit = {h["key"] for h in review["rules_hit"]}
    assert "coupon_total" in hit
    assert got["executions"]["by_status"] == {"held": 1}
    # 결정한 사람 이름은 사실에 넣지 않는다
    assert "decided_by" not in review


@pytest.mark.asyncio
async def test_send_answers_from_facts_and_others_see_only_a_summary(
    gate: Gate, coupon, chat_client: TestClient, llm: FakeLLM
) -> None:
    await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="big"))
    res = chat_client.post(
        "/api/safety/chat", json={"message": "지금 보류된 거 있어?"}, headers=HUMAN
    )
    assert res.status_code == 200, res.text
    thread = res.json()["data"]
    tid = thread["id"]
    assert thread["who"] == "taeyun"
    # TestClient는 요청마다 루프를 끝내므로 답이 붙었는지 다시 읽어 확인한다
    got = chat_client.get(f"/api/safety/chat/{tid}", headers=HUMAN).json()["data"]
    texts = [m["text"] for m in got["messages"]]
    assert texts[0] == "지금 보류된 거 있어?"
    if len(texts) > 1:
        assert texts[1] == "보류 1건이에요." and got["status"] == "talking"
    # 남의 대화는 원문 없이
    other = chat_client.get(f"/api/safety/chat/{tid}", headers=OTHER).json()["data"]
    assert other["restricted"] is True and other["messages"] == []
    # 남의 대화에는 못 쓴다
    res = chat_client.post(f"/api/safety/chat/{tid}/close", headers=OTHER)
    assert res.status_code == 403


def test_writing_needs_a_human_token(chat_client: TestClient) -> None:
    res = chat_client.post("/api/safety/chat", json={"message": "기준 알려줘"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "HUMAN_ONLY"
    agent = {"Authorization": "Bearer mock:marketing"}
    assert (
        chat_client.post("/api/safety/chat", json={"message": "x"}, headers=agent).status_code
        == 403
    )
    # 읽기는 토큰 없이도 된다 (목록)
    assert chat_client.get("/api/safety/chat").json()["data"] == []


def test_close_and_remove_own_thread(chat_client: TestClient) -> None:
    tid = chat_client.post(
        "/api/safety/chat", json={"message": "등급이 뭐야?"}, headers=HUMAN
    ).json()["data"]["id"]
    closed = chat_client.post(f"/api/safety/chat/{tid}/close", headers=HUMAN).json()["data"]
    assert closed["status"] == "closed"
    res = chat_client.post(
        "/api/safety/chat", json={"thread_id": tid, "message": "더"}, headers=HUMAN
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "THREAD_CLOSED"
    assert chat_client.post(f"/api/safety/chat/{tid}/remove", headers=HUMAN).json()["data"][
        "removed"
    ]
    assert chat_client.get(f"/api/safety/chat/{tid}", headers=HUMAN).status_code == 404


def test_no_llm_means_preparing(gate: Gate) -> None:
    service = ChatService(lambda: ChatStore(), gate, llm=lambda: None)
    client = TestClient(create_app(gate, expire_interval=None, chat=service))
    res = client.post("/api/safety/chat", json={"message": "안녕"}, headers=HUMAN)
    assert res.status_code == 409 and res.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_answer_runs_with_facts(gate: Gate, coupon, llm: FakeLLM) -> None:
    await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="big"))
    store = ChatStore()
    service = ChatService(lambda: store, gate, llm=lambda: llm)
    th = service.send("태윤", None, "왜 보류됐어?")
    await service.wait_idle()
    got = service.get("태윤", th["id"])
    assert [m["text"] for m in got["messages"]] == ["왜 보류됐어?", "보류 1건이에요."]
    assert llm.seen[0]["facts"]["pending_reviews"][0]["rules_hit"]

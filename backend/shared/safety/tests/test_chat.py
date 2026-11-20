"""안전 처리 로그 1:1 대화 (11/16): 기준·보류 기록을 facts로 읽고 설명만 한다."""

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


def test_detail_by_query_is_the_same_as_the_path(chat_client: TestClient) -> None:
    """251 nginx는 `chat` 주소에만 사람 토큰을 붙여서 화면은 상세를 `?thread_id=`로 읽는다."""
    res = chat_client.post("/api/safety/chat", json={"message": "등급이 뭐야?"}, headers=HUMAN)
    tid = res.json()["data"]["id"]
    by_query = chat_client.get(f"/api/safety/chat?thread_id={tid}", headers=HUMAN).json()["data"]
    assert by_query["id"] == tid and by_query["restricted"] is False
    assert by_query["messages"][0]["text"] == "등급이 뭐야?"
    assert chat_client.get("/api/safety/chat?thread_id=safetychat-99").status_code == 404


# ---- 단톡방(room_id)에서 만든 대화는 누구나 원문 (11/20) ----

URL = "/api/safety/chat"


def test_room_thread_is_readable_by_others_but_writes_stay_owner_only(
    chat_client: TestClient,
) -> None:
    th = chat_client.post(URL, json={"message": "보류 있어?", "room_id": "all"},
                          headers=HUMAN).json()["data"]  # fmt: skip
    assert th["room_id"] == "all" and th["shared"] is True and th["owner"] == "taeyun"
    for path in (f"{URL}/{th['id']}", f"{URL}?thread_id={th['id']}"):
        other = chat_client.get(path, headers=OTHER).json()["data"]
        assert other["restricted"] is False and other["messages"]
        assert other["shared"] is True and other["owner"] == "taeyun"
    # 다른 계정의 끝내기·지우기는 그대로 막힌다
    assert chat_client.post(f"{URL}/{th['id']}/close", headers=OTHER).status_code == 403
    assert chat_client.post(f"{URL}/{th['id']}/remove", headers=OTHER).status_code == 403
    # 지우면 숨기고, 숨긴 대화는 공유하지 않는다
    assert chat_client.post(f"{URL}/{th['id']}/remove", headers=HUMAN).status_code == 200
    assert chat_client.get(f"{URL}/{th['id']}", headers=OTHER).status_code == 404


def test_one_to_one_thread_is_still_hidden_and_room_id_on_reply_is_ignored(
    chat_client: TestClient,
) -> None:
    th = chat_client.post(URL, json={"message": "1:1 질문"}, headers=HUMAN).json()["data"]
    assert th["room_id"] is None and th["shared"] is False and th["owner"] is None
    other = chat_client.get(f"{URL}/{th['id']}", headers=OTHER).json()["data"]
    assert other["restricted"] is True and other["messages"] == [] and other["shared"] is False


@pytest.mark.asyncio
async def test_room_id_on_a_reply_is_ignored(gate: Gate, llm: FakeLLM) -> None:
    store = ChatStore()
    service = ChatService(lambda: store, gate, llm=lambda: llm)
    th = service.send("taeyun", None, "1:1 질문")
    await service.wait_idle()
    again = service.send("taeyun", th["id"], "더", "all")  # 이어 쓰기에서 준 room_id
    await service.wait_idle()
    assert store.get(th["id"])["room_id"] is None and again["shared"] is False
    assert service.get("junha", th["id"])["restricted"] is True


def test_room_id_must_be_1_to_64_chars(chat_client: TestClient) -> None:
    for bad in ("", "x" * 65, 3):
        res = chat_client.post(URL, json={"message": "안녕", "room_id": bad}, headers=HUMAN)
        assert res.status_code in (400, 422)


def test_old_db_without_room_column_is_upgraded(tmp_path: Any) -> None:
    import sqlite3

    path = str(tmp_path / "chat.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE safety_chat_threads (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
                 " title TEXT NOT NULL, status TEXT NOT NULL, who TEXT,"
                 " summary TEXT NOT NULL DEFAULT '', messages TEXT NOT NULL,"
                 " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")  # fmt: skip
    conn.execute("INSERT INTO safety_chat_threads VALUES ('safetychat-1', 1, '옛', 'talking',"
                 " 'taeyun', '', '[]', '2026-11-01', '2026-11-01')")  # fmt: skip
    conn.commit()
    conn.close()
    store = ChatStore(path)
    ChatStore(path)  # 두 번째 열기도 안전
    assert store.get("safetychat-1")["room_id"] is None

"""고객 아이디어 창구: 받기 규칙, 문의와 분리, 묶음 현황, 기획으로 넘기기, 영속 저장."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import handlers, ideas, persistence
from support_agent.ideas import IdeaStore
from support_agent.main import ALL_HANDLERS, app, load_card
from support_agent.store import InquiryStore

REQ = "REQ-7"
CTX = "ctx_req_idea000001"


class Sent(list[AgentRequest]):
    pass


@pytest.fixture(autouse=True)
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())  # 멱등 결과도 테스트마다 비운다
    monkeypatch.setattr(handlers, "IDEAS", IdeaStore())
    sent_log = Sent()

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        assert target == "planning" and req.skill == "create_ticket"
        sent_log.append(req)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome="created",
            output={"request_id": REQ, "context_id": CTX, "outcome": "created"},
        )

    monkeypatch.setattr(handlers, "SEND", fake_send)
    return sent_log


def call(
    skill: str,
    input: dict[str, Any] | None = None,
    untrusted: dict[str, Any] | None = None,
    *,
    requester: str = "dashboard-api",
    key: str | None = None,
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_shop_idea00000001",
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input or {},
        "untrusted": untrusted or {},
    }
    return TestClient(app).post("/a2a", json=body).json()


def submit(
    text: str, *, user_id: int = 1, kind: str = "event", key: str | None = None
) -> dict[str, Any]:
    return call(
        "submit_idea",
        {"kind": kind, "user_id": user_id},
        {"text": text},
        requester="shop-api",
        key=key or f"idea-{user_id}-{abs(hash(text))}",
    )


def fill(count: int = 3) -> None:
    """이벤트 쿠폰 아이디어를 서로 다른 고객이 낸 것으로 쌓는다."""
    for user in range(1, count + 1):
        out = submit(f"생일에 쿠폰을 주는 이벤트가 있으면 좋겠어요 {user}번째", user_id=user)
        assert out["output"]["received"] is True


# ---------------------------------------------------------------- 받기 규칙


def test_the_idea_is_stored_apart_from_inquiries() -> None:
    out = submit("생일에 쿠폰을 주는 이벤트가 있으면 좋겠어요")
    assert out["status"] == "completed" and out["output"]["idea_id"] == "IDEA-1"
    assert out["output"]["received"] is True
    assert handlers.STORE.all() == []  # 문의 목록에는 들어가지 않는다
    (idea,) = handlers.IDEAS.all()
    assert idea.kind == "event" and idea.area == "benefit" and idea.status == "new"


def test_complaints_are_sent_back_to_the_inquiry_form() -> None:
    out = submit("배송이 안 왔어요 환불해 주세요")
    assert out["outcome"] == "use_inquiry"
    assert handlers.IDEAS.all() == []


@pytest.mark.parametrize(
    ("text", "outcome"),
    [("좋아요", "too_short"), ("자세한 건 https://example.com 에서 보세요", "spam")],
)
def test_short_or_promotional_text_is_not_stored(text: str, outcome: str) -> None:
    assert submit(text)["outcome"] == outcome
    assert handlers.IDEAS.all() == []


def test_personal_info_is_masked_before_storing() -> None:
    submit("연락은 010-1234-5678 로 주시면 좋겠어요 새 이벤트도 부탁해요")
    (idea,) = handlers.IDEAS.all()
    assert "010-1234-5678" not in idea.text


def test_the_same_customer_sending_the_same_text_adds_one_idea() -> None:
    first = submit("재입고 알림 기능이 있으면 좋겠어요", kind="feature", key="a")
    again = submit("재입고  알림 기능이 있으면 좋겠어요", kind="feature", key="b")
    assert first["output"]["idea_id"] == again["output"]["idea_id"]
    assert len(handlers.IDEAS.all()) == 1


def test_a_customer_can_send_only_a_few_ideas_a_day() -> None:
    for n in range(ideas.DAILY_LIMIT):
        assert submit(f"다양한 책 종류를 늘려 주세요 {n}", kind="product")["output"]["received"]
    assert submit("하나 더 제안해요 책 종류 늘리기")["outcome"] == "rate_limited"
    assert submit("다른 고객의 제안입니다 책 종류", user_id=2)["output"]["received"] is True


def test_the_customer_id_is_required() -> None:
    out = call(
        "submit_idea", {"kind": "event"}, {"text": "이벤트를 열어 주세요"}, requester="shop-api"
    )
    assert out["status"] != "completed"


# ---------------------------------------------------------------- 읽기와 묶음


def test_list_ideas_groups_new_ideas_without_customer_ids() -> None:
    fill(3)
    submit("앱에서 검색이 더 빨랐으면 좋겠어요", user_id=4, kind="improvement")
    out = call("list_ideas")["output"]
    assert out["total"] == 4 and out["new_count"] == 4
    top = out["groups"][0]
    assert (top["kind"], top["area"], top["idea_count"], top["customer_count"]) == (
        "event",
        "benefit",
        3,
        3,
    )
    assert top["ready"] is True and top["kind_label"] == "이벤트·혜택 요청"
    assert {"word": "쿠폰", "count": 3} in top["top_words"]
    assert all("user_id" not in item for item in out["items"])
    assert out["groups"][1]["ready"] is False  # 한 건뿐


def test_list_ideas_filters_by_kind() -> None:
    fill(3)
    submit("결제 수단을 늘려 주세요 간편결제 등", user_id=9, kind="feature")
    out = call("list_ideas", {"kind": "feature"})["output"]
    assert [i["kind"] for i in out["items"]] == ["feature"]


# ---------------------------------------------------------------- 기획에 넘기기

DIGEST = {"author": "배병환"}


def test_digest_sends_one_ticket_per_group_and_marks_ideas_sent(sent: Sent) -> None:
    fill(3)
    out = call("send_idea_digest", DIGEST)["output"]
    assert out["failed"] == 0
    (row,) = out["sent"]
    assert row["request_id"] == REQ and row["idea_count"] == 3
    (ticket,) = sent
    assert ticket.input["title"] == "[고객 아이디어] 이벤트·혜택 요청 · 쿠폰·적립·이벤트 3건"
    assert "고객 3명" in ticket.input["evidence"] and "쿠폰 3" in ticket.input["evidence"]
    assert ticket.input["evidence_ids"] == ["idea:IDEA-1", "idea:IDEA-2", "idea:IDEA-3"]
    assert ticket.input["source_ref"].startswith("idea-digest:event:benefit:IDEA-1")
    stored = handlers.IDEAS.all()
    assert {i.status for i in stored} == {"sent"}
    assert {(i.request_id, i.sent_by) for i in stored} == {(REQ, "배병환")}


def test_the_ticket_carries_no_customer_text(sent: Sent) -> None:
    fill(3)
    call("send_idea_digest", DIGEST)
    payload = f"{sent[0].input['title']} {sent[0].input['evidence']}"
    assert "생일에 쿠폰을 주는" not in payload  # 고객 문장은 싣지 않는다 (단어 집계만)


def test_sent_ideas_are_not_sent_twice(sent: Sent) -> None:
    fill(3)
    call("send_idea_digest", DIGEST)
    again = call("send_idea_digest", DIGEST)
    assert again["outcome"] == "nothing_to_send" and len(sent) == 1


def test_groups_below_the_threshold_stay_new(sent: Sent) -> None:
    fill(2)
    out = call("send_idea_digest", DIGEST)
    assert out["outcome"] == "nothing_to_send" and sent == []
    assert {i.status for i in handlers.IDEAS.all()} == {"new"}
    low = call("send_idea_digest", {**DIGEST, "min_ideas": 1})["output"]
    assert low["sent"][0]["idea_count"] == 2


def test_a_group_can_be_picked_by_kind_and_area(sent: Sent) -> None:
    fill(3)
    for user in range(1, 4):
        submit(f"당일 배송을 해 주세요 {user}", user_id=user, kind="improvement")
    call("send_idea_digest", {**DIGEST, "kind": "improvement", "area": "shipping"})
    assert len(sent) == 1 and "개선 제안 · 배송" in sent[0].input["title"]
    left = {i.status for i in handlers.IDEAS.all() if i.kind == "event"}
    assert left == {"new"}


def test_ideas_stay_new_when_planning_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fill(3)

    async def down(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unavailable")

    monkeypatch.setattr(handlers, "SEND", down)
    out = call("send_idea_digest", DIGEST)["output"]
    assert out["failed"] == 1 and out["sent"][0]["error"].startswith("planning_")
    assert {i.status for i in handlers.IDEAS.all()} == {"new"}


def test_only_the_dashboard_may_send_the_digest(sent: Sent) -> None:
    fill(3)
    out = call("send_idea_digest", DIGEST, requester="shop-api")
    assert out["status"] != "completed" and sent == []


# ---------------------------------------------------------------- 카드·영속


def test_every_idea_skill_is_in_the_card_and_handlers() -> None:
    ids = {s.id for s in load_card().skills}
    for skill in ("submit_idea", "list_ideas", "send_idea_digest"):
        assert skill in ids and skill in ALL_HANDLERS


def test_ideas_survive_a_restart(memory_repo: type) -> None:
    repo = memory_repo()
    first = persistence.PersistentIdeaStore(repo)
    idea = first.add(
        tenant_id="default",
        context_id="ctx_shop_a",
        user_id=1,
        kind="event",
        text="쿠폰 이벤트를 열어 주세요",
    )
    first.mark_sent([idea.idea_id], "default", REQ, "배병환")
    again = persistence.PersistentIdeaStore(repo)
    (found,) = again.all("default")
    assert found.status == "sent" and found.request_id == REQ
    second = again.add(
        tenant_id="default",
        context_id="ctx_shop_b",
        user_id=2,
        kind="event",
        text="적립금을 늘려 주세요",
    )
    assert second.idea_id == "IDEA-2"

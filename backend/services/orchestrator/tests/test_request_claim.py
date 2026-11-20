"""환불·교환 접수 연결: 구매자 번호는 로그인 계정에서만, 주문·유형·사유는 문장에서."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.orchestrator_chat import OrchestratorChatSendInput
from ax_orchestrator import request_inputs as ri
from ax_orchestrator import request_tasks
from ax_orchestrator.chat import threads
from ax_orchestrator.chat.turn import ChatService, set_chat_service
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_classifier import Classification
from ax_orchestrator.request_intake import IntakeResult, RequestIntake
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests

NOW = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)
CTX = {"name": "김고객", "buyer_id": "buy_0007"}


def claim(text: str, ctx: dict | None = CTX) -> ri.Fill:
    return ri.fill("claim_input", text, NOW, "wr_1", ctx)


# ---- 입력 규칙 ------------


def test_buyer_and_name_come_from_the_account_and_the_rest_from_the_sentence() -> None:
    out = claim("주문 ord_1001 책이 파손돼서 환불 접수해 주세요")
    assert out.ask is None and out.handoff is None
    assert out.payload == {
        "buyer_id": "buy_0007",
        "customer_name": "김고객",
        "order_id": "ord_1001",
        "type": "return",
        "reason": "damaged",
    }
    assert out.note == "주문 ord_1001 환불·반품 접수 (사유: 파손)"


@pytest.mark.parametrize(
    ("text", "kind", "reason"),
    [
        ("ord_1 교환하고 싶어요. 다른 책이 왔어요", "exchange", "wrong_item"),
        ("ord_1 주문 취소해 주세요", "cancel", "other"),
        ("ord_1 환불해 주세요 단순 변심이에요", "return", "change_of_mind"),
        ("ord_1 환불 부탁해요 배송이 너무 늦게 왔어요", "return", "late_delivery"),
        ("ord_1 반품할게요 불량이에요", "return", "defect"),
        ("ord_1 환불해 주세요 한 권이 빠져서 왔어요", "return", "missing_item"),
    ],
)
def test_type_and_reason_are_read_from_the_words(text: str, kind: str, reason: str) -> None:
    out = claim(text)
    assert out.payload["type"] == kind and out.payload["reason"] == reason


def test_a_buyer_number_written_in_the_sentence_is_never_used() -> None:
    out = claim("buy_9999 구매자로 ord_1001 환불해 주세요")
    assert out.payload["buyer_id"] == "buy_0007"  # 계정의 값만 쓴다
    assert "buy_9999" not in str(out.payload)


def test_missing_order_or_type_is_asked_and_not_guessed() -> None:
    assert claim("환불 접수하고 싶어요").ask == ri.ORDER_ASK
    assert claim("ord_1001 이거 어떻게 해야 하나요").ask == ri.CLAIM_TYPE_ASK
    assert claim("ord_1001 환불이나 교환 해 주세요").ask == ri.CLAIM_TYPE_ASK  # 둘이 섞였다


@pytest.mark.parametrize(
    "ctx", [None, {}, {"name": "김고객", "buyer_id": None}, {"name": "", "buyer_id": "buy_1"}]
)
def test_no_buyer_link_or_no_name_goes_to_a_person_not_to_a_question(ctx: dict | None) -> None:
    out = claim("ord_1001 환불해 주세요", ctx)
    assert out.handoff == ri.BUYER_LINK_MSG and out.ask is None and out.payload == {}


# ---- 접수와 실행 ------------


class LLM:
    async def structured(self, prompt, schema, *, untrusted=None):
        return Classification(
            kind="request", task="claim_register", confidence=0.9, reason="r", alternatives=[]
        )


def result(status: TaskStatus = TaskStatus.COMPLETED, **output) -> AgentResult:
    meta = RequestMeta(
        context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentResult(meta=meta, status=status, outcome="ok", output=output)


class Fake:
    def __init__(self) -> None:
        self.calls: list = []
        entry = SkillEntry("support", "create_claim", "A", None, "normal", True)
        self.registry = SimpleNamespace(find=lambda s: entry if s == "create_claim" else None)

    async def caller(self, dept, req):
        self.calls.append(req)
        return result(claim_id="cl_1")


@pytest.mark.asyncio
async def test_a_linked_buyer_gets_a_claim_after_a_person_approves_what_will_be_sent() -> None:
    reqs, started = WorkRequests(), []
    intake = RequestIntake(reqs, clock=lambda: NOW)
    fake = Fake()
    runner = RequestRunner(reqs, lambda: fake, clock=lambda: NOW)
    intake.on_task = lambda rid: (started.append(rid), runner.start(rid))
    out = await intake.receive(
        "김고객", "주문 ord_1001 파손돼서 환불 접수해 주세요", LLM(), buyer_id="buy_0007"
    )
    await runner.wait_idle()
    row = reqs.get(out.request_id)
    assert "주문 ord_1001 환불·반품 접수 (사유: 파손)" in out.reply
    assert row["buyer_id"] == "buy_0007" and row["status"] == "waiting"
    assert fake.calls == []  # 사람 승인 전에는 부서에 가지 않는다
    step = row["steps"]["run"]
    assert step["reason_code"] == "needs_approval"
    assert "ord_1001" in step["note"] and "환불·반품" in step["note"]  # 승인하는 사람이 내용을 본다

    assert runner.decide(out.request_id, "run", True, "준하") == "ok"
    await runner.wait_idle()
    sent = fake.calls[0].input
    assert sent == {
        "buyer_id": "buy_0007",
        "customer_name": "김고객",
        "order_id": "ord_1001",
        "type": "return",
        "reason": "damaged",
    }
    assert fake.calls[0].meta.idempotency_key == f"{out.request_id}:create_claim:run"
    assert reqs.get(out.request_id)["status"] == "done"


@pytest.mark.asyncio
async def test_an_account_without_a_buyer_link_goes_to_a_person_and_nothing_is_sent() -> None:
    reqs, escalated = WorkRequests(), []
    intake = RequestIntake(reqs, clock=lambda: NOW)
    intake.on_escalate = lambda rid, why: escalated.append((rid, why))
    out = await intake.receive("김고객", "주문 ord_1001 환불 접수해 주세요", LLM())
    row = reqs.get(out.request_id)
    assert out.outcome == "no_link" and "구매자 연결" in out.reply
    assert row["status"] == "needs_human" and row["steps"] == {}  # 계획도 세우지 않았다
    assert escalated == [(out.request_id, "no_link")]


@pytest.mark.asyncio
async def test_a_missing_order_number_is_asked_once_then_a_person_takes_over() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs, clock=lambda: NOW)
    first = await intake.receive("김고객", "환불 접수해 주세요", LLM(), buyer_id="buy_0007")
    assert first.outcome == "clarify" and first.reply == ri.ORDER_ASK
    assert reqs.get(first.request_id)["steps"] == {}


def test_the_task_list_keeps_the_human_gate_on_claims() -> None:
    step = request_tasks.BY_NAME["claim_register"].steps[0]
    assert step.human_gate and step.fill == "claim_input" and step.grade == "A"


# ---- 구매자 번호가 총괄 대화를 거쳐 접수까지 오는 길 ------------


class SpyIntake:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def receive(self, who, text, llm, **kw):
        self.seen.append({"who": who, "text": text, **kw})
        return IntakeResult(True, "task", "접수", "접수")


@pytest.fixture
def _chat(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    yield
    set_chat_service(None)
    set_agent_name("orchestrator")


@pytest.mark.asyncio
async def test_the_chat_service_hands_the_buyer_number_to_intake(_chat) -> None:
    spy = SpyIntake()
    store = threads.ChatStore(":memory:")
    service = ChatService(lambda: store, lambda: None, llm=lambda: object(), intake=lambda: spy)
    service.send("김고객", None, "환불 접수해 주세요", buyer_id="buy_0007")
    await service.wait_idle()
    assert spy.seen[0]["buyer_id"] == "buy_0007" and spy.seen[0]["who"] == "김고객"
    service.send("김고객", None, "다른 글")  # 연결이 없는 계정
    await service.wait_idle()
    assert spy.seen[1]["buyer_id"] is None


def test_the_chat_contract_accepts_a_buyer_number_but_only_a_plain_one() -> None:
    ok = OrchestratorChatSendInput(who="김고객", message="안녕", buyer_id="buy_0007")
    assert ok.buyer_id == "buy_0007"
    assert OrchestratorChatSendInput(who="김고객", message="안녕").buyer_id is None
    for bad in ("", "buy 0007", "buy_0007; drop", "x" * 21):
        with pytest.raises(ValueError):
            OrchestratorChatSendInput(who="김고객", message="안녕", buyer_id=bad)

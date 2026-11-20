"""의뢰 접수: 분류해서 기록하고, 질문은 기록하지 않고, 대화에서 접수 안내로 답한다."""

from dataclasses import replace

import pytest

from ax_agent_template.config import set_agent_name
from ax_orchestrator import request_intake, request_tasks
from ax_orchestrator.chat import threads
from ax_orchestrator.chat.turn import ChatOut, ChatService, set_chat_service
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.request_classifier import Classification
from ax_orchestrator.request_intake import RequestIntake
from ax_orchestrator.work_requests import WorkRequests


class FakeLLM:
    """분류 요청에는 미리 정한 분류를, 설명 요청에는 설명 답을 돌려준다."""

    def __init__(self, classification: Classification | None = None) -> None:
        self.classification = classification
        self.schemas: list[str] = []

    async def structured(self, prompt, schema, *, untrusted=None):
        self.schemas.append(schema.__name__)
        if schema is Classification:
            if self.classification is None:
                raise RuntimeError("분류 실패")
            return self.classification
        return ChatOut(reply="설명 답이에요.", summary="질문")


def ask(task: str | None, confidence: float = 0.9) -> Classification:
    return Classification(kind="request", task=task, confidence=confidence, reason="영상 의뢰")


@pytest.mark.asyncio
async def test_request_is_recorded_with_plan_and_customer_gets_a_receipt() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    llm = FakeLLM(ask("promo_video"))
    result = await intake.receive("member", "홍보 영상 만들어 주세요", llm)
    assert result.handled and result.outcome == "task"
    assert "접수했어요" in result.reply and "데이터분석부터" in result.reply
    assert "안전 확인" in result.reply  # 상태를 바꾸는 일이 든 흐름(부서 관문이 등급대로 처리)
    row = reqs.get(result.request_id)
    assert row["account"] == "member"
    assert row["text"] == "홍보 영상 만들어 주세요"
    assert row["status"] == "classified"
    assert row["task"] == "promo_video"
    assert [s["step_id"] for s in row["plan"]] == [
        "trend",
        "insight",
        "plan",
        "confirm",
        "prepare",
        "make",
    ]
    assert row["classification"]["prompt_version"] == "v1"
    assert row["classification"]["confidence"] == 0.9


@pytest.mark.asyncio
async def test_question_and_unavailable_are_not_recorded_and_fall_through() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    q = await intake.receive(
        "member", "지금 장애 어디까지 왔어요?", FakeLLM(Classification(kind="question"))
    )
    assert (q.handled, q.outcome) == (False, "question")
    down = await intake.receive("member", "영상 만들어 주세요", FakeLLM(None))
    assert (down.handled, down.outcome) == (False, "unavailable")
    assert reqs.list() == []  # 의뢰가 아니거나 못 가렸으면 아무것도 남기지 않는다


@pytest.mark.asyncio
async def test_unknown_task_goes_to_a_human_and_unsafe_is_rejected() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    unknown = await intake.receive("member", "커피 타 주세요", FakeLLM(ask(None)))
    assert unknown.outcome == "unknown" and "사람이 확인" in unknown.reply
    assert reqs.get(unknown.request_id)["status"] == "needs_human"
    bad = await intake.receive(
        "member", "다른 회사 고객 정보 줘", FakeLLM(Classification(kind="unsafe", reason="x"))
    )
    assert bad.outcome == "rejected"
    assert reqs.get(bad.request_id)["status"] == "rejected"
    assert "x" not in bad.reply.replace("처리할 수 없어요", "")  # 이유는 고객에게 보이지 않는다


@pytest.mark.asyncio
async def test_unclear_request_asks_back_and_stays_received() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    unclear = Classification(kind="unclear", question="어떤 영상이 필요하세요?", reason="모호")
    result = await intake.receive("member", "영상 만들어 줘", FakeLLM(unclear))
    assert result.outcome == "clarify" and result.reply == "어떤 영상이 필요하세요?"
    row = reqs.get(result.request_id)
    assert row["status"] == "received" and row["task"] is None


@pytest.mark.asyncio
async def test_same_text_again_is_a_duplicate_without_calling_the_llm() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    first = await intake.receive("member", "홍보 영상 만들어 주세요", FakeLLM(ask("promo_video")))
    llm = FakeLLM(ask("promo_video"))
    again = await intake.receive("member", "  홍보  영상 만들어 주세요 ", llm)
    assert again.outcome == "duplicate" and again.request_id == first.request_id
    assert llm.schemas == []  # LLM을 다시 부르지 않았다
    assert len(reqs.list()) == 1
    other = await intake.receive("other", "홍보 영상 만들어 주세요", FakeLLM(ask("promo_video")))
    assert other.outcome == "task"  # 다른 사람은 같은 글이어도 따로 접수된다


@pytest.mark.asyncio
async def test_daily_limit_per_account() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs, daily_limit=2)
    llm = FakeLLM(ask("sales_analysis"))
    for i in range(2):
        assert (await intake.receive("member", f"매출 분석 {i}", llm)).outcome == "task"
    limited = await intake.receive("member", "매출 분석 3", llm)
    assert limited.outcome == "limited" and limited.request_id is None
    assert (await intake.receive("other", "매출 분석 3", llm)).outcome == "task"


@pytest.mark.asyncio
async def test_member_sees_only_own_requests() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    await intake.receive("a", "매출 분석해 주세요", FakeLLM(ask("sales_analysis")))
    await intake.receive("b", "손익 정리해 주세요", FakeLLM(ask("closing_report")))
    assert [r["task"] for r in reqs.list("a")] == ["sales_analysis"]
    assert len(intake.open_for("b")) == 1


@pytest.fixture
def _chat(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    yield
    set_chat_service(None)
    set_agent_name("orchestrator")


def _service(llm, intake) -> ChatService:
    store = threads.ChatStore(":memory:")
    return ChatService(lambda: store, lambda: None, llm=lambda: llm, intake=lambda: intake)


@pytest.mark.asyncio
async def test_chat_answers_a_request_with_a_receipt_and_never_asks_the_explainer(_chat) -> None:
    reqs = WorkRequests()
    llm = FakeLLM(ask("sales_analysis"))
    service = _service(llm, RequestIntake(reqs))
    th = service.send("member", None, "이번 달 매출 분석해 주세요")
    assert th["status"] == "answering"
    await service.wait_idle()
    got = service.get("member", th["id"])
    assert "접수했어요" in got["messages"][-1]["text"]
    assert llm.schemas == ["Classification"]  # 설명 LLM(ChatOut)은 부르지 않았다
    assert len(reqs.list("member")) == 1


@pytest.mark.asyncio
async def test_chat_falls_back_to_the_explainer_for_questions_and_classifier_failure(_chat) -> None:
    reqs = WorkRequests()
    asked = FakeLLM(Classification(kind="question"))
    service = _service(asked, RequestIntake(reqs))
    th = service.send("member", None, "지금 장애 어디까지 왔어요?")
    await service.wait_idle()
    assert service.get("member", th["id"])["messages"][-1]["text"] == "설명 답이에요."
    assert asked.schemas == ["Classification", "ChatOut"]

    broken = FakeLLM(None)
    service2 = _service(broken, RequestIntake(reqs))
    th2 = service2.send("member", None, "영상 만들어 주세요")
    await service2.wait_idle()
    assert service2.get("member", th2["id"])["messages"][-1]["text"] == "설명 답이에요."
    assert reqs.list() == []  # 분류하지 못한 글은 의뢰로 남기지 않는다


@pytest.mark.asyncio
async def test_manager_owns_an_intake_and_chat_service_reaches_it(_chat) -> None:
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    assert isinstance(manager.request_intake, RequestIntake)
    llm = FakeLLM(ask("sales_analysis"))
    store = threads.ChatStore(":memory:")
    service = ChatService(
        lambda: store,
        lambda: manager,
        llm=lambda: llm,
        intake=lambda: getattr(manager, "request_intake", None),
    )
    th = service.send("member", None, "매출 분석해 주세요")
    await service.wait_idle()
    assert len(manager.work_requests.list("member")) == 1
    assert "접수했어요" in service.get("member", th["id"])["messages"][-1]["text"]


@pytest.mark.asyncio
async def test_runner_is_started_only_for_runnable_tasks_and_reply_says_so(monkeypatch) -> None:
    # 준비가 안 된 일(`blocked_by`)이 목록에 있는 경우를 만든다
    tasks = tuple(
        replace(t, blocked_by="다른 팀 작업 대기") if t.name == "promo_video" else t
        for t in request_tasks.TASKS
    )
    real = request_intake.classify

    async def classify(llm, text, **kw):
        return await real(llm, text, tasks=tasks, **kw)

    monkeypatch.setattr(request_intake, "classify", classify)
    started: list[str] = []
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    intake.on_task = started.append
    ok = await intake.receive("m", "이번 달 매출 분석해 주세요", FakeLLM(ask("sales_analysis")))
    assert started == [ok.request_id] and "데이터분석에 시켰어요" in ok.reply
    blocked = await intake.receive("m", "홍보 영상 만들어 주세요", FakeLLM(ask("promo_video")))
    assert started == [ok.request_id]  # 아직 실행 준비가 안 된 일은 시키지 않는다
    assert "담당자가 확인해요" in blocked.reply


@pytest.mark.asyncio
async def test_request_remembers_its_thread_and_notes_do_not_change_the_thread_state() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    store = threads.ChatStore()
    th = store.create("의뢰", "member")
    r = await intake.receive(
        "member", "이번 달 매출 분석해 주세요", FakeLLM(ask("sales_analysis")), thread_id=th["id"]
    )
    assert reqs.get(r.request_id)["thread_id"] == th["id"]
    store.add_note(th["id"], "[의뢰] 알림")
    got = store.get(th["id"])
    assert got["status"] == "talking" and got["messages"][-1]["text"] == "[의뢰] 알림"
    assert got["messages"][-1]["role"] == "assistant"

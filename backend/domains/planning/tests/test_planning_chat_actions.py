"""기획 AI 대화에서 시킨 일을 직접 한다 (11/20): LLM이 사람 말을 읽고 고른 동작을 코드가 검사해
화면 버튼과 같은 액션으로 실행한다. 결과 문장은 코드가 쓴다(LLM이 성공을 지어내지 않는다)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ax_workflow.core import ActionError, ActionResult
from planning_agent.chat import facts, threads
from planning_agent.chat.turn import MAX_ACTIONS, ChatAction, ChatOut, ChatService

pytestmark = pytest.mark.role("planning")


class ActLLM:
    def __init__(self, actions: list[ChatAction], reply: str = "진행할게요.") -> None:
        self.actions = actions
        self.reply = reply
        self.seen: list[dict] = []

    async def structured(self, system, schema, *, untrusted):
        self.seen.append(untrusted)
        return ChatOut(reply=self.reply, summary="제안 처리", actions=self.actions)


def _prop(pid: str, src: str = "data", status: str = "확인 대기", hour: int = 1):
    return SimpleNamespace(
        id=pid, title=f"{pid} 숏츠", status=status, spec=None, products=[], result=None,
        from_=src, created_at=datetime(2026, 11, 20, hour, 0, tzinfo=UTC), angle=None,
    )  # fmt: skip


def _state():
    props = [_prop("PROP-10", "marketing", hour=1), _prop("PROP-12", "data", hour=5)]
    return SimpleNamespace(requests=[], specs=[], proposals=props, decisions=[])


class Runner:
    """화면 버튼과 같은 액션 창구(`service.dispatch`) 대신."""

    def __init__(self, results: dict[str, ActionResult | Exception] | None = None) -> None:
        self.calls: list[dict] = []
        self.results = results or {}

    async def __call__(self, action: dict) -> ActionResult:
        self.calls.append(action)
        got = self.results.get(action["type"])
        if isinstance(got, Exception):
            raise got
        if got is not None:
            return got
        out = {"make_plan": {"spec_id": "SPEC-7", "request_id": "REQ-9"},
               "proposal_to_request": {"request_id": "REQ-9"}}.get(action["type"], {})  # fmt: skip
        return ActionResult(changed=True, output={"proposal_id": action["proposal"], **out})


ASK = "방금 데이터분석 제안 기획안으로 만들어서 마케팅에 넘겨"


def _run(llm, runner: Runner, message: str = ASK):
    async def go() -> dict:
        store = threads.ChatStore()
        svc = ChatService(lambda: store, _state, lambda: llm, act=runner)
        th = svc.send("yejin", None, message)
        await svc.wait_idle()
        return svc.get("yejin", th["id"])

    return asyncio.run(go())


def test_make_plan_then_send_to_marketing_runs_in_order() -> None:
    runner = Runner()
    llm = ActLLM([ChatAction(type="make_plan", proposal="PROP-12"),
                  ChatAction(type="approve_proposal", proposal="PROP-12")])  # fmt: skip
    th = _run(llm, runner)
    assert [(c["type"], c["proposal"]) for c in runner.calls] == [
        ("make_plan", "PROP-12"), ("approve_proposal", "PROP-12")]  # fmt: skip
    reply = th["messages"][-1]["text"]
    assert "SPEC-7" in reply and "마케팅" in reply
    assert th["status"] == "talking"


def test_facts_tell_source_and_time_so_llm_can_find_latest() -> None:
    got = facts.collect(_state())["proposals"]["recent"]
    last = got[-1]
    assert last["from"] == "data" and last["from_label"] == "데이터분석"
    assert last["created_at"].startswith("2026-11-20T05:00")


def test_unknown_proposal_and_unknown_type_are_not_run() -> None:
    runner = Runner()
    llm = ActLLM([ChatAction(type="make_plan", proposal="PROP-99")])
    th = _run(llm, runner)
    assert runner.calls == []
    assert "PROP-99" in th["messages"][-1]["text"]


def test_failure_stops_following_actions_and_says_why() -> None:
    runner = Runner({"make_plan": ActionError("PROPOSAL_NOT_OPEN", "확인 대기인 제안만 됩니다")})
    llm = ActLLM([ChatAction(type="make_plan", proposal="PROP-12"),
                  ChatAction(type="approve_proposal", proposal="PROP-12")])  # fmt: skip
    th = _run(llm, runner)
    assert [c["type"] for c in runner.calls] == ["make_plan"]
    text = th["messages"][-1]["text"]
    assert "확인 대기인 제안만 됩니다" in text and "마케팅에 넘겼" not in text


def test_unchanged_result_is_reported_as_not_done() -> None:
    runner = Runner({"approve_proposal": ActionResult(changed=False)})
    th = _run(ActLLM([ChatAction(type="approve_proposal", proposal="PROP-12")]), runner)
    assert "못 했어요" in th["messages"][-1]["text"]


def test_at_most_max_actions_per_turn() -> None:
    runner = Runner()
    acts = [ChatAction(type="make_plan", proposal="PROP-12")] * (MAX_ACTIONS + 2)
    _run(ActLLM(acts), runner)
    assert len(runner.calls) == MAX_ACTIONS


def test_question_without_actions_keeps_plain_answer() -> None:
    runner = Runner()
    th = _run(ActLLM([], reply="확인 대기 제안은 2건이에요."), runner, "제안 몇 건이야?")
    assert runner.calls == [] and th["messages"][-1]["text"] == "확인 대기 제안은 2건이에요."


def test_runner_crash_is_reported_without_details() -> None:
    runner = Runner({"make_plan": RuntimeError("secret=abc")})
    th = _run(ActLLM([ChatAction(type="make_plan", proposal="PROP-12")]), runner)
    text = th["messages"][-1]["text"]
    assert "secret" not in text and "못 했어요" in text


# ------------------------------------------------- 실제 기획 서비스로 끝까지 (화면 버튼과 같은 길)


def test_real_service_makes_plan_and_hands_to_marketing() -> None:
    from ax_contracts.schemas import ExecuteDecision
    from ax_contracts.schemas.planning import CampaignBriefOutput, ReceiveInsightInput
    from planning_agent.domain.service import PlanningService

    async def gate(action, params, meta, evidence) -> ExecuteDecision:
        return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")

    class Brief:
        def __init__(self) -> None:
            self.sent: list = []

        async def send(self, ctx, brief, key):
            self.sent.append(brief)
            return CampaignBriefOutput(outcome="accepted", campaign_id="CMP-1")

    brief = Brief()
    svc = PlanningService(gate=gate, relater=None, marketing=brief)
    inp = ReceiveInsightInput.model_validate({
        "insight_id": "ins-1", "title": "가을 밤 10분 독서 숏츠", "summary": "독서 검색이 오른다",
        "skill": "trend_history", "products": ["밤의 도서관"],
        "facts": [{"label": "독서 검색", "value": "평균의 1.6배", "source": "trend_history"}],
    })  # fmt: skip

    async def go() -> None:
        res = await svc.receive_insight(requester="data", inp=inp)
        pid = res.output["proposal_id"]
        llm = ActLLM([ChatAction(type="make_plan", proposal=pid),
                      ChatAction(type="approve_proposal", proposal=pid)])  # fmt: skip
        store = threads.ChatStore()
        chat = ChatService(lambda: store, lambda: svc.state, lambda: llm, act=svc.dispatch)
        th = chat.send("yejin", None, "방금 데이터분석 제안 기획안으로 만들어서 마케팅에 넘겨")
        await chat.wait_idle()
        while svc._sending:
            await asyncio.gather(*list(svc._sending), return_exceptions=True)
        p = next(x for x in svc.state.proposals if x.id == pid)
        assert p.status == "마케팅 전달" and p.spec is not None
        assert brief.sent and brief.sent[0].proposal_id == pid
        text = chat.get("yejin", th["id"])["messages"][-1]["text"]
        assert p.spec in text and "마케팅에 넘겼어요" in text
        got = llm.seen[0]["facts"]["proposals"]["recent"][-1]
        assert got["id"] == pid and got["from_label"] == "데이터분석"

    asyncio.run(go())

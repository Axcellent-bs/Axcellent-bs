"""재무 대화 답의 숫자 확인(`chat/verify.py`)과 다시 쓰기."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from finance_agent import handlers
from finance_agent import ledger as lg
from finance_agent.chat import threads, verify
from finance_agent.chat.turn import ChatOut, ChatService, set_chat_service
from finance_agent.sources import MockSources

FACTS = {
    "overview": {
        "date": "2026-11-09",
        "today_kpis": {"gmv": 1_675_000, "commission_revenue": 167_500, "pg_fee": -51_925},
        "todo": [{"title": "10월 결산 확정 대기 3건", "amount": 2_340_000}],
    },
    "campaigns": {"month_budget": {"amount_krw": 1_000_000}, "rate": 0.031},
}


def _allowed(*said: str) -> set:
    return verify.allowed_numbers(FACTS, list(said))


def test_numbers_from_facts_pass_in_any_written_form() -> None:
    ok = (
        "오늘 총거래액은 1,675,000원이고 판매 수수료는 167,500원이에요. "
        "PG 수수료는 51,925원, 수수료율은 3.1%예요. 2026-11-09 기준 할 일은 3건, "
        "금액은 234만 원이에요. 예산은 100만 원이에요."
    )
    assert verify.bad_numbers(ok, _allowed()) == []


def test_made_up_or_rounded_numbers_are_caught() -> None:
    reply = "판매 수수료는 약 17만 원이고, 영업 결과는 115,575원이에요. 환불은 12건이에요."
    assert verify.bad_numbers(reply, _allowed()) == ["17만 원", "115,575원", "12건"]


def test_small_plain_numbers_and_user_numbers_are_not_flagged() -> None:
    reply = "1~2분 뒤 ① 결산 화면에서 3번째 줄을 보세요. 말씀하신 500,000원 기준이면 돼요."
    assert verify.bad_numbers(reply, _allowed("50만 원으로 하면 어때?")) == []


# ---- 대화 흐름: 다시 쓰기·알림
@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setitem(lg.EVENTS, "connected", False)
    handlers.reset_state(MockSources())

    async def facts_now() -> dict:
        return FACTS

    from finance_agent.chat import facts

    monkeypatch.setattr(facts, "collect", facts_now)
    yield
    set_chat_service(None)


class ScriptLLM:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seen: list[dict] = []

    async def structured(self, system, schema, *, untrusted):
        self.seen.append(untrusted)
        return ChatOut(reply=self.replies.pop(0), summary="요약")


def _run(llm: ScriptLLM) -> str:
    store = threads.ChatStore(clock=lambda: datetime(2026, 11, 9, 6, tzinfo=UTC))
    svc = ChatService(lambda: store, lambda: llm)
    set_chat_service(svc)

    async def go() -> str:
        th = svc.send("taeyoon", None, "오늘 수수료 얼마야?")
        await svc.wait_idle()
        return svc.get("taeyoon", th["id"])["messages"][-1]["text"]

    return asyncio.run(go())


def test_good_answer_is_kept_without_retry() -> None:
    llm = ScriptLLM("판매 수수료는 167,500원이에요.")
    assert _run(llm) == "판매 수수료는 167,500원이에요." and len(llm.seen) == 1


def test_made_up_number_gets_one_retry() -> None:
    llm = ScriptLLM("판매 수수료는 약 17만 원이에요.", "판매 수수료는 167,500원이에요.")
    assert _run(llm) == "판매 수수료는 167,500원이에요."
    assert llm.seen[1]["numbers_not_in_facts"] == ["17만 원"]


def test_still_wrong_after_retry_is_flagged_not_hidden() -> None:
    llm = ScriptLLM("수수료는 170,000원이에요.", "수수료는 170,000원이에요.")
    text = _run(llm)
    assert text.startswith("수수료는 170,000원이에요.")
    assert "확인 필요: 재무 자료에서 찾지 못한 숫자가 있어요 — 170,000원" in text

"""공통 마무리: LLM 문장 대체(fallback)·숫자 검증·인젝션, 모든 상태 변경의 감사 로그."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ax_agent_template.llm import MockLLMClient
from hr_agent.config import HrSettings, ManualClock
from hr_agent.main import app
from hr_agent.narrative import NarrativeOut, Narrator
from hr_agent.services import build_services, set_services


def _body(
    skill: str, inp: dict, key: str, token: str = "mock:orchestrator", ctx: str = "ctx_nar_01"
):
    return {
        "meta": {
            "context_id": ctx,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": f"{ctx}:{key}",
        },
        "skill": skill,
        "input": inp,
    }


def _services(llm):
    services = build_services(HrSettings(mock=True), clock=ManualClock(), llm=llm)
    set_services(services)
    return services


@pytest.fixture(autouse=True)
def _reset():
    yield
    set_services(None)


class BrokenLLM(MockLLMClient):
    async def structured(self, prompt, schema: type[BaseModel], untrusted=None):
        raise RuntimeError("LLM 장애")


@pytest.mark.asyncio
async def test_no_llm_uses_the_template_without_fallback() -> None:
    sentence = await Narrator(None).sentence("문장", {"n": 3}, "고정 문장 3")
    assert (sentence.text, sentence.fallback) == ("고정 문장 3", False)


@pytest.mark.asyncio
async def test_llm_sentence_with_a_number_not_in_the_facts_is_discarded() -> None:
    llm = MockLLMClient(responses={NarrativeOut: NarrativeOut(text="지연이 99일이나 됐다")})
    sentence = await Narrator(llm).sentence("경고", {"days": 12}, "12일 전 기각")
    assert (sentence.text, sentence.fallback) == ("12일 전 기각", True)


@pytest.mark.asyncio
async def test_llm_sentence_using_only_given_numbers_is_kept() -> None:
    llm = MockLLMClient(responses={NarrativeOut: NarrativeOut(text="12일 전에 기각된 안이다")})
    sentence = await Narrator(llm).sentence("경고", {"days": 12}, "12일 전 기각")
    assert (sentence.text, sentence.fallback) == ("12일 전에 기각된 안이다", False)


@pytest.mark.asyncio
async def test_llm_failure_falls_back_and_marks_the_result() -> None:
    svc = _services(BrokenLLM())
    client = TestClient(app)
    start = client.post("/a2a", json=_body("start_handover", {"owner_id": "emp_01"}, "s")).json()
    assert start["fallback"] is True
    # 대체 문장(고정 템플릿)이 실제로 질문에 들어 있다
    assert "비어 있어요" in start["output"]["questions"][0]["question"]
    svc.store.close()


def test_every_state_change_leaves_an_audit_entry() -> None:
    svc = _services(None)
    client = TestClient(app)
    ctx = "ctx_aud_01"
    start = client.post(
        "/a2a", json=_body("start_handover", {"owner_id": "emp_01"}, "s", ctx=ctx)
    ).json()
    q = start["output"]["questions"][0]["question_id"]
    client.post(
        "/a2a",
        json=_body("answer_handover_question", {"question_id": q, "answer": "답"}, "a", ctx=ctx),
    )
    client.post(
        "/a2a",
        json=_body(
            "confirm_handover", {"handover_id": start["output"]["handover_id"]}, "c", ctx=ctx
        ),
    )
    kinds = set(svc.audit.kinds(ctx))
    assert {"start_handover", "answer_handover_question", "confirm_handover.held"} <= kinds
    svc.store.close()


def test_read_skills_still_audit_but_change_nothing() -> None:
    svc = _services(None)
    client = TestClient(app)
    before = len(svc.gate.calls)
    client.post("/a2a", json=_body("get_roster", {}, "g", ctx="ctx_aud_02"))
    assert len(svc.gate.calls) == before
    svc.store.close()

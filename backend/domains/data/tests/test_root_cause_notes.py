"""원인 후보 추정: 총괄 AI의 개선 노트가 있으면 프롬프트에 참고 메모를 더하고, 없으면 전과 같다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_agent_template import improvement_notes
from ax_contracts.schemas import RequestMeta
from ax_contracts.schemas.data import RootCauseInput
from ax_contracts.schemas.diagnosis import ReceiveImprovementNoteInput
from data_agent.clients import fact_sources
from data_agent.clients.fact_sources import RawFact
from data_agent.skills import root_cause
from data_agent.skills.root_cause import _Candidate, _Candidates, estimate_root_cause

START = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
DATA = RootCauseInput(
    context_id="ctx_inc_note",
    window_from=START,
    window_to=START + timedelta(minutes=20),
    metric="payment_fail_rate",
    component="payment",
)


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id="ctx_inc_note",
        token="mock:orchestrator",
        path=["orchestrator"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


class CapturingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.untrusted: list[dict[str, Any]] = []

    async def structured(self, prompt, schema, untrusted=None, **_):  # noqa: ANN001, ANN003
        self.prompts.append(prompt)
        self.untrusted.append(untrusted)
        fact = untrusted["facts"][1]["fact_id"]  # 0번은 이상 구간, 1번이 배포 사실
        return _Candidates(
            hypotheses=[
                _Candidate(
                    explanation="최근 배포가 원인일 수 있음",
                    fact_ids=[fact],
                    confidence="mid",
                )
            ]
        )


@pytest.fixture(autouse=True)
def _facts(monkeypatch: pytest.MonkeyPatch):
    async def collect(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        return [
            RawFact(
                kind="deploy",
                at=START - timedelta(minutes=10),
                source="dev.find_related_prs",
                ref="pr:412",
                summary="결제 재시도 설정 변경",
            )
        ]

    monkeypatch.setattr(fact_sources, "collect", collect)
    improvement_notes.reset_stores()
    yield
    improvement_notes.reset_stores()


def _note(note_id: str = "note-diag-1", **kw) -> ReceiveImprovementNoteInput:
    base = {
        "note_id": note_id,
        "diagnosis_id": "diag-1",
        "agent": "data",
        "step_id": "root_cause",
        "step_label": "원인 후보 추정",
        "summary": "같은 유형의 이벤트(payment:error_rate_5xx)에서 41초 걸렸어요 (E2, E4).",
        "findings": ["같은 유형의 이벤트에서 반복됐어요"],
        "evidence_contexts": ["ctx_inc_a", "ctx_inc_b"],
        "method": "rules",
        "issued_at": datetime.now(UTC).isoformat(),
        "skill": "root_cause_hypotheses",
    }
    return ReceiveImprovementNoteInput.model_validate({**base, **kw})


def _store():
    return improvement_notes.store_for("data")


async def _run(llm: CapturingLLM):
    return await estimate_root_cause(DATA, _meta(), llm, now=START + timedelta(minutes=25))


@pytest.mark.asyncio
async def test_without_notes_the_question_is_exactly_what_it_was() -> None:
    llm = CapturingLLM()
    result = await _run(llm)
    assert llm.prompts == [root_cause._PROMPT]
    assert set(llm.untrusted[0]) == {"facts"}
    assert result.output.method == "llm" and not result.fallback


@pytest.mark.asyncio
async def test_a_note_for_this_step_adds_a_short_memo_without_digits() -> None:
    _store().add(_note(), "2026-11-17T01:00:00+00:00")
    llm = CapturingLLM()
    result = await _run(llm)
    assert llm.prompts == [f"{root_cause._PROMPT}\n{root_cause._NOTE_RULE}"]
    assert "한 문장으로 짧게" in llm.prompts[0]
    notes = llm.untrusted[0]["notes"]
    assert notes == ["최근 이 단계가 반복해 오래 걸렸다 (원인 확정)"]
    # 노트의 숫자(41초 등)가 프롬프트로 새지 않는다 (설명에 새면 출력 검증이 후보를 버린다)
    assert not any(ch.isdigit() for text in notes for ch in text)
    # 사실 목록은 그대로고 검증도 그대로라 후보가 살아남는다
    assert [f["fact_id"] for f in llm.untrusted[0]["facts"]] == ["f1", "f2"]
    assert result.output.method == "llm" and len(result.output.hypotheses) == 1


@pytest.mark.asyncio
async def test_only_the_latest_note_of_the_step_is_used_and_cause_unknown_is_marked() -> None:
    store = _store()
    for n, known in (("1", True), ("2", False), ("3", True), ("4", False)):
        # 같은 단계의 노트는 최신 하나만 남으니, 단계를 바꿔 넣어 개수 상한을 확인한다
        store.add(
            _note(f"note-{n}", cause_known=known, step_id="root_cause", findings=[]),
            f"2026-11-17T0{n}:00:00+00:00",
        )
    assert len(root_cause.recent_notes()) == 1  # 최신 하나만 남음 (쌓임 방지)
    assert root_cause.recent_notes() == ["최근 이 단계가 반복해 오래 걸렸다 (원인 미확정)"]


@pytest.mark.asyncio
async def test_notes_for_other_steps_revoked_or_expired_notes_are_not_used() -> None:
    store = _store()
    store.add(
        _note("note-other", step_id="impact", skill="impact_analysis"), "2026-11-17T01:00:00+00:00"
    )
    store.add(_note("note-gone"), "2026-11-17T02:00:00+00:00")
    store.revoke("note-gone")
    assert root_cause.recent_notes() == []
    llm = CapturingLLM()
    await _run(llm)
    assert llm.prompts == [root_cause._PROMPT]


@pytest.mark.asyncio
async def test_old_notes_stop_being_used_after_thirty_days() -> None:
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    _store().add(_note(), old)
    assert root_cause.recent_notes() == []

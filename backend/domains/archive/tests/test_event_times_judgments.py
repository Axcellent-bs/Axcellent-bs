"""이슈 시각 기록: 단계 시간을 카드에 두고, 판정은 인사 AI에 받아 기준값 버전과 같이 둔다.

판정 규칙 자체(정상/확인 필요)는 인사 AI의 시험(`domains/hr/tests/test_dept_eval.py`)이 확인한다.
여기서는 아카이브가 판정을 받아 카드에 두는 방식만 본다(판정은 가짜로 끼운다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from archive_agent.cards.service import record_case
from archive_agent.case_memory.service import record_event_times
from ax_contracts.schemas import AgentRequest, RecordCase, RequestMeta

CONTEXT = "ctx_inc_0001"


def _req(skill: str, inp: dict, key: str = "k") -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CONTEXT,
                "token": "mock:orchestrator",
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": f"{CONTEXT}:{skill}:{key}",
            },
            "skill": skill,
            "input": inp,
        }
    )


def _times(**kw) -> dict:
    return {"context_id": CONTEXT, **kw}


def _t(step_id: str, dept: str, **kw) -> dict:
    base = {"step_id": step_id, "dept": dept, "status": "completed", "attempts": 1}
    return {"work_seconds": 1.0, **base, **kw}


async def _incident(svc) -> str:
    record = RecordCase.model_validate(
        {
            "context_id": CONTEXT,
            "kind": "incident",
            "summary": "결제가 서버 오류로 자꾸 실패해요",
            "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
            "alternatives_considered": ["즉시 롤백"],
            "source_refs": [f"inc:{CONTEXT}", "area:infra"],
            "author": "orchestrator",
        }
    )
    result = await record_case(svc, _req("record_case", record.model_dump(mode="json"), "rc"))
    assert result.output["outcome"] == "card_created", result.output
    return result.output["card_id"]


class FakeJudge:
    """인사 AI 대신 판정을 돌려주는 가짜. 다시 시도했으면 확인 필요, 아니면 정상으로 본다."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[RequestMeta, list[dict[str, Any]]]] = []
        self.result = result

    async def __call__(self, meta: RequestMeta, timings: list[dict[str, Any]]):
        self.calls.append((meta, timings))
        if self.result is not None:
            return self.result
        items = [
            {"step_id": t["step_id"], "verdict": "check" if t["attempts"] > 1 else "normal"}
            for t in timings
        ]
        return {"criteria_version": 3, "items": items}


@pytest.mark.asyncio
async def test_judgments_are_saved_on_the_card_with_the_criteria_version(svc) -> None:
    svc.judge = judge = FakeJudge()
    card_id = await _incident(svc)
    rows = [_t("diagnose", dept="infra"), _t("rollback", dept="infra", attempts=2)]
    result = await record_event_times(svc, _req("record_event_times", _times(step_timings=rows)))
    assert result.output["outcome"] == "recorded"
    saved = (await svc.memory.get_card(card_id)).extra["step_judgments"]
    assert saved["criteria_version"] == 3
    assert {i["step_id"]: i["verdict"] for i in saved["items"]} == {
        "diagnose": "normal",
        "rollback": "check",
    }
    (meta, sent) = judge.calls[0]
    assert meta.context_id == CONTEXT and [t["step_id"] for t in sent] == ["diagnose", "rollback"]


@pytest.mark.asyncio
async def test_judgments_follow_the_last_step_timings(svc) -> None:
    svc.judge = FakeJudge()
    card_id = await _incident(svc)
    first = [_t("rollback", dept="infra", attempts=2)]
    await record_event_times(svc, _req("record_event_times", _times(step_timings=first), "a"))
    later = [_t("rollback", dept="infra", attempts=1)]
    await record_event_times(svc, _req("record_event_times", _times(step_timings=later), "b"))
    items = (await svc.memory.get_card(card_id)).extra["step_judgments"]["items"]
    assert [i["verdict"] for i in items] == ["normal"]


@pytest.mark.asyncio
async def test_the_promise_is_sent_to_the_judge_with_the_timings(svc) -> None:
    svc.judge = judge = FakeJudge()
    card_id = await _incident(svc)
    rows = [_t("impact", dept="data", work_seconds=45.0, promised_seconds=60.0)]
    await record_event_times(svc, _req("record_event_times", _times(step_timings=rows)))
    assert judge.calls[0][1][0]["promised_seconds"] == 60.0
    card = await svc.memory.get_card(card_id)
    assert card.extra["step_timings"][0]["promised_seconds"] == 60.0


@pytest.mark.asyncio
async def test_times_without_step_timings_ask_for_no_judgment(svc) -> None:
    svc.judge = judge = FakeJudge()
    card_id = await _incident(svc)
    result = await record_event_times(
        svc, _req("record_event_times", _times(detected_at=svc.clock().isoformat()))
    )
    assert result.output["outcome"] == "recorded"
    assert judge.calls == []
    assert "step_judgments" not in (await svc.memory.get_card(card_id)).extra


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [True, False])
async def test_without_a_judgment_the_times_are_still_recorded(svc, connected: bool) -> None:
    """인사 AI가 답하지 않거나(없음) 연결이 없으면 판정을 지어내지 않고 비워 둔다."""

    class Unreachable:
        async def __call__(self, meta, timings):
            return None

    svc.judge = Unreachable() if connected else None
    card_id = await _incident(svc)
    rows = [_t("diagnose", dept="infra")]
    result = await record_event_times(svc, _req("record_event_times", _times(step_timings=rows)))
    assert result.output["outcome"] == "recorded"
    extra = (await svc.memory.get_card(card_id)).extra
    assert extra["step_timings"] and "step_judgments" not in extra
    kinds = [e["kind"] for e in svc.audit.timeline(CONTEXT)]
    assert "dept_eval.judge_unavailable" in kinds


@pytest.mark.asyncio
async def test_a_stale_judgment_is_dropped_when_the_judge_goes_away(svc) -> None:
    svc.judge = FakeJudge()
    card_id = await _incident(svc)
    await record_event_times(
        svc, _req("record_event_times", _times(step_timings=[_t("diagnose", dept="infra")]), "a")
    )
    assert "step_judgments" in (await svc.memory.get_card(card_id)).extra
    svc.judge = None
    await record_event_times(
        svc,
        _req(
            "record_event_times",
            _times(step_timings=[_t("diagnose", dept="infra", work_seconds=2.0)]),
            "b",
        ),
    )
    assert "step_judgments" not in (await svc.memory.get_card(card_id)).extra


class FakeDiagnose:
    """인사 AI의 반복 확인을 대신하는 가짜. 부른 이슈 번호만 적는다."""

    def __init__(self, *, fail: bool = False, answer: bool = True) -> None:
        self.calls: list[str] = []
        self.fail = fail
        self.answer = answer

    async def __call__(self, meta: RequestMeta, context_id: str) -> bool:
        self.calls.append(context_id)
        if self.fail:
            raise RuntimeError("인사 AI에 닿지 않음")
        return self.answer


@pytest.mark.asyncio
async def test_repeat_check_is_asked_after_the_judgments_are_saved(svc) -> None:
    svc.judge = FakeJudge()
    svc.diagnose = diagnose = FakeDiagnose()
    card_id = await _incident(svc)
    rows = [_t("rollback", dept="infra", attempts=2)]
    await record_event_times(svc, _req("record_event_times", _times(step_timings=rows)))
    assert diagnose.calls == [CONTEXT]
    # 인사가 카드에서 판정을 읽으니 부르는 시점에 이미 저장돼 있어야 한다
    assert (await svc.memory.get_card(card_id)).extra["step_judgments"]["items"]


@pytest.mark.asyncio
async def test_no_repeat_check_without_judgments_or_step_timings(svc) -> None:
    svc.diagnose = diagnose = FakeDiagnose()
    await _incident(svc)
    rows = [_t("rollback", dept="infra")]
    svc.judge = None  # 판정을 못 받으면 반복을 볼 근거가 없다
    await record_event_times(svc, _req("record_event_times", _times(step_timings=rows), "a"))
    svc.judge = FakeJudge()
    await record_event_times(
        svc, _req("record_event_times", _times(detected_at=svc.clock().isoformat()), "b")
    )  # 단계 시간 없이 시각만
    assert diagnose.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [True, False])
async def test_repeat_check_trouble_never_blocks_the_times(svc, fail: bool) -> None:
    svc.judge = FakeJudge()
    svc.diagnose = FakeDiagnose(fail=fail, answer=False)
    card_id = await _incident(svc)
    rows = [_t("rollback", dept="infra", attempts=2)]
    result = await record_event_times(svc, _req("record_event_times", _times(step_timings=rows)))
    assert result.output["outcome"] == "recorded"
    assert (await svc.memory.get_card(card_id)).extra["step_judgments"]["items"]
    unavailable = "dept_eval.diagnosis_check_unavailable" in svc.audit.kinds(CONTEXT)
    assert unavailable is True  # 못 했다는 사실은 감사 기록에 남긴다

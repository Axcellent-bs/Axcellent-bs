"""반복 확인 필요 찾기와 `request_agent_diagnosis`: 반복이면 총괄에 진단 요청을 보낸다."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, Card
from ax_contracts.schemas.diagnosis import (
    DiagnoseAgentIssueInput,
    DiagnoseAgentIssueOutput,
    RequestAgentDiagnosisOutput,
)
from hr_agent.dept_eval import judge_timings, load_criteria
from hr_agent.dept_eval.criteria import CriteriaError, RepeatRule, parse_criteria
from hr_agent.dept_eval.diagnosis import request_agent_diagnosis
from hr_agent.dept_eval.repeat import find_repeats
from hr_agent.dept_eval.summary import _event

NAMES = {"a": "결제 오류", "b": "검색 비어 나옴", "c": "주문 목록 안 열림", "d": "로그인 흰 화면"}


def _t(step_id: str, dept: str, **kw) -> dict:
    base = {"step_id": step_id, "dept": dept, "status": "completed", "attempts": 1}
    return {"work_seconds": 1.0, **base, **kw}


async def _event_card(svc, name: str, timings: list[dict], minutes_ago: int) -> str:
    ctx = f"ctx_inc_{name}"
    at = (svc.clock() - timedelta(minutes=minutes_ago)).isoformat()
    await svc.memory.write_card(
        Card(
            card_id=f"card_{name}",
            kind="incident",
            context_id=ctx,
            title=NAMES[name],
            body=NAMES[name],
            tags=[],
            source_refs=[f"inc:{ctx}", "area:infra"],
            author="orchestrator",
            confidence="high",
            created_at=svc.clock(),
            extra={
                "timeline": {"detected_at": at},
                "step_timings": timings,
                "step_judgments": judge_timings(timings, load_criteria()),
            },
        )
    )
    return ctx


SLOW = {"attempts": 2, "work_seconds": 41.0}  # 다시 시도 → 확인 필요


class Sender:
    def __init__(self, answer: DiagnoseAgentIssueOutput | None) -> None:
        self.answer = answer
        self.sent: list[DiagnoseAgentIssueInput] = []

    async def __call__(self, meta, payload):  # noqa: ANN001
        self.sent.append(payload)
        return self.answer


def _req(context_id: str) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": context_id,
                "token": "mock:archive",
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": f"{context_id}:request_agent_diagnosis:k",
            },
            "skill": "request_agent_diagnosis",
            "input": {"context_id": context_id},
        }
    )


async def _run(svc, context_id: str) -> RequestAgentDiagnosisOutput:
    result = await request_agent_diagnosis(svc, _req(context_id))
    assert result.status.value == "completed", result
    return RequestAgentDiagnosisOutput.model_validate(result.output)


# ---- 기준값 파일 -------------------------------------------------------------------------------
def test_repeat_rule_is_in_the_criteria_file() -> None:
    rule = load_criteria().repeat
    assert (rule.min_records, rule.window, rule.min_flagged) == (3, 3, 2)
    assert rule.flagged_verdicts == ("check", "silent")  # 실패·막힘은 넣지 않았다
    assert rule.daily_limit_per_agent == 1


def test_criteria_without_repeat_table_uses_the_defaults() -> None:
    parsed = parse_criteria(
        {"version": 1, "default": {"normal_max_seconds": 30, "normal_max_attempts": 1}}
    )
    assert parsed.repeat == RepeatRule()


@pytest.mark.parametrize(
    "bad",
    [
        {"min_flagged": 4, "window": 3},
        {"window": 0},
        {"flagged_verdicts": ["normal"]},
        {"flagged_verdicts": []},
        {"min_records": "3"},
    ],
)
def test_bad_repeat_values_are_refused(bad: dict) -> None:
    data = {
        "version": 1,
        "default": {"normal_max_seconds": 30, "normal_max_attempts": 1},
        "repeat": bad,
    }
    with pytest.raises(CriteriaError):
        parse_criteria(data)


# ---- 반복 찾기 ---------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_two_of_the_last_three_flags_the_agent_and_step(svc) -> None:
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 30)
    await _event_card(svc, "b", [_t("rollback", "infra")], 20)
    await _event_card(svc, "c", [_t("rollback", "infra", **SLOW)], 10)
    cards = [c for c in await svc.memory.list_cards(states=["active"])]
    events = sorted((_event(c) for c in cards), key=lambda e: e.at, reverse=True)
    found = find_repeats(events, load_criteria().repeat)
    assert [(f.agent, f.step_id, f.window, f.flagged) for f in found] == [
        ("infra", "rollback", 3, 2)
    ]
    assert [e.context_id for e, _ in found[0].evidence] == ["ctx_inc_c", "ctx_inc_a"]


@pytest.mark.asyncio
async def test_a_single_slow_event_or_too_few_records_is_not_a_repeat(svc) -> None:
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 30)
    await _event_card(svc, "b", [_t("rollback", "infra")], 20)
    assert (await _run(svc, "ctx_inc_b")).found == 0  # 기록 2건뿐
    await _event_card(svc, "c", [_t("rollback", "infra")], 10)
    assert (await _run(svc, "ctx_inc_c")).found == 0  # 3건 중 1건만 확인 필요


@pytest.mark.asyncio
async def test_handed_off_and_no_record_do_not_count(svc) -> None:
    handed = _t("rollback", "infra", status="transfer_queued")
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 40)
    await _event_card(svc, "b", [handed], 30)
    await _event_card(svc, "c", [handed], 20)
    await _event_card(svc, "d", [_t("rollback", "infra", **SLOW)], 10)
    # 사람에게 넘긴 두 건은 세지 않아 판정 가능한 기록은 2건뿐이다
    out = await _run(svc, "ctx_inc_d")
    assert out.found == 0


# ---- 요청 보내기 -------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repeat_sends_one_request_with_the_evidence(svc) -> None:
    sender = Sender(DiagnoseAgentIssueOutput(request_id="diag-1", status="received"))
    svc.extras["diagnosis_send"] = sender
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 30)
    await _event_card(svc, "b", [_t("rollback", "infra")], 20)
    await _event_card(svc, "c", [_t("rollback", "infra", **SLOW)], 10)
    out = await _run(svc, "ctx_inc_c")
    assert (out.checked_events, out.found) == (3, 1)
    assert [(s.agent, s.step_id, s.status, s.request_id) for s in out.sent] == [
        ("infra", "rollback", "received", "diag-1")
    ]
    (payload,) = sender.sent
    assert (payload.agent, payload.step_label, payload.window, payload.flagged) == (
        "infra",
        "문제 배포 되돌리기",
        3,
        2,
    )
    assert payload.criteria_version == 3
    assert [e.context_id for e in payload.evidence] == ["ctx_inc_c", "ctx_inc_a"]
    assert payload.evidence[0].attempts == 2 and payload.evidence[0].reason


@pytest.mark.asyncio
async def test_an_unrelated_event_does_not_resend_an_old_repeat(svc) -> None:
    sender = Sender(DiagnoseAgentIssueOutput(request_id="diag-1", status="received"))
    svc.extras["diagnosis_send"] = sender
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 60)
    await _event_card(svc, "b", [_t("rollback", "infra", **SLOW)], 50)
    await _event_card(svc, "c", [_t("rollback", "infra")], 40)
    assert (await _run(svc, "ctx_inc_c")).found == 1 and len(sender.sent) == 1
    # 인프라 되돌리기가 없는 이슈가 판정될 때는 옛 반복을 다시 보내지 않는다
    sender.sent.clear()
    await _event_card(svc, "d", [_t("impact", "data")], 5)
    out = await _run(svc, "ctx_inc_d")
    assert out.found == 0 and sender.sent == []


@pytest.mark.asyncio
async def test_orchestrator_unreachable_does_not_fail_the_skill(svc) -> None:
    svc.extras["diagnosis_send"] = Sender(None)
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 30)
    await _event_card(svc, "b", [_t("rollback", "infra", **SLOW)], 20)
    await _event_card(svc, "c", [_t("rollback", "infra")], 10)
    out = await _run(svc, "ctx_inc_c")
    assert out.found == 1 and [s.status for s in out.sent] == ["failed"]


@pytest.mark.asyncio
async def test_mock_mode_without_a_sender_sends_nothing(svc) -> None:
    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 30)
    await _event_card(svc, "b", [_t("rollback", "infra", **SLOW)], 20)
    await _event_card(svc, "c", [_t("rollback", "infra")], 10)
    out = await _run(svc, "ctx_inc_c")
    assert out.found == 1 and [s.status for s in out.sent] == ["failed"]


@pytest.mark.asyncio
async def test_invalid_input_is_refused(svc) -> None:
    bad = AgentRequest.model_validate(
        {
            "meta": {
                "context_id": "ctx_inc_a",
                "token": "mock:archive",
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": "k",
            },
            "skill": "request_agent_diagnosis",
            "input": {},
        }
    )
    result = await request_agent_diagnosis(svc, bad)
    assert result.status.value == "rejected" and result.outcome == "INVALID_INPUT"


# ---- 카드 읽는 곳 ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_real_mode_reads_the_cards_from_the_archive_not_its_own_store(svc) -> None:
    """실제 모드의 이슈 카드는 아카이브 저장소에 쌓인다(인사 저장소만 읽으면 새 이슈가 안 보임)."""
    from hr_agent.dept_eval.cards import load_cards

    await _event_card(svc, "a", [_t("rollback", "infra", **SLOW)], 10)  # 인사 저장소의 옛 카드
    asked: list[str] = []

    async def archive_cards(meta):  # noqa: ANN001, ANN202
        asked.append(meta.context_id)
        return []

    svc.extras["eval_cards"] = archive_cards
    assert await load_cards(svc, _req("ctx_inc_x").meta) == []
    assert asked == ["ctx_inc_x"]


@pytest.mark.asyncio
async def test_real_mode_asks_the_archive_list_cards_skill(svc, monkeypatch) -> None:
    from hr_agent.dept_eval import cards as cards_module

    seen: dict = {}

    class _Result:
        status = cards_module.TaskStatus.COMPLETED
        output = {"cards": [], "collected_at": datetime.now(UTC).isoformat()}

    async def fake_call(target, request):  # noqa: ANN001, ANN202
        seen["target"], seen["skill"], seen["input"] = target, request.skill, request.input
        seen["path"] = request.meta.path
        return _Result()

    monkeypatch.setattr(cards_module, "call_agent", fake_call)
    svc.settings = replace(svc.settings, mock=False)
    got = await cards_module.load_cards(svc, _req("ctx_inc_x").meta)
    assert got == []
    assert (seen["target"], seen["skill"], seen["input"]) == (
        "archive",
        "list_cards",
        {"limit": 200},
    )


@pytest.mark.asyncio
async def test_unreachable_archive_is_an_error_not_an_empty_list(svc, monkeypatch) -> None:
    from hr_agent.clients.errors import UpstreamError
    from hr_agent.dept_eval import cards as cards_module

    async def boom(target, request):  # noqa: ANN001, ANN202
        raise ConnectionError("꺼짐")

    monkeypatch.setattr(cards_module, "call_agent", boom)
    svc.settings = replace(svc.settings, mock=False)
    with pytest.raises(UpstreamError):
        await cards_module.load_cards(svc, _req("ctx_inc_x").meta)


@pytest.mark.asyncio
async def test_the_request_to_the_orchestrator_starts_a_new_path(monkeypatch) -> None:
    """총괄이 시작한 기록 호출의 뒤라 경로를 이으면 총괄이 이미 있어 LOOP_DETECTED로 거절된다."""
    from ax_contracts.schemas import RequestMeta
    from hr_agent.dept_eval import diagnosis as module

    seen: dict = {}

    class _Result:
        status = module.TaskStatus.COMPLETED
        output = {"request_id": "diag-1", "status": "received", "duplicate": False}

    async def fake_call(target, request):  # noqa: ANN001, ANN202
        seen["target"], seen["path"], seen["skill"] = target, request.meta.path, request.skill
        return _Result()

    monkeypatch.setattr(module, "call_agent", fake_call)
    meta = RequestMeta(
        context_id="ctx_inc_a",
        token="mock:archive",
        path=["orchestrator", "decisions"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    payload = DiagnoseAgentIssueInput.model_validate(
        {
            "agent": "infra",
            "step_id": "rollback",
            "step_label": "문제 배포 되돌리기",
            "window": 3,
            "flagged": 2,
            "criteria_version": 3,
            "evidence": [
                {"context_id": "ctx_inc_a", "title": "t", "verdict": "check", "limit_seconds": 30.0}
            ],
            "requested_at": datetime.now(UTC).isoformat(),
        }
    )
    out = await module.send_to_orchestrator(meta, payload)
    assert out is not None and out.status == "received"
    assert (seen["target"], seen["skill"], seen["path"]) == (
        "orchestrator",
        "diagnose_agent_issue",
        [],
    )

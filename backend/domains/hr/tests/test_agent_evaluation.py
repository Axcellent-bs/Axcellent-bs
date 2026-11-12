"""에이전트 평가 집계: 에이전트별 건수·점·이유·반복·추이. 점수·등급은 만들지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, EvaluateAgentsOutput, RecordCase
from hr_agent.cards.service import record_case
from hr_agent.case_memory.service import record_event_times
from hr_agent.dept_eval.summary import AGENT_ORDER, _readable, evaluate_agents, reason_of

SUMMARIES = {
    "a": "결제가 서버 오류로 자꾸 실패해요",
    "b": "검색 결과가 비어 나와요",
    "c": "주문 목록이 열리지 않아요",
    "d": "로그인 뒤 화면이 하얗게 나와요",
}


def _req(skill: str, ctx: str, inp: dict, key: str = "k") -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": ctx,
                "token": "mock:orchestrator",
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": f"{ctx}:{skill}:{key}",
            },
            "skill": skill,
            "input": inp,
        }
    )


def _t(step_id: str, dept: str, **kw) -> dict:
    base = {"step_id": step_id, "dept": dept, "status": "completed", "attempts": 1}
    return {"work_seconds": 1.0, **base, **kw}


async def _event(
    svc,
    name: str,
    timings: list[dict] | None,
    minutes_ago: int = 0,
    event_type: str | None = None,
) -> str:
    ctx = f"ctx_inc_{name}"
    record = RecordCase.model_validate(
        {
            "context_id": ctx,
            "kind": "incident",
            "summary": SUMMARIES[name],
            "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
            "alternatives_considered": ["즉시 롤백"],
            "source_refs": [f"inc:{ctx}", "area:infra"],
            "author": "orchestrator",
            "event_type": event_type,
        }
    )
    made = await record_case(svc, _req("record_case", ctx, record.model_dump(mode="json"), "rc"))
    assert made.output["outcome"] == "card_created", made.output
    if timings is not None:
        detected = (svc.clock() - timedelta(minutes=minutes_ago)).isoformat()
        sent = await record_event_times(
            svc,
            _req(
                "record_event_times",
                ctx,
                {"context_id": ctx, "detected_at": detected, "step_timings": timings},
            ),
        )
        assert sent.output["outcome"] == "recorded", sent.output
    return ctx


async def _run(svc, limit: int = 30) -> EvaluateAgentsOutput:
    result = await evaluate_agents(svc, _req("evaluate_agents", "ctx_ui_1", {"limit": limit}))
    assert result.status.value == "completed", result
    return EvaluateAgentsOutput.model_validate(result.output)


def _agent(out: EvaluateAgentsOutput, dept: str):
    return next(a for a in out.agents if a.dept == dept)


@pytest.mark.asyncio
async def test_no_evaluated_events_still_lists_every_agent_with_nothing(svc) -> None:
    out = await _run(svc)
    assert [a.dept for a in out.agents] == list(AGENT_ORDER)
    assert out.events == [] and out.trend == []
    assert all(a.events == 0 and a.recent == [] for a in out.agents)
    assert (out.criteria_version, out.normal_max_seconds, out.normal_max_attempts) == (3, 30, 1)


@pytest.mark.asyncio
async def test_cards_without_judgments_are_skipped(svc) -> None:
    await _event(svc, "a", None)  # 카드만 있고 단계 시간·판정은 아직 없다
    assert (await _run(svc)).events == []


@pytest.mark.asyncio
async def test_each_agent_gets_one_dot_per_recent_event_newest_first(svc) -> None:
    await _event(
        svc,
        "a",
        [_t("diagnose", "infra"), _t("rollback", "infra", work_seconds=40.0, attempts=2)],
        minutes_ago=30,
    )
    await _event(svc, "b", [_t("diagnose", "infra"), _t("impact", "data")], minutes_ago=5)
    out = await _run(svc)
    assert [e.context_id for e in out.events] == ["ctx_inc_b", "ctx_inc_a"]  # 새로운 이벤트부터
    infra = _agent(out, "infra")
    assert [d.verdict for d in infra.recent] == ["normal", "check"]
    assert (infra.events, infra.normal, infra.check) == (2, 1, 1)
    data = _agent(out, "data")
    assert [d.verdict for d in data.recent] == ["normal", "absent"]  # a에는 참여하지 않았다
    assert data.events == 1
    assert _agent(out, "support").recent[0].verdict == "absent"


@pytest.mark.asyncio
async def test_a_late_step_says_why_and_which_step(svc) -> None:
    await _event(
        svc, "a", [_t("rollback", "infra", work_seconds=42.0, attempts=2)], event_type="deploy"
    )
    out = await _run(svc)
    dot = _agent(out, "infra").recent[0]
    assert dot.verdict == "check"
    assert dot.reason == "문제 배포 되돌리기: 다시 시도 1회 · 42초 걸림(기준 30초)"
    step = out.events[0].steps[0]
    assert step.reason == "다시 시도 1회 · 42초 걸림(기준 30초)"
    assert step.label == "문제 배포 되돌리기"  # 영어 코드가 아니라 쉬운 말


def test_reasons_are_plain_words() -> None:
    base = {"attempts": 1, "work_seconds": 1.0, "limit_seconds": 30.0}
    assert reason_of({**base, "verdict": "normal"}) is None
    assert reason_of({**base, "verdict": "check", "attempts": 2, "work_seconds": 5}) == (
        "다시 시도 1회"
    )
    assert reason_of({**base, "verdict": "check", "work_seconds": 31}) == "31초 걸림(기준 30초)"
    assert "승인" in reason_of({**base, "verdict": "approval_expired"})
    assert reason_of({**base, "verdict": "no_record"}) == "건너뛰었거나 시간을 알 수 없음"


@pytest.mark.asyncio
async def test_retried_and_slow_are_counted_by_event(svc) -> None:
    await _event(svc, "a", [_t("diagnose", "infra", work_seconds=5.0, attempts=2)], minutes_ago=3)
    await _event(svc, "b", [_t("diagnose", "infra", work_seconds=31.0)], minutes_ago=2)
    await _event(svc, "c", [_t("diagnose", "infra", work_seconds=50.0, attempts=2)], minutes_ago=1)
    infra = _agent(await _run(svc), "infra")
    assert (infra.check, infra.retried, infra.slow) == (3, 2, 2)


@pytest.mark.asyncio
async def test_a_blocked_step_outweighs_a_late_one_in_the_dot(svc) -> None:
    rows = [
        _t("diagnose", "infra", work_seconds=40.0),
        _t("rollback", "infra", status="failed", work_seconds=None),
    ]
    await _event(svc, "a", rows)
    dot = _agent(await _run(svc), "infra").recent[0]
    assert dot.verdict == "blocked" and dot.steps == 2


@pytest.mark.asyncio
async def test_waits_are_attached_to_the_event_steps_not_to_the_agent_card(svc) -> None:
    rows = [_t("rollback", "infra", work_seconds=3.5, approval_wait_seconds=34.9)]
    await _event(svc, "a", rows)
    out = await _run(svc)
    step = out.events[0].steps[0]
    assert (step.work_seconds, step.approval_wait_seconds, step.verdict) == (3.5, 34.9, "normal")
    assert _agent(out, "infra").normal == 1


@pytest.mark.asyncio
async def test_the_same_event_type_looked_at_twice_is_a_repeat(svc) -> None:
    late = [_t("diagnose", "infra", work_seconds=40.0, attempts=2)]
    await _event(svc, "a", late, minutes_ago=30, event_type="payment_failure")
    await _event(svc, "b", late, minutes_ago=20, event_type="payment_failure")
    await _event(svc, "c", late, minutes_ago=10, event_type="search_down")
    repeats = _agent(await _run(svc), "infra").repeats
    assert [(r.event_type, r.count) for r in repeats] == [("payment_failure", 2)]
    assert SUMMARIES["b"] in repeats[0].title  # 가장 최근 이벤트 제목


@pytest.mark.asyncio
async def test_average_and_max_use_only_timed_steps(svc) -> None:
    await _event(
        svc,
        "a",
        [
            _t("diagnose", "infra", work_seconds=2.0),
            _t("rollback", "infra", work_seconds=6.0),
            _t("recovered_notice", "marketing", status="skipped", work_seconds=None),
        ],
    )
    out = await _run(svc)
    infra = _agent(out, "infra")
    assert (infra.avg_work_seconds, infra.max_work_seconds) == (4.0, 6.0)
    marketing = _agent(out, "marketing")
    assert marketing.avg_work_seconds is None and marketing.no_record == 1


@pytest.mark.asyncio
async def test_limit_keeps_only_the_newest_events(svc) -> None:
    await _event(svc, "a", [_t("diagnose", "infra")], minutes_ago=30)
    await _event(svc, "b", [_t("diagnose", "infra")], minutes_ago=5)
    out = await _run(svc, limit=1)
    assert [e.context_id for e in out.events] == ["ctx_inc_b"]
    assert len(_agent(out, "infra").recent) == 1


@pytest.mark.asyncio
async def test_trend_counts_events_and_looked_at_events_per_seoul_day(svc) -> None:
    await _event(svc, "a", [_t("diagnose", "infra", attempts=2)], minutes_ago=1)
    await _event(svc, "b", [_t("diagnose", "infra")], minutes_ago=2)
    out = await _run(svc)
    assert len(out.trend) == 1 and (out.trend[0].events, out.trend[0].with_check) == (2, 1)
    assert out.trend[0].day == "2026-10-22"  # 시험용 시계는 서울 시간 2026-10-22 18:00


@pytest.mark.asyncio
async def test_bad_input_is_rejected(svc) -> None:
    result = await evaluate_agents(svc, _req("evaluate_agents", "ctx_ui_1", {"limit": 0}))
    assert result.status.value in ("failed", "rejected")


@pytest.mark.asyncio
async def test_a_skipped_step_does_not_hide_a_finished_one(svc) -> None:
    rows = [
        _t("notice", "marketing"),
        _t("recovered_notice", "marketing", status="skipped", work_seconds=None),
    ]
    await _event(svc, "a", rows)
    out = await _run(svc)
    assert _agent(out, "marketing").recent[0].verdict == "normal"  # 앞 단계가 안 돌아 건너뛴 것
    assert [s.verdict for s in out.events[0].steps] == [
        "normal",
        "no_record",
    ]  # 단계 목록에는 남는다


@pytest.mark.asyncio
async def test_handing_off_to_a_person_is_its_own_verdict_not_a_failure(svc) -> None:
    await _event(svc, "a", [_t("rollback", "infra", status="transfer_queued", work_seconds=None)])
    out = await _run(svc)
    infra = _agent(out, "infra")
    dot = infra.recent[0]
    assert dot.verdict == "handed_off"
    assert "사람에게 넘김" in (dot.reason or "") and "실패나 지연이 아니에요" in (dot.reason or "")
    assert (infra.handed_off, infra.blocked, infra.check) == (1, 0, 0)
    assert out.trend[0].with_check == 0  # 확인이 필요한 이벤트로 세지 않는다


@pytest.mark.asyncio
async def test_a_handoff_never_counts_as_a_repeat_problem(svc) -> None:
    rows = [_t("rollback", "infra", status="transfer_queued", work_seconds=None)]
    await _event(svc, "a", rows, minutes_ago=20, event_type="payment_failure")
    await _event(svc, "b", rows, minutes_ago=10, event_type="payment_failure")
    assert _agent(await _run(svc), "infra").repeats == []


def test_judgments_saved_before_handoffs_were_split_read_the_same_way() -> None:
    old = {"step_id": "rollback", "verdict": "blocked", "status": "transfer_queued"}
    assert _readable(old)["verdict"] == "handed_off"
    waiting = {"step_id": "notice", "verdict": "in_progress", "status": "waiting_human"}
    assert _readable(waiting)["verdict"] == "handed_off"
    failed = {"step_id": "rollback", "verdict": "blocked", "status": "failed"}
    assert _readable(failed)["verdict"] == "blocked"  # 진짜 실패는 그대로


@pytest.mark.asyncio
async def test_a_silent_long_task_is_its_own_verdict_with_a_plain_reason(svc) -> None:
    """총 시간이 길어서가 아니라 응답이 끊겨서다. 일을 못한 것으로 말하지 않는다."""
    rows = [
        _t(
            "impact",
            "data",
            work_seconds=1800.0,
            promised_seconds=3600.0,
            max_silence_seconds=700.0,
        )
    ]
    await _event(svc, "a", rows)
    out = await _run(svc)
    data = _agent(out, "data")
    dot = data.recent[0]
    assert dot.verdict == "silent"
    assert "응답이" in (dot.reason or "") and "끊김" in (dot.reason or "")
    assert "일을 못한 것이 아니라" in (dot.reason or "")
    assert (data.silent, data.blocked, data.check) == (1, 0, 0)
    assert out.trend[0].with_check == 1  # 확인이 필요한 이벤트로 센다
    step = out.events[0].steps[0]
    assert (step.verdict, step.max_silence_seconds) == ("silent", 700.0)


@pytest.mark.asyncio
async def test_steady_notices_keep_a_long_task_normal_in_the_summary(svc) -> None:
    rows = [
        _t("impact", "data", work_seconds=1800.0, promised_seconds=3600.0, max_silence_seconds=60.0)
    ]
    await _event(svc, "a", rows)
    data = _agent(await _run(svc), "data")
    assert data.recent[0].verdict == "normal" and data.silent == 0


@pytest.mark.asyncio
async def test_a_silent_task_outweighs_a_late_one_in_the_dot_and_repeats_count(svc) -> None:
    rows = [
        _t("impact", "data", work_seconds=40.0, attempts=2),  # 늦음
        _t("root_cause", "data", work_seconds=900.0, max_silence_seconds=800.0),  # 끊김
    ]
    await _event(svc, "a", rows, minutes_ago=20, event_type="payment_failure")
    await _event(svc, "b", rows, minutes_ago=10, event_type="payment_failure")
    data = _agent(await _run(svc), "data")
    assert [d.verdict for d in data.recent] == ["silent", "silent"]
    assert [(r.event_type, r.count) for r in data.repeats] == [("payment_failure", 2)]


def test_a_promised_time_is_told_as_expected_versus_actual() -> None:
    base = {"attempts": 1, "limit_seconds": 180.0, "promised": True}
    slow = {**base, "verdict": "check", "work_seconds": 252.0}
    assert reason_of(slow) == "예상 3분인데 4분 12초 걸렸어요"
    retried = {**slow, "attempts": 2}
    assert reason_of(retried) == "다시 시도 1회 · 예상 3분인데 4분 12초 걸렸어요"
    # 기준 시간이 없는 단계(기본 30초)는 옛 문장 그대로다
    plain = {"attempts": 1, "limit_seconds": 30.0, "verdict": "check", "work_seconds": 42.0}
    assert reason_of(plain) == "42초 걸림(기준 30초)"


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0초"),
        (42, "42초"),
        (60, "1분"),
        (125, "2분 5초"),
        (3600, "1시간"),
        (4800, "1시간 20분"),
        (None, "알 수 없음"),
    ],
)
def test_durations_switch_units_so_hours_long_tasks_read_well(seconds, text) -> None:
    from hr_agent.dept_eval.summary import _duration

    assert _duration(seconds) == text


@pytest.mark.asyncio
async def test_the_screen_is_told_where_each_limit_came_from(svc) -> None:
    rows = [
        _t("impact", "data", work_seconds=45.0, promised_seconds=60.0),
        _t("diagnose", "infra", work_seconds=3.0),
    ]
    await _event(svc, "a", rows)
    out = await _run(svc)
    steps = {s.step_id: s for s in out.events[0].steps}
    assert (steps["impact"].promised, steps["impact"].limit_seconds) == (True, 60.0)
    assert (steps["diagnose"].promised, steps["diagnose"].limit_seconds) == (False, 30.0)
    assert out.use_promised is True and out.max_silence_seconds == 300.0

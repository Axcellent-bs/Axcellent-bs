"""의뢰의 긴 작업 이어받기와 홍보 영상 3단계 연결 (15-2·15-3).

부서가 긴 작업으로 받으면(`WORKING` + 작업 번호) 알림(`task.progress`·`task.completed`)으로
단계를 끝내고 다음 단계를 이어 시킨다. 트렌드 보고서 → 기획 입력(insight) → 영상 제작안 흐름에서
기획 입력은 통째로, 그 밖의 결과는 번호로만 넘어간다.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import (
    AgentResult,
    EventEnvelope,
    EventType,
    RequestMeta,
    TaskStatus,
)
from ax_orchestrator import request_tasks
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests

READ = "read"
TASK = "tsk_1"
INSIGHT = {
    "insight_id": "trend_report:rep_1",
    "title": "봄 신간 트렌드",
    "summary": "요약",
    "skill": "trend_report",
    "facts": [{"label": "검색량", "value": "120", "source": "trend"}],
}


def result(status: TaskStatus, outcome: str | None = None, **kw) -> AgentResult:
    meta = RequestMeta(
        context_id="ctx", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    output = kw.pop("output", {})
    return AgentResult(meta=meta, status=status, outcome=outcome, output=output, **kw)


class Fake:
    def __init__(self, entries: dict[str, SkillEntry], answers: dict[str, list]) -> None:
        self.entries = entries
        self.answers = answers
        self.calls: list[tuple[str, object]] = []
        self.registry = SimpleNamespace(find=entries.get)
        self.on_call = None  # 부를 때 끼워 넣을 일 (알림이 접수 응답보다 먼저 오는 경우)

    async def caller(self, dept, req):
        self.calls.append((dept, req))
        if self.on_call is not None:
            self.on_call(req)
        queue = self.answers[req.skill]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def entry(dept: str, skill: str, grade: str | None = READ) -> SkillEntry:
    return SkillEntry(dept, skill, grade, None, "normal", True)


def promo_entries() -> dict[str, SkillEntry]:
    return {
        "trend_report_create": entry("data", "trend_report_create"),
        "trend_report_insight": entry("data", "trend_report_insight"),
        "receive_insight": entry("planning", "receive_insight", "A"),
        "generate_media": entry("marketing", "generate_media", "B"),
    }


def make(task: str = "promo_video", text: str = "신간 소개 영상 만들어 주세요"):
    reqs = WorkRequests()
    row = reqs.create("member", text)
    plan = request_tasks.plan_of(request_tasks.BY_NAME[task])
    reqs.set_plan(
        row["request_id"], task=task, confidence=0.9, reason="r", alternatives=[], plan=plan
    )
    return reqs, row["request_id"]


def runner(reqs, fake, **kw) -> RequestRunner:
    return RequestRunner(reqs, lambda: fake, retry_delay=0, **kw)


def done(**output) -> dict:
    return {"task_id": TASK, "skill": "trend_report_create", "status": "done", "output": output}


def working() -> AgentResult:
    return result(TaskStatus.WORKING, output={"task_id": TASK, "expected_seconds": 120})


async def started(task: str = "trend_report"):
    """긴 작업을 맡기고 알림을 기다리는 단계까지 돌린다."""
    reqs, rid = make(task)
    fake = Fake(promo_entries(), {"trend_report_create": [working()]})
    r = runner(reqs, fake)
    await r.run(rid)
    return reqs, rid, fake, r, f"ctx_{rid}"


@pytest.mark.asyncio
async def test_trend_is_asked_as_a_long_task_with_the_customer_sentence_in_input() -> None:
    reqs, rid, fake, _, _ = await started()
    req = fake.calls[0][1]
    assert req.input == {"caller": "orchestrator", "request_text": "신간 소개 영상 만들어 주세요"}
    step = reqs.get(rid)["steps"]["run"]
    assert step["status"] == "waiting_async" and step["async_task_id"] == TASK
    row = reqs.get(rid)
    assert row["status"] == "running"  # 사람 확인이 아니라 부서의 긴 작업을 기다리는 중이다


@pytest.mark.asyncio
async def test_progress_keeps_the_step_waiting_and_shows_only_the_percent() -> None:
    reqs, rid, _, r, ctx = await started()
    data = {"task_id": TASK, "skill": "trend_report_create", "percent": 40, "note": "비밀 문장"}
    assert r.receive_async("progress", "data", data, ctx) == "progressed"
    step = reqs.get(rid)["steps"]["run"]
    assert step["status"] == "waiting_async" and "40%" in step["note"]
    assert "비밀 문장" not in str(reqs.get(rid))  # 부서가 쓴 문장은 옮기지 않는다


@pytest.mark.asyncio
async def test_completed_event_finishes_the_step_with_the_report_number() -> None:
    reqs, rid, _, r, ctx = await started()
    assert r.receive_async("completed", "data", done(report_id="rep_1"), ctx) == "resumed"
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["run"]["status"] == "completed"
    assert row["steps"]["run"]["result_ref"] == "rep_1"
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_completed_event_continues_to_the_next_step_with_the_number() -> None:
    reqs, rid, fake, r, ctx = await started("promo_video")
    fake.answers["trend_report_insight"] = [
        result(TaskStatus.COMPLETED, output={"report_id": "rep_1", "insight": INSIGHT})
    ]
    r.receive_async("completed", "data", done(report_id="rep_1"), ctx)
    await r.wait_idle()
    assert [c[1].skill for c in fake.calls] == ["trend_report_create", "trend_report_insight"]
    assert fake.calls[1][1].input == {"report_id": "rep_1"}  # 번호만 넘어간다


@pytest.mark.asyncio
async def test_forged_or_mismatched_events_are_ignored_and_unknown_ones_unmatched() -> None:
    reqs, rid, _, r, ctx = await started()
    assert r.receive_async("completed", "marketing", done(report_id="x"), ctx) == "ignored"
    other_skill = {**done(report_id="x"), "skill": "sales_analysis"}
    assert r.receive_async("completed", "data", other_skill, ctx) == "ignored"
    assert reqs.get(rid)["steps"]["run"]["status"] == "waiting_async"  # 위조는 반영되지 않는다
    assert r.receive_async("completed", "data", {**done(), "task_id": "tsk_9"}, ctx) == "unmatched"
    assert r.receive_async("completed", "data", done(report_id="x"), "ctx_nope") == "unmatched"


@pytest.mark.asyncio
async def test_repeated_completion_is_not_applied_twice() -> None:
    reqs, rid, _, r, ctx = await started()
    r.receive_async("completed", "data", done(report_id="rep_1"), ctx)
    await r.wait_idle()
    assert r.receive_async("completed", "data", done(report_id="rep_2"), ctx) == "unmatched"
    assert reqs.get(rid)["steps"]["run"]["result_ref"] == "rep_1"


@pytest.mark.asyncio
async def test_event_arriving_before_the_accepted_reply_is_kept_and_applied() -> None:
    reqs, rid = make("trend_report")
    fake = Fake(promo_entries(), {"trend_report_create": [working()]})
    r = runner(reqs, fake)
    # 같은 보고서를 다시 요청하면 접수 응답보다 완료 알림이 먼저 올 수 있다
    fake.on_call = lambda _req: r.receive_async(
        "completed", "data", done(report_id="rep_1"), f"ctx_{rid}"
    )
    await r.run(rid)
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["run"]["status"] == "completed" and row["status"] == "done"


@pytest.mark.asyncio
async def test_failed_long_task_ends_the_step_and_hands_the_request_to_a_person() -> None:
    reqs, rid, _, r, ctx = await started()
    failed = {"task_id": TASK, "skill": "trend_report_create", "status": "failed", "note": "내부"}
    assert r.receive_async("completed", "data", failed, ctx) == "resumed"
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["run"]["status"] == "failed" and row["status"] == "needs_human"
    assert "내부" not in str(row["steps"])


@pytest.mark.asyncio
async def test_silent_long_task_is_closed_as_no_response() -> None:
    reqs, rid, _, r, _ = await started()
    assert r.expire_silent(max_silence=300) == []  # 방금 받았으니 아직 기다린다
    assert r.expire_silent(max_silence=0) == [rid]
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["run"]["status"] == "failed"
    assert row["steps"]["run"]["reason_code"] == "async_silent"
    assert row["status"] == "needs_human"


@pytest.mark.asyncio
async def test_restart_keeps_waiting_and_a_late_event_still_finishes() -> None:
    reqs, rid, fake, _, ctx = await started()
    fresh = runner(reqs, fake)  # 다시 켠 총괄
    assert await fresh.resume_open() == 0  # 시킬 단계가 없다: 알림을 기다린다
    assert reqs.get(rid)["steps"]["run"]["status"] == "waiting_async"
    assert fresh.receive_async("completed", "data", done(report_id="rep_1"), ctx) == "resumed"
    await fresh.wait_idle()
    assert reqs.get(rid)["status"] == "done"


# ---- 홍보 영상: 트렌드 → 인사이트 → 영상 제작안 ---------------------------------------------


async def promo_until_plan(answers_insight, escalated=None):
    reqs, rid, fake, r, ctx = await started("promo_video")
    if escalated is not None:
        r.escalate = lambda *a: escalated.append(a)
    fake.answers["trend_report_insight"] = [answers_insight]
    fake.answers["receive_insight"] = [result(TaskStatus.COMPLETED, output={"proposal_id": "p_1"})]
    r.receive_async("completed", "data", done(report_id="rep_1"), ctx)
    await r.wait_idle()
    return reqs, rid, fake, r


@pytest.mark.asyncio
async def test_insight_goes_whole_to_the_plan_after_approval_and_is_then_dropped() -> None:
    insight = result(TaskStatus.COMPLETED, output={"report_id": "rep_1", "insight": INSIGHT})
    reqs, rid, fake, r = await promo_until_plan(insight)
    row = reqs.get(rid)
    assert row["steps"]["insight"]["status"] == "completed"
    assert row["steps"]["plan"]["status"] == "waiting_human"  # 상태를 바꾸는 일이라 승인을 기다린다
    assert row["steps"]["plan"]["reason_code"] == "needs_approval"
    assert reqs.get_carry(rid, "insight") == INSIGHT
    assert all(c[1].skill != "receive_insight" for c in fake.calls)  # 승인 전에는 시키지 않는다

    assert r.decide(rid, "plan", True, "준하") == "ok"
    await r.wait_idle()
    plan_call = next(c[1] for c in fake.calls if c[1].skill == "receive_insight")
    assert plan_call.input == INSIGHT  # 기획 입력을 그대로
    assert plan_call.meta.idempotency_key == f"{rid}:receive_insight:plan"  # A 등급이라 필수
    assert reqs.get_carry(rid, "insight") is None  # 쓰고 나면 지운다
    row = reqs.get(rid)
    assert row["steps"]["plan"]["status"] == "completed"
    assert row["steps"]["confirm"]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_no_facts_skips_the_plan_without_asking_approval_and_tells_a_person() -> None:
    calls: list[tuple] = []
    insight = result(
        TaskStatus.COMPLETED,
        output={"report_id": "rep_1", "insight": None, "reason": "SECRET_REASON"},
    )
    reqs, rid, fake, r = await promo_until_plan(insight, calls)
    row = reqs.get(rid)
    assert row["steps"]["plan"]["status"] == "skipped"
    assert row["steps"]["plan"]["reason_code"] == "no_insight"
    assert row["steps"]["confirm"]["status"] == "skipped"
    assert row["steps"]["make"]["status"] == "skipped"
    assert row["status"] == "needs_human"
    assert all(c[1].skill != "receive_insight" for c in fake.calls)  # 기획에 보내지 않는다
    assert "SECRET_REASON" not in str(row)  # 부서가 쓴 이유 문장은 옮기지 않는다
    assert calls == [(rid, "step_failed", "plan")]


@pytest.mark.asyncio
async def test_insight_with_empty_facts_is_treated_as_no_insight() -> None:
    empty = {**INSIGHT, "facts": []}
    insight = result(TaskStatus.COMPLETED, output={"report_id": "rep_1", "insight": empty})
    reqs, rid, _, _ = await promo_until_plan(insight)
    assert reqs.get_carry(rid, "insight") is None
    assert reqs.get(rid)["steps"]["plan"]["status"] == "skipped"


# ---- 조회에는 부서 결과를 내지 않는다 / 알림이 총괄 관리자를 거쳐 온다 ---------------------------


def test_public_view_drops_the_carried_department_result() -> None:
    from ax_orchestrator.main import _public

    reqs, rid = make()
    reqs.set_carry(rid, "insight", INSIGHT)
    assert "carry" in reqs.get(rid)
    assert "carry" not in _public(reqs.get(rid))


@pytest.mark.asyncio
async def test_manager_routes_task_events_of_a_request_to_the_runner() -> None:
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    reqs = manager.work_requests
    row = reqs.create("member", "트렌드 보고서 만들어 주세요")
    rid = row["request_id"]
    plan = [request_tasks.plan_of(request_tasks.BY_NAME["trend_report"])[0]]
    reqs.set_plan(rid, task="trend_report", confidence=0.9, reason="r", alternatives=[], plan=plan)
    reqs.update_step(rid, "run", status="waiting_async", async_task_id=TASK)
    env = EventEnvelope(
        event_id="evt_t1",
        type=EventType.TASK_COMPLETED,
        occurred_at=datetime.now(UTC),
        source="data",
        idempotency_key=f"task.completed:{TASK}",
        context_id=f"ctx_{rid}",
        data=done(report_id="rep_1"),
    )
    outcome, inc = await manager._process(env)
    assert (outcome, inc) == ("resumed", None)
    assert reqs.get(rid)["steps"]["run"]["result_ref"] == "rep_1"
    await manager.request_runner.wait_idle()
    # 알림이 위조(다른 부서가 보냄)되면 반영하지 않는다
    other = env.model_copy(update={"event_id": "evt_t2", "source": "marketing"})
    assert (await manager._process(other))[0] in ("ignored", "unmatched")

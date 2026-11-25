"""홍보 영상 의뢰에 회사 캐릭터 붙이기 (11/23).

- 의뢰에 대표 캐릭터를 정한다: 영상 준비(prepare) 전까지만(뒤면 409), 다른 회사 의뢰는 404
- 정한 번호는 영상 준비 입력 `brand_character_record_id`로만 간다. 결과의 `creative_record_id`가
  단계 결과 번호(result_ref)가 된다. 정하지 않으면 지금 흐름 그대로다
- 영상이 사람 승인에 올라가면(`video_held`) 그 단계는 실패가 아니라 "영상 승인 대기"(held·review_id)
- 안전 관문 결정이 나면(이벤트 또는 주기 점검) 같은 단계를 같은 멱등 키로 다시 보낸다.
  거절이면 단계 rejected_by_human("사람이 영상 생성을 거절함")·의뢰 needs_human
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import (
    AgentResult,
    EventEnvelope,
    EventType,
    RequestMeta,
    TaskStatus,
)
from ax_orchestrator import request_tasks
from ax_orchestrator.engine import ReviewOutcome
from ax_orchestrator.main import app, state
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_intake import RequestIntake
from ax_orchestrator.request_runner import (
    REPLACED_RECORD_KEY,
    VIDEO_APPROVAL,
    VIDEO_REJECT_NOTE,
    VIDEO_WAIT_NOTE,
    RequestRunner,
)
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests

READ = "read"
BRAND = "CR-0123456789ab"
MADE = "CR-fedcba987654"
INSIGHT = {
    "insight_id": "trend_report:rep_1",
    "title": "봄 신간 트렌드",
    "summary": "요약",
    "skill": "trend_report",
    "facts": [{"label": "검색량", "value": "120", "source": "trend"}],
}
VIDEO_PLAN = {"concept": "책방 고양이", "duration_s": 15, "script": "s", "scenes": []}
HUMAN = {"Authorization": "Bearer human-test-token"}
AGENT = {"Authorization": "Bearer shop-test-token"}


def result(status: TaskStatus, outcome: str | None = None, **kw: Any) -> AgentResult:
    meta = RequestMeta(
        context_id="ctx", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentResult(meta=meta, status=status, outcome=outcome, output=kw.pop("output", {}))


class Fake:
    """부서 호출기·레지스트리·안전 관문 되묻기(settle_review)를 흉내 낸다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.decided: dict[str, ReviewOutcome] = {}
        entries = {
            "trend_report_create": ("data", READ),
            "trend_report_insight": ("data", READ),
            "receive_insight": ("planning", "A"),
            "prepare_video_draft_from_plan": ("marketing", READ),
            "generate_media": ("marketing", "B"),
        }

        def find(skill: str) -> SkillEntry | None:
            if skill not in entries:
                return None
            dept, grade = entries[skill]
            return SkillEntry(dept, skill, grade, None, "normal", True)

        self.registry = SimpleNamespace(find=find)
        self.answers: dict[str, list[AgentResult]] = {
            "trend_report_create": [result(TaskStatus.COMPLETED, output={"report_id": "rep_1"})],
            "trend_report_insight": [
                result(TaskStatus.COMPLETED, output={"report_id": "rep_1", "insight": INSIGHT})
            ],
            "receive_insight": [
                result(
                    TaskStatus.COMPLETED,
                    output={"proposal_id": "PROP-1", "video_plan": VIDEO_PLAN},
                )
            ],
            "prepare_video_draft_from_plan": [self.prepared(None)],
            "generate_media": [self.working("t1")],
        }

    @staticmethod
    def prepared(creative: str | None) -> AgentResult:
        media = {"post_draft_id": "spd_1", "media_kind": "video"}
        if creative:
            media["creative_record_id"] = creative
        return result(
            TaskStatus.COMPLETED,
            output={"creative_record_id": creative, "generate_media_input": media},
        )

    @staticmethod
    def working(task_id: str) -> AgentResult:
        return result(TaskStatus.WORKING, output={"task_id": task_id})

    async def caller(self, dept: str, req: Any) -> AgentResult:
        self.calls.append((dept, req))
        queue = self.answers[req.skill]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def settle_review(self, review_id: str | None, claimed: str | None) -> Any:
        return self.decided.get(review_id or "")

    def skills(self) -> list[str]:
        return [c[1].skill for c in self.calls]


def make(reqs: WorkRequests | None = None, task: str = "promo_video") -> tuple[WorkRequests, str]:
    reqs = reqs or WorkRequests()
    row = reqs.create("member", "신간 소개 영상 만들어 주세요")
    plan = request_tasks.plan_of(request_tasks.BY_NAME[task])
    reqs.set_plan(
        row["request_id"], task=task, confidence=0.9, reason="r", alternatives=[], plan=plan
    )
    return reqs, row["request_id"]


async def until_confirm(
    fake: Fake, first: str | None = None
) -> tuple[WorkRequests, str, RequestRunner]:
    """캐릭터 답(기본: 없어요)을 하고 사람 확인 단계까지 돌린다 (11/24: 답 전에는 시작 안 함)."""
    reqs, rid = make()
    r = RequestRunner(reqs, lambda: fake, retry_delay=0)
    await r.run(rid)
    assert fake.calls == []
    assert r.set_brand_character(rid, first) == "ok"
    await r.wait_idle()
    assert reqs.get(rid)["steps"]["confirm"]["status"] == "waiting_human"
    return reqs, rid, r


def done_event(task_id: str, ok: bool, **output: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "skill": "generate_media",
        "status": "done" if ok else "failed",
        "output": output,
    }


async def until_video_held(fake: Fake) -> tuple[WorkRequests, str, RequestRunner]:
    reqs, rid, r = await until_confirm(fake)
    fake.answers["prepare_video_draft_from_plan"] = [Fake.prepared(MADE)]
    assert r.set_brand_character(rid, BRAND, by="준하") == "ok"
    r.decide(rid, "confirm", True, by="준하")
    await r.wait_idle()
    assert reqs.get(rid)["steps"]["make"]["status"] == "waiting_async"
    held = done_event("t1", False, outcome="video_held", review_id="rev_1", creative_record_id=MADE)
    assert r.receive_async("completed", "marketing", held, f"ctx_{rid}") == "resumed"
    await r.wait_idle()
    return reqs, rid, r


# ---------------------------------------------------------------- 캐릭터 정하기


@pytest.mark.asyncio
async def test_brand_character_goes_only_to_the_prepare_step_and_its_record_is_the_result() -> None:
    fake = Fake()
    reqs, rid, r = await until_confirm(fake)
    fake.answers["prepare_video_draft_from_plan"] = [Fake.prepared(MADE)]
    assert r.set_brand_character(rid, BRAND, by="준하") == "ok"
    assert r.set_brand_character(rid, "CR-x") == "invalid"
    r.decide(rid, "confirm", True, by="준하")
    await r.wait_idle()
    prepare = next(c[1] for c in fake.calls if c[1].skill == "prepare_video_draft_from_plan")
    assert prepare.input["brand_character_record_id"] == BRAND
    assert prepare.input["proposal_id"] == "PROP-1"
    make_call = next(c[1] for c in fake.calls if c[1].skill == "generate_media")
    assert "brand_character_record_id" not in make_call.input
    assert make_call.input["creative_record_id"] == MADE  # 마케팅이 정한 의뢰용 기록
    row = reqs.get(rid)
    assert row["steps"]["prepare"]["result_ref"] == MADE
    assert row["brand_character_record_id"] == BRAND
    # 11/26: 영상 준비가 시작된 뒤에도 영상 요청 전이면 바꾼다(다시 준비). 예전 기대값: "started"
    assert r.set_brand_character(rid, None) == "reprepared"
    await r.wait_idle()
    assert reqs.get(rid)["brand_character_record_id"] is None


@pytest.mark.asyncio
async def test_answering_no_character_keeps_the_flow_unchanged() -> None:
    """11/24 전 이름: test_without_an_answer_the_flow_is_unchanged. 이제 답이 없으면 기다리고,
    "없어요"로 답하면 지금 흐름 그대로다."""
    fake = Fake()
    reqs, rid, r = await until_confirm(fake)
    r.decide(rid, "confirm", True)
    await r.wait_idle()
    prepare = next(c[1] for c in fake.calls if c[1].skill == "prepare_video_draft_from_plan")
    assert "brand_character_record_id" not in prepare.input
    row = reqs.get(rid)
    assert row["steps"]["prepare"]["result_ref"] is None
    assert row["steps"]["make"]["status"] == "waiting_async"  # 지금처럼 generate_media(B)
    assert r.receive_async("completed", "marketing", done_event("t1", True), f"ctx_{rid}")
    await r.wait_idle()
    assert reqs.get(rid)["status"] == "done"


@pytest.mark.asyncio
async def test_only_promo_video_requests_take_a_character() -> None:
    reqs, rid = make(task="trend_report")
    r = RequestRunner(reqs, lambda: Fake(), retry_delay=0)
    assert r.set_brand_character(rid, BRAND) == "not_video"
    assert r.set_brand_character("wr_none", BRAND) == "missing"


# ---------------------------------------------------------------- 영상 승인 대기


@pytest.mark.asyncio
async def test_held_video_waits_for_approval_and_is_not_a_failure() -> None:
    fake = Fake()
    reqs, rid, r = await until_video_held(fake)
    row = reqs.get(rid)
    make_step = row["steps"]["make"]
    assert make_step["status"] == "held" and make_step["reason_code"] == VIDEO_APPROVAL
    assert make_step["review_id"] == "rev_1" and make_step["note"] == VIDEO_WAIT_NOTE
    assert row["status"] == "waiting"
    assert r.held_videos() == [(rid, "make", "rev_1")]
    # 아직 사람이 정하지 않았다: 다시 보내지 않는다
    assert await r.recheck_videos() == 0
    assert fake.skills().count("generate_media") == 1


@pytest.mark.asyncio
async def test_approval_resends_the_same_step_and_finishes() -> None:
    fake = Fake()
    reqs, rid, r = await until_video_held(fake)
    fake.decided["rev_1"] = ReviewOutcome(True, "human", "태윤")
    fake.answers["generate_media"] = [Fake.working("t2")]
    assert await r.recheck_videos() == 1
    await r.wait_idle()
    calls = [c[1] for c in fake.calls if c[1].skill == "generate_media"]
    assert len(calls) == 2 and calls[0].input == calls[1].input
    assert calls[0].meta.idempotency_key == calls[1].meta.idempotency_key  # 같은 멱등 키
    assert reqs.get(rid)["steps"]["make"]["status"] == "waiting_async"
    made = done_event("t2", True, media_id="med_x", creative_record_id=MADE)
    r.receive_async("completed", "marketing", made, f"ctx_{rid}")
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["make"]["status"] == "completed" and row["status"] == "done"


@pytest.mark.asyncio
async def test_rejected_video_closes_the_step_and_hands_the_request_to_a_person() -> None:
    fake = Fake()
    reqs, rid, r = await until_video_held(fake)
    fake.decided["rev_1"] = ReviewOutcome(False, "human", "태윤")
    fake.answers["generate_media"] = [Fake.working("t2")]
    await r.recheck_videos()
    await r.wait_idle()
    rejected = done_event("t2", False, outcome="video_rejected", creative_record_id=MADE)
    r.receive_async("completed", "marketing", rejected, f"ctx_{rid}")
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["make"]["status"] == "rejected_by_human"
    assert row["steps"]["make"]["note"] == VIDEO_REJECT_NOTE
    assert row["status"] == "needs_human"


@pytest.mark.asyncio
async def test_safety_decision_event_resumes_the_held_video() -> None:
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    fake = Fake()
    manager.engine = fake  # type: ignore[assignment]  # settle_review만 쓴다
    reqs = manager.work_requests
    r = manager.request_runner
    r._get_engine = lambda: fake  # noqa: SLF001
    r._retry_delay = 0  # noqa: SLF001
    _, rid = make(reqs)
    await r.run(rid)
    fake.answers["prepare_video_draft_from_plan"] = [Fake.prepared(MADE)]
    r.set_brand_character(rid, BRAND)
    await r.wait_idle()  # 11/24: 답을 받아야 데이터분석부터 사람 확인까지 간다
    r.decide(rid, "confirm", True)
    await r.wait_idle()
    held = done_event("t1", False, outcome="video_held", review_id="rev_1", creative_record_id=MADE)
    r.receive_async("completed", "marketing", held, f"ctx_{rid}")
    await r.wait_idle()
    env = EventEnvelope(
        event_id="evt_r1",
        type=EventType.SAFETY_REVIEW_DECIDED,
        occurred_at=datetime.now(UTC),
        source="safety",
        idempotency_key="review:rev_1",
        data={
            "review_id": "rev_1",
            "execution_id": "ex_1",
            "action": "generate_creative_video",
            "requester": "marketing",
            "decision": "approved",
            "decided_by": "태윤",
        },
    )
    # 안전 관문이 아직 승인으로 기록하지 않았으면(위조 등) 따르지 않는다
    assert (await manager._process(env))[0] == "unverified"  # noqa: SLF001
    fake.decided["rev_1"] = ReviewOutcome(True, "human", "태윤")
    fake.answers["generate_media"] = [Fake.working("t2")]
    assert (await manager._process(env))[0] == "resumed"  # noqa: SLF001
    await r.wait_idle()
    assert fake.skills().count("generate_media") == 2


@pytest.mark.asyncio
async def test_deleted_or_other_requests_are_not_resumed() -> None:
    fake = Fake()
    reqs, rid, r = await until_video_held(fake)
    reqs.set_status(rid, "rejected", "사람이 멈춤")
    fake.decided["rev_1"] = ReviewOutcome(True, "human", "태윤")
    assert await r.recheck_videos() == 0
    assert r.resume_video(rid, "make") is False


@pytest.mark.asyncio
async def test_other_company_is_passed_to_departments() -> None:
    fake = Fake()
    reqs, rid = make(WorkRequests(IncidentStore(company_id="nubitour")))
    r = RequestRunner(reqs, lambda: fake, retry_delay=0)
    await r.run(rid)
    r.set_brand_character(rid, None)  # 11/24: 캐릭터 답 뒤에 부서를 부른다
    await r.wait_idle()
    assert {c[1].meta.tenant_id for c in fake.calls} == {"nubitour"}
    fake2 = Fake()
    reqs2, rid2 = make(WorkRequests(IncidentStore(company_id="catbooks")))
    r2 = RequestRunner(reqs2, lambda: fake2, retry_delay=0)
    await r2.run(rid2)
    r2.set_brand_character(rid2, None)
    await r2.wait_idle()
    assert {c[1].meta.tenant_id for c in fake2.calls} == {"shop-demo"}  # 캣북스는 기본값 그대로


# ---------------------------------------------------------------- API


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    store = IncidentStore(company_id="catbooks")
    reqs = WorkRequests(store)
    manager = SimpleNamespace(
        work_requests=reqs,
        request_intake=RequestIntake(reqs),
        request_runner=RequestRunner(reqs, lambda: None, retry_delay=0),
    )
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as c:
        monkeypatch.setattr(state, "manager", manager)
        c.store, c.reqs = store, reqs  # type: ignore[attr-defined]
        yield c


def _path(rid: str) -> str:
    return f"/api/requests/{rid}/brand-character"


def test_api_needs_a_human_token(client: Any) -> None:
    _, rid = make(client.reqs)
    assert client.post(_path(rid), json={"record_id": BRAND}).status_code == 401
    assert client.post(_path(rid), json={"record_id": BRAND}, headers=AGENT).status_code == 403


def test_api_sets_and_clears_until_prepare_starts(client: Any, monkeypatch: Any) -> None:
    # 답하면 다음 단계를 시작한다(11/24). 이 시험은 답·바꾸기 규칙만 본다
    monkeypatch.setattr(state.manager.request_runner, "start", lambda *_a, **_k: None)
    _, rid = make(client.reqs)
    res = client.post(_path(rid), json={"record_id": BRAND}, headers=HUMAN)
    assert res.status_code == 200 and res.json()["data"]["record_id"] == BRAND
    assert res.json()["data"]["plan_outdated"] is False
    assert client.reqs.get(rid)["brand_character_record_id"] == BRAND
    assert client.post(_path(rid), json={"record_id": None}, headers=HUMAN).status_code == 200
    assert client.reqs.get(rid)["brand_character_record_id"] is None
    bad = client.post(_path(rid), json={"record_id": "CR-zz"}, headers=HUMAN)
    assert bad.status_code == 422
    client.reqs.update_step(rid, "prepare", status="running")
    late = client.post(_path(rid), json={"record_id": BRAND}, headers=HUMAN)
    assert late.status_code == 409
    # 11/26: 준비 중이면 잠시 뒤(예전 기대값: "영상 준비가 시작돼서 바꿀 수 없어요")
    assert late.json()["detail"] == "영상 초안을 준비하는 중이에요. 잠시 뒤 다시 바꿔 주세요"
    client.reqs.update_step(rid, "prepare", status="completed")
    client.reqs.update_step(rid, "make", status="held", reason_code=VIDEO_APPROVAL)
    sent = client.post(_path(rid), json={"record_id": BRAND}, headers=HUMAN)
    assert sent.status_code == 409 and sent.json()["detail"] == "영상 요청 뒤라 바꿀 수 없어요"
    assert client.reqs.get(rid)["brand_character_record_id"] is None  # 409면 그대로


def test_api_other_company_request_is_404(client: Any) -> None:
    other = IncidentStore(company_id="nubitour")
    other._db = client.store._db  # noqa: SLF001 - 같은 파일(메모리 DB)을 다른 회사로 쓴다
    _, rid = make(WorkRequests(other))
    assert client.reqs.get(rid) is None
    res = client.post(_path(rid), json={"record_id": BRAND}, headers=HUMAN)
    assert res.status_code == 404
    none = client.post(_path("wr_none"), json={"record_id": BRAND}, headers=HUMAN)
    assert none.status_code == 404


# ---------------------------------------------------------------- 기획에 고객 문장 (11/23)


@pytest.mark.asyncio
async def test_plan_step_gets_the_request_text_with_the_insight() -> None:
    """의뢰 길이("30초 이내")를 기획이 읽게, 넘겨받은 인사이트에 고객 문장 칸을 더한다."""
    fake = Fake()
    await until_confirm(fake)
    plan = next(c[1] for c in fake.calls if c[1].skill == "receive_insight")
    assert plan.input["request_text"] == "신간 소개 영상 만들어 주세요"
    assert plan.input["insight_id"] == INSIGHT["insight_id"]  # 인사이트는 그대로
    insight = next(c[1] for c in fake.calls if c[1].skill == "trend_report_insight")
    assert "request_text" not in insight.input  # 문장 칸이 없는 단계에는 안 붙는다


# ---------------------------------------------------------------- 기획에 고른 캐릭터 (11/26)


@pytest.mark.asyncio
async def test_plan_step_gets_the_chosen_character_number() -> None:
    fake = Fake()
    await until_confirm(fake, first=BRAND)
    plan = next(c[1] for c in fake.calls if c[1].skill == "receive_insight")
    assert plan.input["brand_character_record_id"] == BRAND
    assert plan.input["insight_id"] == INSIGHT["insight_id"]
    assert plan.input["request_text"] == "신간 소개 영상 만들어 주세요"
    assert REPLACED_RECORD_KEY not in plan.input  # 준비 단계 칸은 안 붙는다
    for c in fake.calls:
        if c[1].skill != "receive_insight":
            assert "brand_character_record_id" not in c[1].input


@pytest.mark.asyncio
async def test_no_character_answer_sends_no_number_to_planning() -> None:
    fake = Fake()
    await until_confirm(fake)
    plan = next(c[1] for c in fake.calls if c[1].skill == "receive_insight")
    assert "brand_character_record_id" not in plan.input


def test_only_the_prepare_step_is_the_character_prepare_step() -> None:
    """pass_row는 값만 넘긴다: 캐릭터 바꾸기·넘겨받은 값 남기기가 찾는 단계(row_input)는 그대로."""
    plan = request_tasks.plan_of(request_tasks.PROMO_VIDEO)
    key = request_tasks.BRAND_CHARACTER_KEY
    assert [p["step_id"] for p in plan if key in (p.get("row_input") or [])] == ["prepare"]
    assert [p["step_id"] for p in plan if key in (p.get("pass_row") or [])] == ["plan"]
    other = request_tasks.plan_of(request_tasks.BY_NAME["trend_report"])
    assert all("pass_row" not in p for p in other)  # 다른 일의 계획 모양은 그대로

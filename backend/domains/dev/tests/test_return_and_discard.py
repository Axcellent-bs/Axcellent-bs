"""코드 변경이 아닌 일은 기획에 돌려보내기 (`returned`) · 사람이 개발에 다시 맡기기 (`keep_dev`) ·
멈춘 작업 폐기 (`discarded`).

실제 GitHub·LLM은 부르지 않는다. 형상관리는 파일을 넣은 목 부품, LLM은 가짜 클라이언트다.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError, LLMSettings
from dev_agent import coder, repo_routing, vcs
from dev_agent.coder import CodePlan, CodeReview, FilePick, FileWrite, NotCode
from dev_agent.main import app
from dev_agent.vcs import MockVcsAdapter
from planning_agent.domain.service import ActionError, PlanningService

pytestmark = pytest.mark.role("dev")

BANNER = "shop-web/src/components/Banner.tsx"
FILES = {BANNER: "export function Banner() {\n  return <div>배너</div>\n}\n"}
NEW = "export function Banner() {\n  return <div>신간 배너</div>\n}\n"
SNS = NotCode(reason="인스타그램 게시는 마케팅이 올리는 일", department="marketing")


class FakeLLM:
    """후보 고르기는 정해 둔 계획, 쓰기·리뷰는 늘 같은 답. `fail_plan`이면 계획에서 실패한다."""

    def __init__(self, plan: CodePlan, fail_plan: bool = False):
        self.settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")
        self.plan = plan
        self.fail_plan = fail_plan
        self.plans = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        if schema is CodePlan:
            self.plans += 1
            if self.fail_plan:
                raise LLMResponseError("LLM API가 500를 돌려줬다")
            return self.plan
        if schema is FileWrite:
            return FileWrite(content=NEW, reason="문구")
        if schema is CodeReview:
            return CodeReview(verdict="통과", notes=[])
        raise KeyError(schema.__name__)


NOT_CODE = CodePlan(files=[], summary="", not_code=SNS)
# keep_dev 뒤에는 not_code가 와도 무시하고 고른 파일로 간다
BOTH = CodePlan(
    files=[FilePick(path=BANNER, action="modify", reason="배너")], summary="배너", not_code=SNS
)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files(FILES)
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


def _task(svc: PlanningService, task_id: str = "T-D1"):
    return next(t for t in svc.state.tasks if t.id == task_id)


def _spec(svc: PlanningService, task_id: str = "T-D1"):
    return next(s for s in svc.state.specs if s.id == _task(svc, task_id).spec)


async def _create(svc: PlanningService, title: str = "신간 이벤트 알리기") -> None:
    await svc.create_dev_task(title=title, plan="신간 이벤트를 인스타그램에 올린다", done="게시됨")
    await svc.wait_coding()


async def _retry(svc: PlanningService, task_id: str = "T-D1"):
    out = await svc.retry_dev_task(task_id)
    await svc.wait_coding()
    return out


def _as_planning_handoff(svc: PlanningService, task_id: str = "T-D1") -> None:
    """개발팀 기획을 기획 서비스가 넘긴 기획서처럼 바꾼다 (start_dev로 받은 것과 같게)."""
    sp = _spec(svc, task_id)
    sp.origin, sp.handed_to_dev = None, True


def test_unit_decides_return_only_with_code_signals() -> None:
    job = coder.Job(
        run_id=1, task_id="T-1", spec_id="SPEC-1", request_id="REQ-1", ctx="ctx", title="t",
        plan="p", done=(), hints=(), connection=None, from_planning=True,
    )  # fmt: skip
    back = coder.not_code_stop(job, SNS, has_candidates=False, has_hints=False)
    assert back.status == "returned" and back.fields["returned_to"] == "marketing"
    assert "마케팅" in back.message
    # 고를 파일이 있거나 담당 경로가 레포에 있으면 LLM 판단만으로 튕기지 않는다
    assert coder.not_code_stop(job, SNS, True, False).status == "waiting_human"
    assert coder.not_code_stop(job, SNS, False, True).status == "waiting_human"
    # 개발팀이 직접 낸 기획은 돌려보낼 곳이 없어 사람 확인
    mine = coder.Job(**{**job.__dict__, "from_planning": False})
    assert coder.not_code_stop(mine, SNS, False, False).status == "waiting_human"
    assert coder.hinted(["shop-web/src"], [BANNER]) and not coder.hinted(["sns/"], [BANNER])


def test_dev_team_task_that_is_not_code_waits_for_human(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM(NOT_CODE))
    asyncio.run(_create(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and c.returned_to == "marketing"
    assert "사람 확인" in (c.error or "") and adapter.writes == []


def test_planning_task_is_returned_then_human_keeps_it_in_dev(
    service: PlanningService, adapter
) -> None:
    coder.set_llm(FakeLLM(NOT_CODE, fail_plan=True))
    asyncio.run(_create(service))
    assert _task(service).coding.status == "failed"
    _as_planning_handoff(service)

    coder.set_llm(FakeLLM(NOT_CODE))
    asyncio.run(_retry(service))
    task = _task(service)
    assert task.coding.status == "returned" and task.state == "중단"
    assert task.coding.returned_to == "marketing" and "인스타그램" in task.coding.returned_reason
    assert "code.return" in [a.point for a in service.state.activity]
    assert adapter.writes == [] and not any(p.task == "T-D1" for p in service.state.prs)

    # 사람이 "개발에 다시 맡기기": keep_dev로 확정되고, 다시 돌려보내지 않고 코딩한다
    llm = FakeLLM(BOTH)
    coder.set_llm(llm)
    out = asyncio.run(_retry(service))
    assert out.outcome == "queued" and _spec(service).keep_dev
    assert _task(service).coding.status in ("merged", "opened", "waiting_human")
    assert _task(service).coding.status != "returned" and llm.plans >= 1
    assert any("개발에 다시 맡기기" in a.text for a in service.state.activity)


def test_start_dev_with_keep_dev_restarts_a_returned_task(
    service: PlanningService, adapter
) -> None:
    coder.set_llm(FakeLLM(NOT_CODE, fail_plan=True))
    asyncio.run(_create(service))
    _as_planning_handoff(service)
    coder.set_llm(FakeLLM(NOT_CODE))
    asyncio.run(_retry(service))
    assert _task(service).coding.status == "returned"

    task, sp = _task(service), _spec(service)
    req = next(r for r in service.state.requests if r.id == sp.request)
    spec = sp.model_dump(mode="json", by_alias=True) | {"keep_dev": True}
    kept = task.model_copy()  # 기획이 돌려받은 작업 그대로 다시 넘긴다 (coding returned 포함)

    async def again() -> None:
        await service.start_dev(
            context_id=req.context_id,
            requester="planning",
            request=req.model_dump(mode="json", by_alias=True),
            spec=spec,
            tasks=[kept.model_dump(mode="json", by_alias=True)],
            idempotency_key=f"{req.context_id}:start_dev:{sp.id}:again",
        )

    coder.set_llm(FakeLLM(BOTH))
    asyncio.run(again())
    task = _task(service)
    assert _spec(service).keep_dev
    assert task.coding is None or task.coding.status != "returned"
    assert task.state != "중단"


def test_discard_only_stopped_tasks_and_never_retry(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM(NOT_CODE, fail_plan=True))
    asyncio.run(_create(service))
    assert _task(service).coding.status == "failed"

    out = asyncio.run(service.discard_dev_task("T-D1", "yejin"))
    task = _task(service)
    assert out.outcome == "discarded" and task.discarded and task.discarded_by == "yejin"
    assert task.state == "중단" and task.discarded_at is not None
    assert "dev_task_discard" in [a.point for a in service.state.activity]
    again = asyncio.run(service.discard_dev_task("T-D1"))  # 이미 폐기: 그대로
    assert again.outcome == "discarded" and not again.changed
    with pytest.raises(ActionError) as no_retry:
        asyncio.run(service.retry_dev_task("T-D1"))
    assert no_retry.value.code == "NOT_RETRYABLE"

    # 돌고 있거나 사람 확인(열린 PR 가능)인 작업은 먼저 중단해야 한다
    coder.set_llm(FakeLLM(NOT_CODE))
    asyncio.run(_create(service, "두 번째"))
    assert _task(service, "T-D2").coding.status == "waiting_human"
    with pytest.raises(ActionError) as busy:
        asyncio.run(service.discard_dev_task("T-D2"))
    assert busy.value.code == "NOT_DISCARDABLE"
    asyncio.run(service.cancel_dev_task("T-D2"))
    assert asyncio.run(service.discard_dev_task("T-D2")).outcome == "discarded"
    with pytest.raises(ActionError):
        asyncio.run(service.discard_dev_task("T-NOPE"))


def test_discard_route_needs_a_human_and_maps_errors(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM(NOT_CODE, fail_plan=True))
    asyncio.run(_create(service))
    with TestClient(app) as c:
        r = c.post("/api/planning/dev-tasks/T-D1/discard")
        assert r.status_code == 200 and r.json()["data"]["discarded"] is True
        assert c.post("/api/planning/dev-tasks/T-NOPE/discard").status_code == 404


def test_human_returns_a_task_and_open_pr_is_closed(service: PlanningService, adapter) -> None:
    """사람이 돌려보내기: 열린 PR(사람 확인)은 닫고, returned로 둔다. 머지·폐기 작업은 안 된다."""
    mismatch = CodeReview(verdict="불일치", notes=["아직 아님"])

    class TwoMismatch(FakeLLM):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
            if schema is CodeReview:
                return mismatch
            return await super().structured(prompt, schema, untrusted, images, context_id)

    plan = CodePlan(files=[FilePick(path=BANNER, action="modify", reason="배너")], summary="배너")
    coder.set_llm(TwoMismatch(plan))
    asyncio.run(_create(service, "SNS 게시인데 PR이 열림"))
    task = _task(service)
    assert task.coding.status == "waiting_human" and task.coding.pr

    with pytest.raises(ActionError) as bad_to:
        asyncio.run(service.return_dev_task("T-D1", "boss", "이유"))
    assert bad_to.value.code == "INVALID_INPUT"
    with pytest.raises(ActionError) as no_reason:
        asyncio.run(service.return_dev_task("T-D1", "marketing", "  "))
    assert no_reason.value.code == "INVALID_INPUT"

    out = asyncio.run(
        service.return_dev_task("T-D1", "marketing", "인스타 게시는 마케팅 일", "yejin")
    )
    task = _task(service)
    assert out.outcome == "returned" and task.coding.status == "returned" and task.state == "중단"
    assert (task.coding.returned_to, task.coding.returned_reason) == (
        "marketing",
        "인스타 게시는 마케팅 일",
    )
    pr = next(p for p in service.state.prs if p.task == "T-D1")
    assert pr.closed and not pr.merged
    assert "dev_task_return" in [a.point for a in service.state.activity]
    with pytest.raises(ActionError) as twice:
        asyncio.run(service.return_dev_task("T-D1", "marketing", "또"))
    assert twice.value.code == "NOT_RETURNABLE"

    # 폐기한 작업·머지된 작업은 돌려보낼 수 없다
    asyncio.run(service.discard_dev_task("T-D1"))
    with pytest.raises(ActionError) as gone:
        asyncio.run(service.return_dev_task("T-D1", "marketing", "이유"))
    assert gone.value.code == "NOT_RETURNABLE"
    coder.set_llm(FakeLLM(plan))
    asyncio.run(_create(service, "배너"))
    assert _task(service, "T-D2").coding.status == "merged"
    with pytest.raises(ActionError) as merged:
        asyncio.run(service.return_dev_task("T-D2", "marketing", "이유"))
    assert merged.value.code == "NOT_RETURNABLE"


def test_return_route(service: PlanningService, adapter) -> None:
    coder.set_llm(FakeLLM(NOT_CODE, fail_plan=True))
    asyncio.run(_create(service))
    with TestClient(app) as c:
        url = "/api/planning/dev-tasks/T-D1/return"
        assert c.post(url, json={"to": "marketing"}).status_code == 400
        r = c.post(url, json={"to": "marketing", "reason": "쿠폰 발행은 마케팅"})
        assert r.status_code == 200 and r.json()["data"]["status"] == "returned"
        assert c.post(url, json={"to": "marketing", "reason": "또"}).status_code == 409
        assert (
            c.post(
                "/api/planning/dev-tasks/T-NOPE/return", json={"to": "data", "reason": "x"}
            ).status_code
            == 404
        )

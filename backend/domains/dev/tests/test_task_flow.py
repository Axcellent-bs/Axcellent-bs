"""개발 화면 작업·PR 흐름에서 막히던 곳.

- 열린 PR이 있는데 멈춘 작업은 사람 확인(머지·추가 지시)으로 둔다
  (다시 시도·폐기는 서버가 거절하므로)
- 카드 머지가 형상관리에서 실패하면 성공으로 답하지 않는다 (이유를 돌려준다)
- 앞 작업이 사람 확인을 기다리면 사람이 '지금 진행'으로 순서를 건너뛴다
- 서버 AI로 못 하는 작업을 내 Claude 대기열로 넘긴다 (서버 AI로 넘기기의 반대)
- 코딩 작업 PR의 '다시 리뷰'는 LLM 없이 통과를 기록하지 않는다

실제 GitHub·LLM은 부르지 않는다. 형상관리는 파일을 넣은 목 부품, LLM은 가짜 클라이언트다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError, LLMSettings
from dev_agent import coder, repo_routing, vcs
from dev_agent.coder import CodePlan, CodeReview, FilePick, FileWrite
from dev_agent.main import app
from dev_agent.vcs import MockVcsAdapter, VcsError
from planning_agent.domain.service import ActionError, PlanningService

pytestmark = pytest.mark.role("dev")

BANNER = "shop-web/src/components/Banner.tsx"
NOTICE = "shop-web/src/components/Notice.tsx"
BANNER_OLD = "export function Banner() {\n  return <div>배너</div>\n}\n"
BANNER_NEW = "export function Banner() {\n  return <div className='wrap'>신간 배너</div>\n}\n"
BANNER_FIX = "export function Banner() {\n  return <div className='wrap m'>신간 배너</div>\n}\n"
NOTICE_NEW = "export const NOTICE = '오늘 배송'\n"
FILES = {BANNER: BANNER_OLD}
DONE = "배너에 신간 문구가 보인다"
PASS = CodeReview(verdict="통과", notes=[])
MISMATCH = CodeReview(verdict="불일치", notes=["아직 아님"])


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """스키마별로 정해 둔 답을 준다. `fail`에 든 스키마는 LLM 오류로 답한다."""

    def __init__(self, reviews: list[CodeReview] | None = None, fail: set[str] | None = None):
        self.settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")
        self.reviews = list(reviews or [PASS] * 4)
        self.fail = fail or set()
        self.writes = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        data = untrusted or {}
        if schema.__name__ in self.fail:
            raise LLMResponseError("LLM API가 500를 돌려줬다")
        if schema is CodePlan:
            return CodePlan(
                files=[FilePick(path=BANNER, action="modify", reason="배너 문구 변경")],
                summary="배너 문구",
            )
        if schema is FileWrite:
            self.writes += 1
            body = BANNER_FIX if data.get("instruction") else BANNER_NEW
            return FileWrite(content=body, reason=f"{data['path']} 고침")
        if schema is CodeReview:
            return self.reviews.pop(0) if self.reviews else PASS
        raise KeyError(schema.__name__)


class Merges(MockVcsAdapter):
    """머지를 정해 둔 오류로 막는다 (None이면 그대로 병합)."""

    def __init__(self) -> None:
        super().__init__()
        self.seed_files(FILES)
        self.block: VcsError | None = None

    async def merge_change(self, number: int, method: str = "squash"):
        if self.block is not None:
            raise self.block
        return await super().merge_change(number, method)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = Merges()
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


async def _go(svc: PlanningService, **kw: Any):
    kw.setdefault("title", "신간 배너 문구 바꾸기")
    kw.setdefault("plan", "메인 배너 문구를 신간 안내로 바꾼다.")
    kw.setdefault("done", DONE)
    out = await svc.create_dev_task(**kw)
    await svc.wait_coding()
    return out


async def _settle(svc: PlanningService, coro):
    out = await coro
    await svc.wait_coding()
    return out


def _task(svc: PlanningService, task_id: str = "T-D1"):
    return next(t for t in svc.state.tasks if t.id == task_id)


def _pr(svc: PlanningService, task_id: str = "T-D1"):
    return next(p for p in svc.state.prs if p.task == task_id and not p.closed)


def _waiting_with_pr(svc: PlanningService) -> None:
    """리뷰 불일치 두 번 → 열린 PR로 사람 확인을 기다리는 작업 (T-D1)."""
    run(_go(svc))
    c = _task(svc).coding
    assert c.status == "waiting_human" and c.pr is not None, c.error


# ------------------------------------------------ 1·2. 열린 PR이 있는 실패는 사람 확인으로


def test_auto_merge_failure_waits_for_human_merge(service, adapter) -> None:
    """자동 머지가 형상관리에서 막히면(보호 규칙) 실패가 아니라 사람 확인으로 둔다.
    PR이 열려 있어 다시 시도는 안 되지만, 머지 버튼·추가 지시로 이어 갈 수 있다."""
    adapter.block = VcsError("forbidden", "required status check is expected")
    coder.set_llm(FakeLLM())
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and not c.transient, c.error
    assert "병합 실패" in (c.error or "") and not c.merge_conflict
    assert not _pr(service).merged
    with pytest.raises(ActionError) as err:  # 열린 PR이 있어 다시 쓰지 않는다
        run(service.retry_dev_task("T-D1"))
    assert err.value.code == "NOT_RETRYABLE"

    adapter.block = None  # 보호 규칙이 풀리면 사람 머지 버튼으로 머지한다
    run(service.merge_pr(_pr(service).id))
    assert _task(service).coding.status == "merged"


def test_revise_llm_error_keeps_pr_waiting_not_auto_retried(service, adapter, monkeypatch) -> None:
    """수정 지시 중 LLM 오류: 다시 시도는 PR 때문에 늘 거절되므로 잠깐 오류로 두지 않는다.
    사람 확인(머지·추가 지시)으로 두고, 에이전트 자동 다시 하기 대상에서 뺀다."""
    from dev_agent import service as dev_service

    monkeypatch.setattr(dev_service, "AUTO_RETRY_SECONDS", 0)
    llm = FakeLLM(reviews=[MISMATCH, MISMATCH])
    coder.set_llm(llm)
    _waiting_with_pr(service)
    llm.fail = {"FileWrite"}
    run(_settle(service, service.revise_dev_task("T-D1", "Banner.tsx 여백을 넓혀")))
    c = _task(service).coding
    assert c.status == "waiting_human" and not c.transient, c.error
    assert "LLM" in (c.error or "")
    assert run(service.auto_retry_stuck()) == []
    llm.fail = set()  # 같은 PR에 다시 지시할 수 있다
    out = run(_settle(service, service.revise_dev_task("T-D1", "Banner.tsx 여백을 넓혀")))
    assert out.outcome == "queued"


def test_revise_commit_failure_keeps_pr_waiting(service, adapter) -> None:
    class NoCommit(Merges):
        async def update_change(self, number, files, message):
            raise VcsError("forbidden", "no push")

    broken = NoCommit()
    vcs.set_vcs(broken)
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH, PASS]))
    _waiting_with_pr(service)
    run(_settle(service, service.revise_dev_task("T-D1", "Banner.tsx 여백을 넓혀")))
    c = _task(service).coding
    assert c.status == "waiting_human" and c.pr is not None, c.error


def test_auto_retry_skips_open_pr_tasks(service, adapter, monkeypatch) -> None:
    """예전 상태(열린 PR + 잠깐 오류 실패)가 남아 있어도 자동 다시 하기는 부르지 않는다."""
    from dev_agent import service as dev_service

    monkeypatch.setattr(dev_service, "AUTO_RETRY_SECONDS", 0)
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH]))
    _waiting_with_pr(service)
    c = _task(service).coding
    c.status, c.transient, c.error = "failed", True, "LLM 쓰기 실패"
    assert run(service.auto_retry_stuck()) == []


# ------------------------------------------------ 3. 카드 머지 실패는 이유와 함께 실패로


def test_human_merge_failure_is_an_error_with_reason(service, adapter) -> None:
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH]))
    _waiting_with_pr(service)
    pr = _pr(service)
    adapter.block = VcsError("forbidden", "required review approval")
    with pytest.raises(ActionError) as err:
        run(service.merge_pr(pr.id))
    assert err.value.code == "MERGE_FAILED" and "보호 규칙" in err.value.message
    assert not _pr(service).merged
    c = _task(service).coding
    assert c.status == "waiting_human" and "보호 규칙" in (c.error or "")
    assert any(a.point == "vcs.merge_failed" for a in service.state.activity)  # 기록은 남는다


def test_human_merge_conflict_marks_rewrite(service, adapter) -> None:
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH, PASS]))
    _waiting_with_pr(service)
    first = _pr(service).id
    adapter.block = VcsError("conflict", "merge conflict")
    with pytest.raises(ActionError) as err:
        run(service.merge_pr(first))
    assert "충돌" in err.value.message
    c = _task(service).coding
    assert c.merge_conflict and c.status == "failed" and "충돌" in (c.error or "")
    adapter.block = None  # 충돌이면 다시 시도로 PR을 닫고 최신 코드로 다시 쓴다
    run(_settle(service, service.retry_dev_task("T-D1")))
    assert next(p for p in service.state.prs if p.id == first).closed
    c = _task(service).coding
    assert c.pr not in (None, first) and not c.merge_conflict, c.error


def test_card_merge_api_returns_reason(service, adapter) -> None:
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH]))
    _waiting_with_pr(service)
    adapter.block = VcsError("forbidden", "protected branch")
    with TestClient(app) as c:
        r = c.post("/api/planning/actions", json={"type": "mergePr", "pr": _pr(service).id})
    assert r.status_code == 400
    body = r.json()["error"]
    assert body["code"] == "MERGE_FAILED" and "보호 규칙" in body["message"]


def test_auto_merge_still_quiet_on_failure(service, adapter) -> None:
    """자동 머지(사람 클릭 아님)는 실패해도 작업 흐름을 깨지 않는다 (사람 확인으로만 남김)."""
    adapter.block = VcsError("forbidden", "protected branch")
    coder.set_llm(FakeLLM())
    run(_go(service))  # 예외 없이 끝난다
    assert _task(service).coding.status == "waiting_human"


# ------------------------------------------------ 4. 지금 진행 (순서 건너뛰기)


async def _until(cond, tries: int = 300) -> None:
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("기다린 상태가 오지 않음")


def test_skip_queue_lets_a_waiting_task_go(service, adapter, monkeypatch) -> None:
    """앞 작업이 사람 확인으로 멈춰 있으면 뒤 작업은 계속 기다린다. 사람이 '지금 진행'을 누르면
    기다리지 않고 이어서 쓴다. 기다리는 중이 아닌 작업은 거절한다."""
    monkeypatch.setattr(coder, "QUEUE_POLL_SECONDS", 0.01)
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH, PASS]))
    _waiting_with_pr(service)  # T-D1: 열린 PR로 사람 확인 대기

    with pytest.raises(ActionError) as err:
        run(service.skip_queue_dev_task("T-D1"))
    assert err.value.code == "NOT_WAITING"

    async def flow():
        await service.create_dev_task(title="배너 2", plan="배너를 다시 바꾼다", done=DONE)
        await _until(lambda: _task(service, "T-D2").coding.waiting_for == ["T-D1"])
        out = await service.skip_queue_dev_task("T-D2", "operator")
        await service.wait_coding()
        return out

    out = run(flow())
    assert out.output["skip_queue"] is True
    c = _task(service, "T-D2").coding
    assert c.skip_queue and c.waiting_for == [] and c.status != "queued", c.error
    assert any(a.point == "dev_task_skip_queue" for a in service.state.activity)


def test_skip_queue_route(service, adapter) -> None:
    with TestClient(app) as c:
        r = c.post("/api/planning/dev-tasks/T-NOPE/skip-queue")
    assert r.status_code == 404


# ------------------------------------------------ 8. 내 Claude 대기열로 넘기기


def test_failed_agent_task_moves_to_my_claude(service, adapter) -> None:
    coder.set_llm(FakeLLM(fail={"CodePlan"}))
    run(_go(service))
    assert _task(service).coding.status == "failed"
    out = run(service.hand_to_subscription("T-D1"))
    assert out.output == {"task_id": "T-D1", "status": "queued", "mode": "subscription"}
    c = _task(service).coding
    assert c.mode == "subscription" and c.status == "queued" and c.claimed_by is None
    assert any("내 Claude" in a.text for a in service.state.activity)
    with pytest.raises(ActionError) as err:  # 이미 넘겼으면 안 된다
        run(service.hand_to_subscription("T-D1"))
    assert err.value.code == "NOT_MOVABLE"


def test_to_my_claude_allowed_even_when_company_uses_server_ai(service, adapter) -> None:
    """서버 AI만 쓰는 설정이어도 예산을 다 쓰면 새 작업도 내 Claude로 간다 (coding_choice).
    넘기기도 같게 받는다."""
    coder.set_llm(FakeLLM(fail={"CodePlan"}))
    run(_go(service))
    run(service.set_coding_mode("agent"))
    out = run(service.hand_to_subscription("T-D1"))
    assert out.output["mode"] == "subscription"


def test_to_my_claude_and_retry_race(service, adapter) -> None:
    """내 Claude로 넘기기와 다시 시도를 동시에 누르면 하나만 된다 (잠금 안에서 다시 확인)."""
    for first in ("retry", "move"):
        coder.set_llm(FakeLLM(fail={"CodePlan"}))
        task_id = run(_go(service)).output["task_id"]

        async def both(task_id: str = task_id, first: str = first):
            retry = service.retry_dev_task(task_id)
            move = service.hand_to_subscription(task_id)
            pair = (retry, move) if first == "retry" else (move, retry)
            got = await asyncio.gather(*pair, return_exceptions=True)
            await service.wait_coding()
            return got

        got = run(both())
        errors = [g for g in got if isinstance(g, Exception)]
        assert len(errors) == 1 and isinstance(errors[0], ActionError), (first, got)
        c = _task(service, task_id).coding
        if first == "retry":
            assert errors[0].code == "NOT_MOVABLE" and c.mode == "agent"
        else:
            assert errors[0].code == "NOT_RETRYABLE"
            assert c.mode == "subscription" and c.status == "queued"


def test_to_my_claude_refused_while_auto_retry_runs(service, adapter, monkeypatch) -> None:
    from dev_agent import service as dev_service

    monkeypatch.setattr(dev_service, "AUTO_RETRY_SECONDS", 0)
    llm = FakeLLM(fail={"CodeReview"})  # 잠깐 오류 → 에이전트가 다시 한다
    coder.set_llm(llm)
    run(_go(service))
    assert _task(service).coding.transient

    async def both():
        got = await asyncio.gather(
            service.auto_retry_stuck(), service.hand_to_subscription("T-D1"),
            return_exceptions=True,
        )  # fmt: skip
        await service.wait_coding()
        return got

    retried, moved = run(both())
    assert retried == ["T-D1"] and isinstance(moved, ActionError), moved
    assert _task(service).coding.mode == "agent"


def test_to_my_claude_refused_with_open_pr(service, adapter) -> None:
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH]))
    _waiting_with_pr(service)
    with pytest.raises(ActionError) as err:
        run(service.hand_to_subscription("T-D1"))
    assert err.value.code == "NOT_MOVABLE"


def test_budget_message_points_to_my_claude(service, adapter) -> None:
    """예산을 다 쓰면 내 Claude로 넘기기를 안내한다 (회사가 서버 AI만 써도 넘기기는 된다)."""
    coder.set_llm(FakeLLM(fail={"CodePlan"}))
    run(_go(service))
    run(service.set_budget(0))
    run(service.set_coding_mode("agent"))
    with pytest.raises(ActionError) as err:
        run(service.retry_dev_task("T-D1"))
    assert err.value.code == "BUDGET_EXHAUSTED" and "내 Claude로 넘기" in err.value.message
    assert run(service.hand_to_subscription("T-D1")).output["mode"] == "subscription"


def test_to_subscription_route(service, adapter) -> None:
    coder.set_llm(FakeLLM(fail={"CodePlan"}))
    run(_go(service))
    with TestClient(app) as c:
        r = c.post("/api/planning/dev-tasks/T-D1/to-subscription")
        assert r.status_code == 200 and r.json()["data"]["mode"] == "subscription"
        again = c.post("/api/planning/dev-tasks/T-D1/to-subscription")
    assert again.status_code == 409


# ------------------------------------------------ 9. 다시 리뷰는 통과를 지어내지 않는다


def test_rereview_of_coding_pr_is_refused(service, adapter) -> None:
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH]))
    _waiting_with_pr(service)
    pr = _pr(service)
    before = list(pr.reviews)
    with pytest.raises(ActionError) as err:
        run(service.review_pr(pr.id))
    assert err.value.code == "NOT_REVIEWABLE" and "추가 지시" in err.value.message
    assert _pr(service).reviews == before and not _pr(service).merged


def test_human_merge_failure_keeps_my_claude_hold(service, adapter) -> None:
    """내 Claude가 쥐고 있는 작업(claimed·writing)은 머지 실패로 상태를 바꾸지 않는다."""
    coder.set_llm(FakeLLM(reviews=[MISMATCH, MISMATCH]))
    _waiting_with_pr(service)
    c = _task(service).coding
    c.mode, c.status, c.error = "subscription", "writing", None
    adapter.block = VcsError("forbidden", "protected branch")
    with pytest.raises(ActionError):
        run(service.merge_pr(_pr(service).id))
    c = _task(service).coding
    assert c.status == "writing" and c.error is None


def test_skip_queue_checks_budget_before_starting(service, adapter) -> None:
    """기다리던 실행이 없어 새로 띄워야 하면 예산을 본다. 폐기한 작업은 받지 않는다."""
    coder.set_llm(FakeLLM(fail={"CodePlan"}))
    run(_go(service))
    c = _task(service).coding
    c.status, c.waiting_for = "queued", ["T-0"]  # 순서를 기다리던 채 실행이 없음 (재시작 등)
    run(service.set_budget(0))
    with pytest.raises(ActionError) as err:
        run(service.skip_queue_dev_task("T-D1"))
    assert err.value.code == "BUDGET_EXHAUSTED"
    assert not _task(service).coding.skip_queue
    _task(service).discarded = True
    with pytest.raises(ActionError) as err:
        run(service.skip_queue_dev_task("T-D1"))
    assert err.value.code == "NOT_WAITING"

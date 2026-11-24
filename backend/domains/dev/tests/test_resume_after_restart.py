"""서버를 다시 켜서 멈춘 서버 AI 코딩 작업은 켤 때 한 번 이어서 다시 한다 (11/24).

자동 배포로 서버가 자주 다시 켜진다. 돌던·순서를 기다리던 작업이 실패로 남아 사람이 하나씩
"다시 시도"를 눌러야 했다. 이제 store.load가 표시(`restart_resume`)를 남기고, 개발 서비스가 켤 때
사람 다시 시도와 같은 길(`retry_dev_task`, 관문 dev_task_retry)로 한 번 잇는다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMSettings
from ax_workflow.models import PR, CodingRun, CodingStatus, PrGate
from dev_agent import coder, repo_routing, vcs
from dev_agent import service as dev_service
from dev_agent.coder import CodePlan, CodeReview, FilePick, FileWrite
from dev_agent.service import DevService
from dev_agent.vcs import MockVcsAdapter

pytestmark = pytest.mark.role("dev")

BANNER = "shop-web/src/components/Banner.tsx"
BANNER_OLD = "export function Banner() {\n  return <div>배너</div>\n}\n"
BANNER_NEW = "export function Banner() {\n  return <div className='wrap'>신간 배너</div>\n}\n"
BANNER_FIX = "export function Banner() {\n  return <div className='wrap m'>신간 배너</div>\n}\n"
DONE = "배너에 신간 문구가 보인다"
RESUMED = "서버 재시작 뒤 이어서 다시 시도"


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """배너 하나를 고르고 쓰고 통과 리뷰를 준다. `fail`이면 후보 고르기에서 멈춘다.
    `hold`가 있으면 첫 리뷰는 그 이벤트가 켜질 때까지 기다린다 (앞 작업이 도는 중)."""

    def __init__(self, fail: bool = False, hold: asyncio.Event | None = None) -> None:
        self.settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")
        self.fail = fail
        self.hold = hold
        self.writes = 0
        self.reviewed = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        if self.fail:
            from ax_agent_template.llm import LLMResponseError

            raise LLMResponseError("LLM API가 500를 돌려줬다")
        if schema is CodePlan:
            picks = [FilePick(path=BANNER, action="modify", reason="배너 문구")]
            return CodePlan(files=picks, summary="배너")
        if schema is FileWrite:
            self.writes += 1
            return FileWrite(content=BANNER_NEW if self.writes == 1 else BANNER_FIX, reason="고침")
        if schema is CodeReview:
            if self.hold is not None and self.reviewed == 0:
                await self.hold.wait()
            self.reviewed += 1
            return CodeReview(verdict="통과", notes=[])
        raise KeyError(schema.__name__)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files({BANNER: BANNER_OLD})
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


def _task(svc: DevService, task_id: str = "T-D1"):
    return next(t for t in svc.state.tasks if t.id == task_id)


async def _make(svc: DevService, n: int) -> None:
    """작업 n개를 만든다 (LLM이 실패해 failed로 멈춘다)."""
    for i in range(n):
        await svc.create_dev_task(title=f"배너 {i + 1}", plan="배너 문구를 바꾼다", done=DONE)
        await svc.wait_coding()


def _stopped_by_restart(
    tmp_path, monkeypatch: pytest.MonkeyPatch, n: int = 1, status: CodingStatus = "writing"
) -> DevService:
    """작업 n개가 `status`로 돌던 중에 서버가 꺼졌다가 다시 켜진 서비스."""
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = DevService()
    coder.set_llm(FakeLLM(fail=True))
    run(_make(svc, n))
    for i in range(n):
        task = _task(svc, f"T-D{i + 1}")
        assert task.coding is not None and task.coding.status == "failed"
        task.coding.status = status
        task.coding.waiting_for = ["T-D1"] if i else []
    svc._save()
    return DevService()  # 다시 켬: 파일에서 읽는다


async def _resume(svc: DevService) -> list[str]:
    out = await svc.resume_after_restart()
    await svc.wait_coding()
    return out


def test_restart_marks_and_resumes_once_through_retry(tmp_path, monkeypatch, adapter) -> None:
    again = _stopped_by_restart(tmp_path, monkeypatch)
    cr = _task(again).coding
    assert cr.status == "failed" and "다시 켜서" in (cr.error or "") and cr.restart_resume

    coder.set_llm(FakeLLM())
    assert run(_resume(again)) == ["T-D1"]
    cr = _task(again).coding
    assert cr.status == "merged", cr.error
    assert not cr.restart_resume and cr.restart_resumes == 1
    # 사람 다시 시도와 같은 관문(dev_task_retry)을 지나고, 같은 모양의 활동 한 줄을 남긴다
    assert [g.action for g in again.gate_calls].count("dev_task_retry") == 1
    rows = [a for a in again.state.activity if a.point == "dev_task_retry"]
    assert rows and RESUMED in rows[-1].text
    assert run(_resume(again)) == []  # 이번 켜기에서는 한 번만


def test_restart_during_resumed_run_resumes_once_more(tmp_path, monkeypatch, adapter) -> None:
    """이어서 하던 실행 중에 또 꺼지면 다음 켤 때 다시 한 번 잇는다 (켤 때마다 한 번)."""
    again = _stopped_by_restart(tmp_path, monkeypatch)
    coder.set_llm(FakeLLM(fail=True))
    assert run(_resume(again)) == ["T-D1"]
    cr = _task(again).coding
    assert cr.status == "failed" and not cr.restart_resume and cr.restart_resumes == 1
    assert run(_resume(again)) == []  # 같은 켜기에서 또 돌지 않는다

    cr.status = "reviewing"  # 이어서 하던 중 또 꺼짐
    again._save()
    third = DevService()
    assert _task(third).coding.restart_resume
    coder.set_llm(FakeLLM())
    assert run(_resume(third)) == ["T-D1"]
    cr = _task(third).coding
    assert cr.status == "merged" and cr.restart_resumes == 2


def test_resume_stops_after_max_and_human_retry_resets(tmp_path, monkeypatch, adapter) -> None:
    again = _stopped_by_restart(tmp_path, monkeypatch)
    _task(again).coding.restart_resumes = dev_service.RESTART_RESUME_MAX
    coder.set_llm(FakeLLM())
    assert run(_resume(again)) == []
    cr = _task(again).coding
    assert cr.status == "failed" and not cr.restart_resume  # 사람 다시 시도로 남긴다
    assert not any(RESUMED in a.text for a in again.state.activity)

    async def retry() -> None:
        await again.retry_dev_task("T-D1")
        await again.wait_coding()

    run(retry())
    cr = _task(again).coding
    assert cr.status == "merged" and cr.restart_resumes == 0


def test_resume_refused_by_budget_leaves_failed(tmp_path, monkeypatch, adapter) -> None:
    again = _stopped_by_restart(tmp_path, monkeypatch)
    again.state.monthly_budget_tokens = 0  # 이번 달 서버 AI 예산을 다 씀
    coder.set_llm(FakeLLM())
    assert run(_resume(again)) == []
    cr = _task(again).coding
    assert cr.status == "failed" and not cr.restart_resume
    assert "다시 켜서" in (cr.error or "")
    assert run(_resume(again)) == []


def test_open_pr_subscription_and_discarded_are_not_resumed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    svc = DevService()
    base = svc.state.tasks[0]
    with_pr = base.model_copy(update={"id": "T-PR"})
    sub = base.model_copy(update={"id": "T-SUB"})
    gone = base.model_copy(update={"id": "T-GONE", "discarded": True})
    svc.state.tasks += [with_pr, sub, gone]
    now = svc.clock()
    with_pr.coding = CodingRun(status="reviewing", updated_at=now, pr="PR #90")
    svc.state.prs.append(
        PR(
            id="PR #90", number=90, task="T-PR", title="R1: x", branch="gen/x",
            reviews=[], gate=PrGate(result="통과", lines=1, files=1, paths="x"), merged=False,
        )
    )  # fmt: skip
    sub.coding = CodingRun(status="queued", updated_at=now, mode="subscription")
    gone.coding = CodingRun(status="writing", updated_at=now)
    svc._save()

    again = DevService()
    by = {t.id: t.coding for t in again.state.tasks}
    assert by["T-PR"].status == "waiting_human" and not by["T-PR"].restart_resume
    assert by["T-SUB"].status == "queued" and not by["T-SUB"].restart_resume
    assert by["T-GONE"].restart_resume  # 표시는 남지만 폐기한 작업은 잇지 않는다
    assert run(_resume(again)) == []
    assert not _task(again, "T-GONE").coding.restart_resume
    assert not any(g.action == "dev_task_retry" for g in again.gate_calls)


def test_resume_keeps_queue_order(tmp_path, monkeypatch, adapter) -> None:
    """순서를 기다리던 뒤 작업은 다시 이어도 평소처럼 앞 작업을 기다린다 (한꺼번에 쓰지 않는다)."""
    monkeypatch.setattr(coder, "QUEUE_POLL_SECONDS", 0.01)
    again = _stopped_by_restart(tmp_path, monkeypatch, n=2, status="queued")
    assert _task(again, "T-D2").coding.waiting_for == []  # 읽을 때 비운다
    hold = asyncio.Event()
    coder.set_llm(FakeLLM(hold=hold))

    async def until(cond: Any) -> None:
        for _ in range(300):
            if cond():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("기다린 상태가 오지 않음")

    async def flow() -> None:
        assert await again.resume_after_restart() == ["T-D1", "T-D2"]
        await until(lambda: _task(again).coding.status == "reviewing")
        await until(lambda: _task(again, "T-D2").coding.waiting_for == ["T-D1"])
        assert _task(again, "T-D2").coding.status == "queued"
        hold.set()
        await again.wait_coding()

    run(flow())
    first, second = _task(again), _task(again, "T-D2")
    assert first.coding.status == "merged" and second.coding.status == "merged"
    assert run(adapter.read_file(BANNER)) == BANNER_FIX  # 앞 작업 머지 뒤 글 위에서 씀


def test_lifespan_resumes_only_outside_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """켤 때 이어서 하기는 주기 루프처럼 목 모드(시험 전용)가 아닐 때만 돈다."""
    import dev_agent.main as dev_main
    from dev_agent.runtime import get_service
    from dev_agent.vcs import inventory

    svc = get_service()
    calls: list[str] = []

    async def fake_resync(wait_peer: bool = False) -> None:
        return None

    async def fake_resume(wait_gate: bool = False) -> list[str]:
        calls.append(f"resume wait_gate={wait_gate}")
        return []

    monkeypatch.setattr(svc, "resync_peer", fake_resync)
    monkeypatch.setattr(svc, "resume_after_restart", fake_resume)
    with TestClient(dev_main.app) as client:  # 목 모드
        client.get("/health")
    assert calls == []

    monkeypatch.setattr(dev_main, "_runs_loops", lambda: True)
    monkeypatch.setattr(svc, "start_pr_sync", lambda: None)
    monkeypatch.setattr(svc, "stop_pr_sync", lambda: None)
    monkeypatch.setattr(inventory, "start_daily", lambda: None)
    monkeypatch.setattr(inventory, "stop_daily", lambda: None)
    with TestClient(dev_main.app) as client:
        for _ in range(50):
            client.get("/health")
            if calls:
                break
            asyncio.run(asyncio.sleep(0.02))
    assert calls == ["resume wait_gate=True"]  # 켤 때는 관문이 뜰 때까지 기다린 뒤 잇는다


# ---------------------------------------------------------------- 관문이 같이 다시 켜질 때


def test_resume_waits_until_gate_is_up(tmp_path, monkeypatch, adapter) -> None:
    from ax_workflow import base as workflow_base

    again = _stopped_by_restart(tmp_path, monkeypatch)
    probes: list[bool] = []
    sleeps: list[float] = []

    async def probe() -> bool:
        probes.append(len(probes) >= 2)  # 세 번째에 뜬다
        return probes[-1]

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(again, "_safety_up", probe)
    monkeypatch.setattr(workflow_base, "_sleep", no_sleep)
    coder.set_llm(FakeLLM())

    async def go() -> list[str]:
        out = await again.resume_after_restart(wait_gate=True)
        await again.wait_coding()
        return out

    assert run(go()) == ["T-D1"]
    assert probes == [False, False, True] and len(sleeps) == 2
    assert _task(again).coding.status == "merged"


def test_unreachable_gate_keeps_mark_for_next_boot(tmp_path, monkeypatch, adapter) -> None:
    """관문에 닿지 못한 거부(exe_unavailable)는 판단이 아니다: 표시를 남겨 다음 켤 때 다시 본다."""
    again = _stopped_by_restart(tmp_path, monkeypatch)
    ok_gate = again._gate

    async def down(action, params, meta, evidence):
        raise ConnectionError("safety 아직 안 뜸")  # core가 exe_unavailable 거부로 바꾼다

    again._gate = down
    coder.set_llm(FakeLLM())
    assert run(_resume(again)) == []
    cr = _task(again).coding
    assert cr.status == "failed" and cr.restart_resume and cr.restart_resumes == 0
    assert again.gate_calls[-1].execution_id == dev_service.GATE_UNAVAILABLE

    again._gate = ok_gate  # 관문이 떴다 (다음 켜기)
    assert run(_resume(again)) == ["T-D1"]
    assert _task(again).coding.status == "merged"


def test_gate_policy_rejection_drops_mark(tmp_path, monkeypatch, adapter) -> None:
    """관문이 판단해 거절한 것은 사람 다시 시도로 남긴다 (표시를 지운다)."""
    from ax_contracts.schemas import ExecuteDecision

    again = _stopped_by_restart(tmp_path, monkeypatch)

    async def refuse(action, params, meta, evidence):
        return ExecuteDecision(execution_id="exe_no", grade="C", status="rejected")

    again._gate = refuse
    assert run(_resume(again)) == []
    cr = _task(again).coding
    assert cr.status == "failed" and not cr.restart_resume


def test_human_retry_and_resume_at_once_run_once(tmp_path, monkeypatch, adapter) -> None:
    """사람 "다시 시도"와 켤 때 이어서 하기가 겹쳐도 한 번만 돈다 (잠금 안에서 다시 본다)."""
    again = _stopped_by_restart(tmp_path, monkeypatch)
    coder.set_llm(FakeLLM())

    async def both() -> list[Any]:
        out = await asyncio.gather(
            again.retry_dev_task("T-D1"), again.resume_after_restart(), return_exceptions=True
        )
        await again.wait_coding()
        return out

    human, resumed = run(both())
    assert not isinstance(human, BaseException)  # 사람이 먼저 잡았다
    assert resumed == []
    assert [g.action for g in again.gate_calls].count("dev_task_retry") == 1
    assert sum(1 for p in again.state.prs if p.task == "T-D1") == 1  # PR도 하나
    cr = _task(again).coding
    assert cr.status == "merged" and not cr.restart_resume

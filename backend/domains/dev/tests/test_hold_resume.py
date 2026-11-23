"""관문이 보류한 개발 AI 액션을 사람이 승인하면 한 번만 이어서 한다 (hold_resume).

실제 safety·GitHub·LLM은 부르지 않는다. 관문은 키별로 보류·승인을 흉내 내는 가짜(`KeyGate`),
형상관리는 목 부품, LLM은 가짜 클라이언트다. 승인된 키를 다시 보내면 `executed`를 돌려주는 것은
safety의 멱등 재생(gate.py `idempotent_replay`)과 같다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from ax_workflow.core import ActionError, GateRefused
from dev_agent import coder, hold_resume, repo_routing, vcs
from dev_agent.coder import CodePlan, CodeReview, FilePick, FileWrite
from dev_agent.hold_resume import HoldStore, decide, poll_once
from dev_agent.main import app
from dev_agent.service import DevService
from dev_agent.vcs import MockVcsAdapter, VcsError

pytestmark = pytest.mark.role("dev")

BANNER = "shop-web/src/components/Banner.tsx"
CHECKOUT = "shop-web/src/pages/Checkout.tsx"
FILES = {
    BANNER: "export function Banner() {\n  return <div>배너</div>\n}\n",
    CHECKOUT: "export function Checkout() {\n  return null\n}\n",
}


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    def __init__(self, path: str = BANNER) -> None:
        self.path = path
        self.calls = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        self.calls += 1
        if schema is CodePlan:
            return CodePlan(files=[FilePick(path=self.path, reason="문구")], summary="문구")
        if schema is FileWrite:
            return FileWrite(content="export const X = 1\n", reason="고침")
        if schema is CodeReview:
            return CodeReview(verdict="통과", notes=[])
        raise KeyError(schema.__name__)


class KeyGate:
    """`hold` 액션은 승인된 키만 실행하고 나머지는 보류한다. 키마다 보류 건은 하나."""

    def __init__(self, *hold: str) -> None:
        self.hold = set(hold)
        self.reviews: dict[str, str] = {}
        self.approved: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, action, params, meta, evidence):
        key = meta.idempotency_key
        self.calls.append((action, key))
        if action in self.hold and key not in self.approved:
            rid = self.reviews.setdefault(key, f"rev_{len(self.reviews) + 1}")
            return ExecuteDecision(
                execution_id="x", grade="C", status="held", review_id=rid,
                hold_reason="over_threshold",
            )  # fmt: skip
        return ExecuteDecision(execution_id="ok", grade="A", status="executed")

    def approve(self, rid: str) -> None:
        self.approved.add(next(k for k, v in self.reviews.items() if v == rid))

    def count(self, action: str) -> int:
        return sum(1 for a, _ in self.calls if a == action)


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


async def _go(svc: DevService, **kw: Any) -> None:
    await svc.create_dev_task(
        title="배너 문구", plan="배너 문구를 바꾼다.", done="배너가 보인다", **kw
    )
    await svc.wait_coding()


async def _decide(svc: DevService, rid: str, how: str = "approved") -> Any:
    row = await decide(svc, rid, how, "사람", "test")  # type: ignore[arg-type]
    await svc.wait_coding()
    return row


def _task(svc: DevService):
    return next(t for t in svc.state.tasks if t.id == "T-D1")


def _vcs_number(service: DevService, pr: str) -> int:
    n = next(p for p in service.state.prs if p.id == pr).vcs_number
    assert n is not None
    return n


def _held_pr(service: DevService, gate: KeyGate) -> tuple[str, str]:
    """보호 경로 PR(사람 확인) → 사람 머지 버튼 → 관문이 merge_pr 보류. (PR id, review_id)."""
    coder.set_llm(FakeLLM(CHECKOUT))
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and c.pr, c.error
    with pytest.raises(GateRefused):
        run(service.merge_pr(c.pr))
    rid = gate.reviews[next(k for a, k in gate.calls if a == "merge_pr")]
    return c.pr, rid


# ---------------------------------------------------------------- PR 열기 (generate_pr)


def test_approved_generate_pr_resumes_once(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    c = _task(service).coding
    assert c.status == "waiting_human" and c.pr is None and c.hold_review == "rev_1"
    row = service.holds.get("rev_1")
    assert row is not None and (row.kind, row.status) == ("coding_pr", "pending")
    assert row.args == {"task_id": "T-D1", "key": c.hold_key}

    gate.approve("rev_1")
    row = run(_decide(service, "rev_1"))
    c = _task(service).coding
    assert row.status == "done" and "PR 열기" in (row.reason or "")
    assert c.pr is not None and c.status in ("opened", "merged"), c.error
    assert gate.count("generate_pr") == 2  # 보류 1번 + 승인 뒤 같은 키 1번
    keys = [k for a, k in gate.calls if a == "generate_pr"]
    assert keys[0] == keys[1]  # 보류 때 키 그대로 (관문이 승인된 판정을 돌려준다)
    assert any(a.point == "hold.resume" for a in service.state.activity)

    # 중복 승인 이벤트·폴링·사람 버튼: 다시 하지 않는다
    run(_decide(service, "rev_1"))
    with pytest.raises(ActionError):  # PR이 열린 뒤 버튼은 지금처럼 거절
        run(service.retry_dev_task("T-D1", approved=True))
    assert gate.count("generate_pr") == 2
    assert len(adapter._entries) == 1


def test_concurrent_approvals_run_once(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    gate.approve("rev_1")

    async def both() -> None:
        await asyncio.gather(
            decide(service, "rev_1", "approved", "a", "event"),
            decide(service, "rev_1", "approved", "b", "poll"),
        )
        await service.wait_coding()

    run(both())
    assert gate.count("generate_pr") == 2
    assert service.holds.get("rev_1").status == "done"
    assert _task(service).coding.pr is not None


def test_button_after_auto_resume_does_not_run_again(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    gate.approve("rev_1")

    async def race() -> Any:
        row = await decide(service, "rev_1", "approved", "a", "event")
        out = await service.retry_dev_task("T-D1", approved=True)  # 화면 버튼이 뒤따라옴
        await service.wait_coding()
        return row, out

    row, out = run(race())
    assert row.status == "done" and out.outcome == "already_resumed"
    assert gate.count("generate_pr") == 2 and len(adapter._entries) == 1


def test_rejected_or_expired_does_not_run(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    row = run(_decide(service, "rev_1", "rejected"))
    assert row.status == "rejected" and "거절" in (row.reason or "")
    assert gate.count("generate_pr") == 1
    c = _task(service).coding
    assert c.status == "waiting_human" and c.pr is None

    # 그 뒤 승인이 와도(순서가 뒤바뀐 이벤트) 다시 하지 않는다
    gate.approve("rev_1")
    run(_decide(service, "rev_1"))
    assert gate.count("generate_pr") == 1

    # 만료는 폴링으로
    renewed = run(service.renew_hold("T-D1"))
    rid = renewed.output["review_id"]

    async def expired(review_id: str) -> dict:
        return {"status": "expired"}

    assert run(poll_once(service, expired)) == 1
    assert service.holds.get(rid).status == "expired"
    assert gate.count("generate_pr") == 2  # renew 1번뿐


def test_poll_approved_resumes(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    gate.approve("rev_1")

    async def approved(review_id: str) -> dict:
        return {"status": "approved", "decided_by": "kim"}

    async def go() -> int:
        n = await poll_once(service, approved)
        await service.wait_coding()
        return n

    assert run(go()) == 1
    assert run(go()) == 0  # 대기 건이 없다
    row = service.holds.get("rev_1")
    assert row.status == "done" and row.decided_by == "kim"
    assert _task(service).coding.pr is not None and gate.count("generate_pr") == 2


def test_stale_cancelled_task_is_skipped_with_reason(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    run(service.cancel_dev_task("T-D1"))
    gate.approve("rev_1")
    row = run(_decide(service, "rev_1"))
    assert row.status == "skipped" and "canceled" in (row.reason or "")
    assert gate.count("generate_pr") == 1 and _task(service).coding.pr is None


# ---------------------------------------------------------------- 머지 (merge_pr)


def test_approved_merge_resumes_once(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)
    row = service.holds.get(rid)
    head = adapter._entries[_vcs_number(service, pr)].commits[-1].sha
    assert row is not None and (row.kind, row.args) == ("merge_pr", {"pr": pr, "head": head})
    assert row.key.endswith(f":merge_pr:{pr}:{head[:12]}")  # (PR, 머리 커밋)마다 키 하나
    assert not next(p for p in service.state.prs if p.id == pr).merged

    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "done" and "머지함" in (row.reason or "")
    assert next(p for p in service.state.prs if p.id == pr).merged
    assert _task(service).coding.status == "merged"
    merges = [w for w in adapter.writes if w[0] == "merge_change"]
    run(_decide(service, rid))
    assert [w for w in adapter.writes if w[0] == "merge_change"] == merges and len(merges) == 1


def test_merge_already_done_by_human_is_skipped(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)
    gate.approve(rid)
    run(service.merge_pr(pr))  # 사람이 승인하고 같은 버튼을 다시 누름 (같은 키 → 머지)
    row = run(_decide(service, rid))
    assert row.status == "skipped" and "이미 머지됨" in (row.reason or "")
    assert len([w for w in adapter.writes if w[0] == "merge_change"]) == 1


def test_resumed_merge_failure_is_recorded(
    service: DevService, adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)

    async def broken(number: int, method: str = "merge", sha: str | None = None) -> Any:
        raise VcsError("unavailable", "형상관리 서버가 응답하지 않음")

    monkeypatch.setattr(adapter, "merge_change", broken)
    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "failed" and "MERGE_FAILED" in (row.reason or "")
    assert not next(p for p in service.state.prs if p.id == pr).merged
    assert "보류 승인 뒤 이어서 하기 실패" in (_task(service).coding.error or "")
    assert any(a.point == "hold.resume" and "실패" in a.text for a in service.state.activity)


# ---------------------------------------------------------------- 레포 작업 공간 (repo_write)


def test_repo_merge_resumes_with_held_key(service: DevService, adapter) -> None:
    """레포 쓰기는 누를 때마다 새 키(`:n`)라 다시 눌러도 또 보류됐다. 승인 뒤 보류 때 키로."""
    gate = KeyGate("merge_pr")
    service._gate = gate
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    client = TestClient(app)
    res = client.post(f"/api/planning/repo/prs/{n}/merge", json={"method": "squash"})
    assert res.status_code != 200
    (rid,) = gate.reviews.values()
    held_key = next(iter(gate.reviews))
    row = service.holds.get(rid)
    assert row is not None and row.kind == "repo_write" and row.args["op"]["method"] == "squash"

    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "done", row.reason
    assert ("merge_change", n) in adapter.writes
    assert gate.calls[-1] == ("merge_pr", held_key)  # 보류 때 키 그대로 (관문이 승인 판정 재생)
    assert client.get("/api/planning/holds").status_code == 401  # 부르는 사람 토큰 필요
    res = client.get("/api/planning/holds", headers={"Authorization": "Bearer human:kim"})
    listed = res.json()["data"]
    assert listed[0]["review_id"] == rid and listed[0]["status"] == "done"
    assert "args" not in listed[0] and set(listed[0]) == {
        "review_id", "kind", "action", "status", "reason", "created_at", "finished_at", "task_id",
    }  # fmt: skip


# ---------------------------------------------------------------- 다시 켜기


def test_restart_does_not_rerun(
    service: DevService, adapter, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    gate = KeyGate("merge_pr")
    service._gate = gate
    service.holds = HoldStore(service.clock)
    pr, rid = _held_pr(service, gate)
    gate.approve(rid)
    run(_decide(service, rid))
    merges = len([w for w in adapter.writes if w[0] == "merge_change"])

    service.holds = HoldStore(service.clock)  # 다시 켬: 파일에서 읽는다
    assert service.holds.get(rid).status == "done"
    run(_decide(service, rid))
    assert len([w for w in adapter.writes if w[0] == "merge_change"]) == merges == 1


def test_restart_mid_resume_marks_failed_not_rerun(
    service: DevService, adapter, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    gate = KeyGate("merge_pr")
    service._gate = gate
    service.holds = HoldStore(service.clock)
    pr, rid = _held_pr(service, gate)
    assert service.holds.claim(rid, "kim") is not None  # 이어서 하던 중 꺼졌다고 친다

    service.holds = HoldStore(service.clock)
    row = service.holds.get(rid)
    assert row.status == "failed" and row.reason == hold_resume.INTERRUPTED
    gate.approve(rid)
    run(_decide(service, rid))
    assert not next(p for p in service.state.prs if p.id == pr).merged


def test_unknown_review_and_no_recipe_are_ignored(service: DevService, adapter) -> None:
    assert run(decide(service, "rev_unknown", "approved")) is None
    d = ExecuteDecision(
        execution_id="x", grade="C", status="held", review_id="rev_x", hold_reason="over_threshold"
    )
    assert service.holds.note("merge_pr", "k", "ctx", d) is None  # 적어 둔 것 없음 → 남기지 않음


# ---------------------------------------------------------------- 장애 되돌리기 (revert_merged_pr)


def test_incident_revert_resumes_through_both_gates(service: DevService, adapter) -> None:
    """되돌리기 PR 열기 보류 → 승인 → 열기 → 머지 보류(새 보류 건) → 승인 → 머지. 단계마다 한 번."""
    from dev_agent import incident_repo

    opened = run(adapter.open_change("gen/pay", "요율", "", files={BANNER: "rate = 0\n"}))
    run(adapter.merge_change(opened.number, "squash"))
    gate = KeyGate("repo_revert_pr", "merge_pr")
    service._gate = gate
    out = run(
        incident_repo.revert_merged(
            service, context_id="c", requester="infra", connection_id=vcs.OVERRIDE_ID,
            pr_number=opened.number, reason="결제 오류",
        )
    )  # fmt: skip
    assert out.status == "held" and out.revert_pr_number is None
    (rid,) = gate.reviews.values()
    assert service.holds.get(rid).kind == "incident_revert"

    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "held_again" and row.next_review
    assert gate.count("repo_revert_pr") == 2  # 보류 1번 + 승인 뒤 같은 키 1번
    nxt = row.next_review
    assert service.holds.get(nxt).kind == "incident_revert"

    gate.approve(nxt)
    row = run(_decide(service, nxt))
    assert row.status == "done", row.reason
    assert gate.count("repo_revert_pr") == 2  # 이미 연 되돌리기 PR을 쓴다 (다시 열지 않음)
    reverts = [w for w in adapter.writes if w[0] == "revert_change"]
    assert len(reverts) == 1


# ---------------------------------------------------------------- 리뷰 반영 (11/23)


def _repo_press(client: TestClient, path: str, body: dict) -> None:
    res = client.post(f"/api/planning/repo/prs/{path}", json=body)
    assert res.status_code != 200  # 관문 보류


def test_repo_revert_two_holds_approved_open_one_revert_pr(service: DevService, adapter) -> None:
    """같은 PR 되돌리기를 두 번 눌러 보류가 둘 → 둘 다 승인해도 되돌리기 PR은 하나."""
    opened = run(adapter.open_change("gen/pay", "요율", "", files={BANNER: "rate = 0\n"}))
    run(adapter.merge_change(opened.number, "squash"))
    gate = KeyGate("repo_revert_pr")
    service._gate = gate
    client = TestClient(app)
    _repo_press(client, f"{opened.number}/revert", {})
    _repo_press(client, f"{opened.number}/revert", {"title": "되돌림"})
    first, second = gate.reviews.values()
    assert len(gate.reviews) == 2  # 누를 때마다 새 키
    for rid in (first, second):
        gate.approve(rid)
    a, b = run(_decide(service, first)), run(_decide(service, second))
    assert a.status == "done", a.reason
    assert b.status == "skipped" and "이미 있다" in (b.reason or "")
    assert len([w for w in adapter.writes if w[0] == "revert_change"]) == 1


def test_repo_merge_pressed_twice_makes_one_hold(service: DevService, adapter) -> None:
    """같은 머리 커밋에서 두 번 누르면(두 번 클릭·다시 보내기·둘이 같이) 같은 키 → 보류 건 하나.
    전에는 누를 때마다 새 키라 보류 건이 둘 생겼다. 승인 뒤 머지는 한 번."""
    gate = KeyGate("merge_pr")
    service._gate = gate
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    client = TestClient(app)
    _repo_press(client, f"{n}/merge", {})
    _repo_press(client, f"{n}/merge", {})
    (rid,) = gate.reviews.values()
    assert len(service.holds.pending()) == 1
    gate.approve(rid)
    assert run(_decide(service, rid)).status == "done"
    assert run(_decide(service, rid)).status == "done"  # 같은 승인이 또 와도 다시 하지 않음
    assert len([w for w in adapter.writes if w[0] == "merge_change"]) == 1
    # 머지된 뒤 또 누르면 머지하지 않는다 (409)
    res = client.post(f"/api/planning/repo/prs/{n}/merge", json={})
    assert res.status_code == 409
    assert len([w for w in adapter.writes if w[0] == "merge_change"]) == 1


def test_repo_now_points_elsewhere_is_skipped(
    service: DevService, adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    gate = KeyGate("merge_pr")
    service._gate = gate
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    _repo_press(TestClient(app), f"{n}/merge", {})
    (rid,) = gate.reviews.values()
    real = vcs.find_connection

    def moved(cid: str):
        conn = real(cid)
        return dataclasses.replace(conn, repo="other/repo") if conn else None

    monkeypatch.setattr(vcs, "find_connection", moved)
    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "skipped" and "다른 레포" in (row.reason or "")
    assert ("merge_change", n) not in adapter.writes


def test_claim_save_failure_does_not_resume(
    service: DevService, adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)
    gate.approve(rid)
    monkeypatch.setattr(service.holds, "_save", lambda: False)  # 디스크 가득 등
    row = run(_decide(service, rid))
    assert row.status == "pending" and row.decided_by is None
    assert not next(p for p in service.state.prs if p.id == pr).merged
    monkeypatch.undo()  # 저장이 되돌아오면 다음 차례에 잇는다
    service._gate = gate
    assert run(_decide(service, rid)).status == "done"


def test_missing_review_is_skipped(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)

    async def missing(review_id: str) -> dict:
        return {"status": "missing"}

    assert run(poll_once(service, missing)) == 1
    row = service.holds.get(rid)
    assert row.status == "skipped" and row.reason == hold_resume.MISSING
    assert not next(p for p in service.state.prs if p.id == pr).merged


def test_poll_is_capped_and_slow_fetch_times_out(
    service: DevService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hold_resume, "FETCH_TIMEOUT", 0.05)
    d = ExecuteDecision(
        execution_id="x", grade="C", status="held", review_id="r", hold_reason="over_threshold"
    )
    with hold_resume.recipe("merge_pr", pr="PR #99"):
        for i in range(hold_resume.POLL_MAX + 5):
            service.holds.note(
                "merge_pr", f"k{i}", "c", d.model_copy(update={"review_id": f"r{i}"})
            )
    asked: list[str] = []

    async def slow(review_id: str) -> dict:
        asked.append(review_id)
        await asyncio.sleep(5)
        return {"status": "approved"}

    assert run(poll_once(service, slow)) == 0
    assert len(asked) == hold_resume.POLL_MAX
    assert len(service.holds.pending()) == hold_resume.POLL_MAX + 5


def test_closed_rows_are_pruned_open_rows_kept(service: DevService) -> None:
    from datetime import timedelta

    d = ExecuteDecision(
        execution_id="x", grade="C", status="held", review_id="r", hold_reason="over_threshold"
    )
    with hold_resume.recipe("merge_pr", pr="PR #99"):
        for i in range(hold_resume.KEEP_CLOSED + 10):
            service.holds.note(
                "merge_pr", f"k{i}", "c", d.model_copy(update={"review_id": f"r{i}"})
            )
            if i:
                service.holds.close(f"r{i}", "skipped", "x")
    assert service.holds.get("r0").status == "pending"  # 대기 건은 늘 둔다
    assert len(service.holds.rows) == hold_resume.KEEP_CLOSED + 1
    old = service.holds.get(f"r{hold_resume.KEEP_CLOSED + 9}")
    old.finished_at = old.finished_at - timedelta(days=hold_resume.KEEP_DAYS + 1)
    service.holds._save()
    assert service.holds.get(old.review_id) is None


def test_button_proceeds_after_failed_or_skipped_hold(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    service.holds.close("rev_1", "skipped", "건너뜀: 시험")
    gate.approve("rev_1")

    async def press() -> Any:
        out = await service.retry_dev_task("T-D1", approved=True)
        await service.wait_coding()
        return out

    assert run(press()).outcome == "queued"
    assert _task(service).coding.pr is not None


def test_coding_resume_wording_and_clean_context(service: DevService, adapter) -> None:
    gate = KeyGate("generate_pr")
    service._gate = gate
    coder.set_llm(FakeLLM())
    run(_go(service))
    gate.approve("rev_1")
    row = run(_decide(service, "rev_1"))
    assert "다시 시작함" in (row.reason or "") and "PR 열기 실행" in (row.reason or "")

    async def inside() -> Any:
        token = hold_resume._resuming.set("rev_1")
        try:
            return hold_resume.clean_context().run(hold_resume.current_resume)
        finally:
            hold_resume._resuming.reset(token)

    assert run(inside()) is None


# ---------------------------------------------------------------- 키가 다시 켜도 겹치지 않음


def test_repo_write_key_not_reused_after_restart(adapter) -> None:
    """옛 키 `…:1`이 승인·실행된 채 다시 켜면 같은 키로 승인 없이 재생되던 문제."""
    keys: list[str] = []
    for _ in range(2):  # 다시 켬 = 새 서비스 (gate_calls가 빈 채로 시작)
        svc = DevService()
        gate = KeyGate()
        svc._gate = gate

        async def op() -> None:
            return None

        run(svc.repo_write("repo_comment", "default#5", "코멘트", None, op))
        keys.append(gate.calls[-1][1])
    assert keys[0] != keys[1]


def test_promotion_key_is_per_proposal(service: DevService) -> None:
    from ax_workflow.models import AutoProposal

    gate = KeyGate()
    service._gate = gate
    keys = []
    for minute in (1, 2):
        service.state.auto_policy.level = 0
        service.state.auto_policy.proposal = AutoProposal(
            to=1, at=service.clock().replace(minute=minute), why="연속 정상"
        )
        run(service.approve_promotion())
        assert service.state.auto_policy.level == 1
        keys.append(next(k for a, k in reversed(gate.calls) if a == "auto_policy_update"))
    assert keys[0] != keys[1]  # 같은 L0->L1이라도 제안마다 새 키 (지난 승인을 재생하지 않음)


# ---------------------------------------------------------------- 자동 처리 범위


def test_policy_promote_resumes_once_and_stale_is_skipped(service: DevService) -> None:
    from ax_workflow.models import AutoProposal

    gate = KeyGate("auto_policy_update")
    service._gate = gate
    pol = service.state.auto_policy
    pol.level, pol.proposal = 0, AutoProposal(to=1, at=service.clock(), why="연속 정상")
    with pytest.raises(GateRefused):
        run(service.approve_promotion())
    (rid,) = gate.reviews.values()
    assert service.holds.get(rid).kind == "policy_promote"
    assert service.state.auto_policy.level == 0

    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "done" and service.state.auto_policy.level == 1
    assert service.state.auto_policy.proposal is None
    run(_decide(service, rid))
    assert gate.count("auto_policy_update") == 2

    # 제안이 그 사이 거절됨 → 건너뜀
    service.state.auto_policy.level = 0
    service.state.auto_policy.proposal = AutoProposal(
        to=1, at=service.clock().replace(minute=7), why="다시"
    )
    with pytest.raises(GateRefused):
        run(service.approve_promotion())
    rid2 = [r for r in gate.reviews.values() if r != rid][0]
    run(service.reject_promotion())
    gate.approve(rid2)
    row = run(_decide(service, rid2))
    assert row.status == "skipped" and service.state.auto_policy.level == 0


def test_policy_demote_resumes_once(service: DevService) -> None:
    gate = KeyGate("auto_policy_update")
    service._gate = gate
    service.state.auto_policy.level = 2

    async def held() -> None:
        with hold_resume.recipe("policy_demote", frm=2, to=1, text="장애 연결 → 축소"):
            await service._execute("auto_policy_update", "ctx_inc_9", "L2->L1", "축소", {"to": 1})

    with pytest.raises(GateRefused):
        run(held())
    (rid,) = gate.reviews.values()
    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "done" and service.state.auto_policy.level == 1
    assert gate.calls[-1] == ("auto_policy_update", next(iter(gate.reviews)))  # 같은 키
    assert any("보류 뒤 사람 승인" in x.text for x in service.state.auto_policy.log)
    run(_decide(service, rid))
    assert service.state.auto_policy.level == 1 and gate.count("auto_policy_update") == 2

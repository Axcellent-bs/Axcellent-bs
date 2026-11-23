# ruff: noqa: F811 - test_hold_resume의 adapter fixture를 가져와 인자로 받는다
"""개발 AI 승인 굳히기 (사람 머지 멱등 키 · 막힌 축소 동안 자동 처리 줄이기 · 공용 토큰 승인 거부).

1. 사람 머지 관문 키는 (PR, 머리 커밋)마다 하나다. 두 번 클릭·다시 보내기·둘이 같이 누르기는
   같은 키라 보류 건이 하나고 머지는 한 번이다. 새 커밋이 올라오면 새 키로 새로 판정받고, 옛
   머리에 대한 승인은 이어서 하지 않는다
2. 장애 연결로 줄여야 하는 자동 처리 범위가 관문에서 막혀도(보류·거절) 자동 처리는 줄인 단계로
   본다 (요구사항 FR-ATL-005·008 "줄이는 쪽은 승인 없이"). 사람이 그 보류를 거절하면 푼다
3. 세션 모드에서 승인 같은 사람 결정(머지·레포 승인·기각·배포 승인)은 로그인 세션만 받고 그
   계정을 남긴다. 공용 사람 토큰은 403 SESSION_REQUIRED. 세션 설정이 꺼진 모드는 예전 그대로

실제 safety·GitHub·LLM은 부르지 않는다 (키별 가짜 관문 `KeyGate`, 목 형상관리, 가짜 LLM).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_workflow.auth import acting
from ax_workflow.core import ActionError, GateRefused
from ax_workflow.models import AutoPolicy
from ax_workflow.rules import check_simple, effective_level
from dev_agent import coder
from dev_agent.main import app
from dev_agent.service import DevService

sys.path.insert(0, str(Path(__file__).parent))
from test_hold_resume import (  # noqa: E402
    BANNER,
    CHECKOUT,
    FakeLLM,
    KeyGate,
    _decide,
    _go,
    _held_pr,
    _task,
    _vcs_number,
    adapter,  # noqa: F401 - fixture
    run,
)
from test_repo_routing import _clean, _req12_to_prs, _two  # noqa: E402, F401 - _clean 연결 비우기

pytestmark = pytest.mark.role("dev")

ACTIONS = "/api/planning/actions"
SHARED = {"Authorization": "Bearer human-test-token"}


def _merges(adapter) -> int:
    return len([w for w in adapter.writes if w[0] == "merge_change"])


def _keys(gate: KeyGate, action: str = "merge_pr") -> list[str]:
    return [k for a, k in gate.calls if a == action]


# ---------------------------------------------------------------- 1. 사람 머지 멱등 키


def test_human_merge_pressed_again_reuses_held_key(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)
    # 두 번 클릭·다시 보내기: 같은 키 → 같은 보류 건 (새 보류를 만들지 않는다)
    for _ in range(2):
        with pytest.raises(GateRefused) as err:
            run(service.merge_pr(pr))
        assert err.value.decision.review_id == rid
    assert len(gate.reviews) == 1 and len(set(_keys(gate))) == 1
    assert len(service.holds.pending()) == 1

    # 승인 뒤 이어서 하기와 사람이 또 누르기가 겹쳐도 머지는 한 번 (같은 키 → 승인 재생)
    gate.approve(rid)
    assert run(_decide(service, rid)).status == "done"
    run(service.merge_pr(pr))  # 이미 머지됨 → 아무것도 안 함
    assert _merges(adapter) == 1 and len(set(_keys(gate))) == 1


def test_new_head_commit_gets_new_key_and_old_approval_is_not_resumed(
    service: DevService, adapter
) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, rid = _held_pr(service, gate)
    n = _vcs_number(service, pr)
    # 보류 뒤 새 커밋이 올라옴 → 다시 누르면 새 키, 새 보류 건
    run(adapter.update_change(n, {BANNER: "export const Y = 2\n"}, "고침"))
    with pytest.raises(GateRefused) as err:
        run(service.merge_pr(pr))
    assert err.value.decision.review_id != rid
    assert len(set(_keys(gate))) == 2

    # 옛 머리에 대한 승인은 새 코드를 머지하지 않는다 (건너뜀 + 이유)
    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "skipped" and "새 커밋" in (row.reason or "")
    assert _merges(adapter) == 0
    assert not next(p for p in service.state.prs if p.id == pr).merged


def test_head_read_failure_does_not_merge(service: DevService, adapter, monkeypatch) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, _ = _held_pr(service, gate)
    calls = len(gate.calls)

    async def broken(number: int):
        from dev_agent.vcs import VcsError

        raise VcsError("unavailable", "응답 없음")

    monkeypatch.setattr(adapter, "get_change", broken)
    with pytest.raises(ActionError) as err:  # 이유와 함께 실패로 답한다
        run(service.merge_pr(pr))
    assert err.value.code == "MERGE_FAILED" and "머리 커밋" in err.value.message
    assert len(gate.calls) == calls and _merges(adapter) == 0  # 관문에도 묻지 않는다


class PushDuringGate(KeyGate):
    """관문이 merge_pr을 실행 판정하는 사이에 새 커밋이 올라온다 (승인과 병합 사이 경합)."""

    def __init__(self, adapter, number_of) -> None:
        super().__init__()
        self.adapter, self.number_of = adapter, number_of

    async def __call__(self, action, params, meta, evidence):
        if action == "merge_pr":
            n = self.number_of()
            await self.adapter.update_change(n, {BANNER: "export const Z = 3\n"}, "끼어든 커밋")
        return await super().__call__(action, params, meta, evidence)


def test_merge_passes_approved_sha_internal(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, _ = _held_pr(service, gate)
    service._gate = PushDuringGate(adapter, lambda: _vcs_number(service, pr))
    with pytest.raises(ActionError) as err:
        run(service.merge_pr(pr))
    assert (
        err.value.code == "MERGE_FAILED" and "새 커밋이 올라와 머지하지 않음" in err.value.message
    )
    assert _merges(adapter) == 0 and not next(p for p in service.state.prs if p.id == pr).merged
    assert "충돌" not in (_task(service).coding.error or "")  # 다시 쓰기(충돌)로 보지 않는다


def test_merge_passes_approved_sha_repo(service: DevService, adapter) -> None:
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    service._gate = PushDuringGate(adapter, lambda: n)
    res = TestClient(app).post(f"/api/planning/repo/prs/{n}/merge", json={})
    assert res.status_code == 409 and "새 커밋이 올라와 머지하지 않음" in res.text
    assert _merges(adapter) == 0


def test_repo_resume_skips_when_head_moved(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    client = TestClient(app)
    assert client.post(f"/api/planning/repo/prs/{n}/merge", json={}).status_code != 200
    (rid,) = gate.reviews.values()
    assert service.holds.get(rid).args["op"]["head"]
    run(adapter.update_change(n, {BANNER: "export const Y = 2\n"}, "승인 전 새 커밋"))
    gate.approve(rid)
    row = run(_decide(service, rid))
    assert row.status == "skipped" and "새 커밋" in (row.reason or "")
    assert _merges(adapter) == 0


def test_repo_press_after_rejected_hold_asks_again(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    client = TestClient(app)

    def press() -> None:
        assert client.post(f"/api/planning/repo/prs/{n}/merge", json={}).status_code != 200

    press()
    (first,) = gate.reviews.values()
    run(_decide(service, first, "rejected"))
    press()
    press()  # 보류가 열려 있는 동안은 같은 키 (보류 건 하나)
    assert len(gate.reviews) == 2
    second = [r for r in gate.reviews.values() if r != first][0]
    assert service.holds.get(second).key.endswith(":r1")
    gate.approve(second)
    assert run(_decide(service, second)).status == "done" and _merges(adapter) == 1


def test_internal_press_after_rejected_hold_asks_again(service: DevService, adapter) -> None:
    gate = KeyGate("merge_pr")
    service._gate = gate
    pr, first = _held_pr(service, gate)
    run(_decide(service, first, "rejected"))
    for _ in range(2):
        with pytest.raises(GateRefused):
            run(service.merge_pr(pr))
    assert len(gate.reviews) == 2
    second = [r for r in gate.reviews.values() if r != first][0]
    gate.approve(second)
    assert run(_decide(service, second)).status == "done"
    assert _merges(adapter) == 1


def test_clean_context_drops_actor() -> None:
    from ax_workflow.auth import current_actor
    from dev_agent import hold_resume

    with acting("가영"):
        assert hold_resume.clean_context().run(current_actor) is None
        assert current_actor() == "가영"


# ---------------------------------------------------------------- 2. 막힌 축소 동안 자동 처리


def _link_auto_pr(service: DevService, ctx: str = "ctx_inc_9") -> None:
    pr = next(p for p in service.state.prs if p.id == "PR #28")
    pr.auto = True
    run(
        service.link_incident(
            context_id=ctx, requester="orchestrator", incident_context_id=ctx,
            pr_numbers=[pr.number], follow_up_title=None, idempotency_key=f"k-{ctx}",
        )
    )  # fmt: skip


def test_effective_level_follows_pending_shrink() -> None:
    pol = AutoPolicy(level=1, paths="shop-web/")
    assert effective_level(pol) == 1 and check_simple("배너 문구 오타", pol).ok
    pol.shrink_pending = 0
    assert effective_level(pol) == 0
    got = check_simple("배너 문구 오타", pol)
    assert not got.ok and "L0" in got.reasons[0]
    pol.shrink_pending = 3  # 넓히는 쪽으로는 보지 않는다
    assert effective_level(pol) == 1


def test_held_demotion_shrinks_effective_level_until_resolved(service: DevService) -> None:
    gate = KeyGate("auto_policy_update")
    service._gate = gate
    service.state.auto_policy.level = 1
    _link_auto_pr(service)
    pol = service.state.auto_policy
    assert pol.level == 1 and pol.shrink_pending == 0  # 기록 단계는 관문 뒤에만 바뀐다
    assert effective_level(pol) == 0
    line = next(a for a in service.state.activity if a.point == "auto_policy.update_held")
    assert "L0 기준" in line.text
    assert pol.proposal is None and pol.streak == 0

    # 승인 → 줄이기 실행, 표시 비움
    (rid,) = gate.reviews.values()
    gate.approve(rid)
    assert run(_decide(service, rid)).status == "done"
    pol = service.state.auto_policy
    assert pol.level == 0 and pol.shrink_pending is None


def test_rejected_demotion_hold_clears_pending(service: DevService) -> None:
    gate = KeyGate("auto_policy_update")
    service._gate = gate
    service.state.auto_policy.level = 2
    _link_auto_pr(service)
    assert service.state.auto_policy.shrink_pending == 1
    (rid,) = gate.reviews.values()
    assert run(_decide(service, rid, "rejected")).status == "rejected"
    pol = service.state.auto_policy
    assert pol.level == 2 and pol.shrink_pending is None
    assert any("보류 거절" in x.text for x in pol.log)


def test_gate_rejected_demotion_keeps_level_and_records(service: DevService) -> None:
    """보류 기록 없이 관문이 바로 거절·못 부름이면 끝낼 보류 건이 없다 → 표시를 두지 않는다
    (영영 안 풀리는 표시를 만들지 않는다). 거절은 활동에 남긴다."""
    from ax_contracts.schemas import ExecuteDecision

    async def down(action, params, meta, evidence):
        if action == "auto_policy_update":  # 관문을 못 부름 → core가 rejected로 본다
            raise RuntimeError("safety down")
        return ExecuteDecision(execution_id="ok", grade="A", status="executed")

    service._gate = down
    service.state.auto_policy.level = 1
    _link_auto_pr(service)
    pol = service.state.auto_policy
    assert pol.level == 1 and pol.shrink_pending is None and effective_level(pol) == 1
    line = next(a for a in service.state.activity if a.point == "auto_policy.update_held")
    assert "rejected" in line.text and "범위는 그대로 L1" in line.text
    # 다음 장애 연결에서 줄이기가 실행되면 그대로 줄어든다
    service._gate = KeyGate()
    _link_auto_pr(service, "ctx_inc_10")
    assert service.state.auto_policy.level == 0


def test_two_held_demotions_keep_pending_until_both_closed(service: DevService) -> None:
    gate = KeyGate("auto_policy_update")
    service._gate = gate
    service.state.auto_policy.level = 2
    _link_auto_pr(service, "ctx_inc_a")
    _link_auto_pr(service, "ctx_inc_b")
    first, second = gate.reviews.values()
    assert service.state.auto_policy.shrink_pending == 1
    run(_decide(service, first, "rejected"))
    pol = service.state.auto_policy
    assert pol.shrink_pending == 1  # 다른 축소 보류가 아직 열려 있다
    assert any("다른 축소 보류" in x.text for x in pol.log)
    run(_decide(service, second, "expired"))
    pol = service.state.auto_policy
    assert pol.level == 2 and pol.shrink_pending is None


def test_auto_policy_api_shows_effective_level(service: DevService) -> None:
    gate = KeyGate("auto_policy_update")
    service._gate = gate
    service.state.auto_policy.level = 2
    _link_auto_pr(service)
    data = TestClient(app).get("/api/planning/auto-policy").json()["data"]
    assert (data["level"], data["effective_level"], data["shrink_pending"]) == (2, 1, 1)
    assert data["limit"]["level"] == 1


# ---------------------------------------------------------------- 3. 공용 토큰 승인 거부


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")

    def token(account_id: str, name: str) -> dict[str, str]:
        tok, _ = human_auth.issue_session(account_id=account_id, name=name, role="member")
        return {"Authorization": f"Bearer {tok}"}

    return token


def test_session_mode_refuses_shared_token_for_merge(service: DevService, adapter, sessions):
    gate = KeyGate()
    service._gate = gate
    coder.set_llm(FakeLLM(CHECKOUT))
    run(_go(service))
    pr = _task(service).coding.pr
    c = TestClient(app)
    res = c.post(ACTIONS, json={"type": "mergePr", "pr": pr}, headers=SHARED)
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    assert c.post(ACTIONS, json={"type": "mergePr", "pr": pr}).status_code in (401, 403)
    assert _merges(adapter) == 0 and not _keys(gate)  # merge_pr 관문에도 묻지 않는다

    res = c.post(ACTIONS, json={"type": "mergePr", "pr": pr}, headers=sessions("acc-1", "가영"))
    assert res.status_code == 200, res.text
    assert _merges(adapter) == 1
    line = next(a for a in service.state.activity if a.point == "PR 머지")
    assert line.text.endswith("· 가영")


def test_session_mode_records_repo_approver(service: DevService, sessions) -> None:
    _two()
    _req12_to_prs(service)
    c = TestClient(app)
    res = c.post(ACTIONS, json={"type": "approveRepo", "pr": "PR #31"}, headers=SHARED)
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    res = c.post(
        ACTIONS, json={"type": "approveRepo", "pr": "PR #31"}, headers=sessions("acc-2", "나래")
    )
    assert res.status_code == 200, res.text
    pr = next(p for p in service.state.prs if p.id == "PR #31")
    assert pr.target is not None and pr.target.decided_by == "나래"
    res = c.post(
        ACTIONS, json={"type": "rejectRepo", "pr": "PR #32"}, headers=sessions("acc-3", "다온")
    )
    assert res.status_code == 200, res.text
    pr = next(p for p in service.state.prs if p.id == "PR #32")
    assert pr.target is not None and pr.target.decided_by == "다온"


def test_session_mode_deploy_and_repo_merge_refuse_shared_token(sessions) -> None:
    c = TestClient(app)
    res = c.post("/api/planning/deployments/DEP-999/approve", json={"approved_by": "x"},
                 headers=SHARED)  # fmt: skip
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    res = c.post("/api/planning/repo/prs/1/merge", json={}, headers=SHARED)
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    # 세션이면 승인 관문을 지나 다음 단계(그런 배포 없음 404)로 간다. 본문 이름이 없어도 된다
    res = c.post("/api/planning/deployments/DEP-999/approve", json={},
                 headers=sessions("acc-4", "라온"))  # fmt: skip
    assert res.status_code == 404


def test_non_session_mode_keeps_shared_token_and_team_lead(service: DevService) -> None:
    assert not human_auth.sessions_enabled()
    _two()
    _req12_to_prs(service)
    res = TestClient(app).post(ACTIONS, json={"type": "approveRepo", "pr": "PR #31"})
    assert res.status_code == 200
    pr = next(p for p in service.state.prs if p.id == "PR #31")
    assert pr.target is not None and pr.target.decided_by == "노예진"  # 예전 그대로


def test_acting_is_scoped(service: DevService) -> None:
    from ax_workflow.auth import current_actor

    assert current_actor() is None
    with acting("가영"):
        assert current_actor() == "가영"
        with acting(None):
            assert current_actor() == "가영"
    assert current_actor() is None

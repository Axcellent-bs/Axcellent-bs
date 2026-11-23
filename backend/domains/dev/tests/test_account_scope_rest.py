"""개발 AI 계정 범위의 나머지 길 (#1556·#1587 후속).

1. `/actions`의 작업·PR 액션(PR 만들기·재검수·머지·레포 승인·기각): 세션 모드면 작업 주인·관리자만
2. 레포 작업 공간 쓰기(`/repo/*`): 세션 모드면 로그인 세션만, 누른 계정을 기록. AI 작업 PR은 주인만.
   되돌리기는 (연결, PR)마다 같은 관문 키 (test_hold_resume에도)
3. 내 Claude 작업 대기열: 세션 모드면 계정을 아는 토큰만, 그 계정 작업만 (test_work_queue에도)
4. 시연용 경로(지어낸 PR 기록)는 목 모드에서만
5. 자동 범위 승급 승인·보류: 세션 모드면 로그인한 회사 관리자, 그 사람을 기록

세션 설정이 꺼진 로컬·시험 모드는 예전과 같다. 실제 GitHub·LLM·safety는 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_workflow.base import TEAM_LEAD
from ax_workflow.models import PR, AutoProposal, ChatThread, PrGate
from dev_agent import coder, dev_chat, repo_routing, vcs, work_queue
from dev_agent.main import app
from dev_agent.service import DevService
from dev_agent.vcs import MockVcsAdapter
from planning_agent.domain.service import ActionError

sys.path.insert(0, str(Path(__file__).parent))

pytestmark = pytest.mark.role("dev")

ACTIONS = "/api/planning/actions"
REPO = "/api/planning/repo"
SHARED = {"Authorization": "Bearer human:tester"}  # 공용 사람 토큰 (목)
BANNER = "shop-web/src/components/Banner.tsx"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files({BANNER: "export function Banner() {\n  return null\n}\n"})
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    coder.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch):
    def token(account_id: str, name: str, role: str = "member") -> dict[str, str]:
        tok, _ = human_auth.issue_session(account_id=account_id, name=name, role=role)
        return {"Authorization": f"Bearer {tok}"}

    def on() -> None:
        monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)

    on()
    return token


def _owned_task(svc: DevService, account: str = "acc-a") -> str:
    """계정 `account`가 개발 화면 대화로 낸 요청의 작업 (내 Claude 작업: 서버 AI를 안 띄운다)."""
    out = run(
        svc.create_dev_task(title="배너 문구", plan="배너 문구를 바꾼다.", mode="subscription")
    ).output
    t = datetime(2026, 11, 23, tzinfo=UTC)
    svc.state.chats.append(
        ChatThread(
            id=f"chat-{account}",
            title="배너",
            origin="screen",
            account=account,
            who="가",
            request_id=out["request_id"],
            created_at=t,
            updated_at=t,
        )
    )
    assert dev_chat.task_request(svc.state, out["task_id"]) == out["request_id"]
    return out["task_id"]


def _pr_for(svc: DevService, task_id: str, number: int = 77, vcs_number: int | None = None) -> PR:
    pr = PR(
        id=f"PR #{number}",
        number=number,
        task=task_id,
        title=f"{task_id}: 배너",
        branch=f"gen/{task_id.lower()}",
        reviews=["불일치"],
        gate=PrGate(result="통과", lines=3, files=1, paths="shop-web"),
        merged=False,
        vcs_number=vcs_number,
    )
    svc.state.prs.append(pr)
    return pr


def _code(res) -> tuple[int, str | None]:
    body = res.json()
    return res.status_code, (body.get("error") or {}).get("code")


# ---------------------------------------------------------------- 1. /actions 작업·PR 액션


@pytest.mark.parametrize(
    "action",
    [
        {"type": "mergePr", "pr": "PR #77"},
        {"type": "reviewPr", "pr": "PR #77"},
        {"type": "rejectRepo", "pr": "PR #77"},
        {"type": "approveRepo", "pr": "PR #77"},
        {"type": "generatePrs", "spec": "__SPEC__"},
    ],
)
def test_task_actions_are_owner_only_in_session_mode(
    service: DevService, adapter, sessions, action: dict
) -> None:
    task_id = _owned_task(service)
    _pr_for(service, task_id)
    spec = next(t.spec for t in service.state.tasks if t.id == task_id)
    body = {k: (spec if v == "__SPEC__" else v) for k, v in action.items()}
    a, b = sessions("acc-a", "가"), sessions("acc-b", "나")
    boss = sessions("acc-boss", "대표", role="admin")
    with TestClient(app) as c:
        assert _code(c.post(ACTIONS, json=body, headers=b)) == (403, "VIEW_ONLY")
        assert c.post(ACTIONS, json=body, headers=b).json()["error"]["message"] == (
            dev_chat.TASK_OWNER_ONLY
        )
        # 공용 사람 토큰·토큰 없음은 누구인지 몰라 받지 않는다
        assert c.post(ACTIONS, json=body, headers=SHARED).status_code == 403
        assert c.post(ACTIONS, json=body, headers={"Authorization": ""}).status_code == 401
        for who in (a, boss):  # 주인·관리자는 막지 않는다 (작업 상태에 따른 다른 답은 된다)
            assert _code(c.post(ACTIONS, json=body, headers=who)) != (403, "VIEW_ONLY")


@pytest.mark.parametrize("pr", [77.0, "77"])
def test_owner_guard_reads_pr_like_dispatch(service: DevService, adapter, sessions, pr) -> None:
    """`77.0`·`"77"`도 dispatch처럼 PR #77로 읽어 주인을 가린다 (빈 목록으로 통과하지 않는다)."""
    task_id = _owned_task(service)
    _pr_for(service, task_id)
    b = sessions("acc-b", "나")
    with TestClient(app) as c:
        res = c.post(ACTIONS, json={"type": "mergePr", "pr": pr}, headers=b)
    assert _code(res) == (403, "VIEW_ONLY"), res.text


def test_owner_guard_fails_closed_on_unknown_or_bad_targets(
    service: DevService, adapter, sessions
) -> None:
    _owned_task(service)
    b = sessions("acc-b", "나")
    with TestClient(app) as c:
        for body in (
            {"type": "mergePr", "pr": "PR #999"},
            {"type": "approveRepo", "pr": 999},
            {"type": "generatePrs", "spec": "SPEC-없음"},
        ):
            assert _code(c.post(ACTIONS, json=body, headers=b)) == (404, "NOT_FOUND"), body
        bad = c.post(ACTIONS, json={"type": "mergePr", "pr": [77]}, headers=b)
        assert _code(bad) == (400, "INVALID_ACTION")


def test_task_actions_unchanged_outside_session_mode(service: DevService, adapter) -> None:
    task_id = _owned_task(service)
    _pr_for(service, task_id)
    assert not human_auth.sessions_enabled()
    with TestClient(app) as c:
        res = c.post(ACTIONS, json={"type": "reviewPr", "pr": "PR #77"}, headers=SHARED)
    # 주인 확인 없이 액션까지 간다 (코딩 작업 PR이라 재검수 자체는 액션이 거절)
    assert _code(res) == (400, "NOT_REVIEWABLE"), res.text


def test_task_without_chat_owner_stays_open_in_session_mode(
    service: DevService, adapter, sessions
) -> None:
    """대화가 낸 요청이 아닌 작업은 작업 API처럼 로그인한 누구나 (`dev_chat.may_operate`)."""
    out = run(service.create_dev_task(title="배너", plan="배너 문구", mode="subscription"))
    _pr_for(service, out.output["task_id"])
    with TestClient(app) as c:
        x = sessions("x", "x")
        res = c.post(ACTIONS, json={"type": "reviewPr", "pr": "PR #77"}, headers=x)
    assert _code(res) == (400, "NOT_REVIEWABLE"), res.text


# ---------------------------------------------------------------- 2. 레포 작업 공간 쓰기


def test_repo_writes_need_a_session_and_record_the_actor(
    service: DevService, adapter, sessions
) -> None:
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    a = sessions("acc-a", "가나다")
    with TestClient(app) as c:
        for path, body in (
            (f"/prs/{n}/comment", {"body": "확인"}),
            (f"/prs/{n}/close", {}),
            (f"/prs/{n}/reopen", {}),
            (f"/prs/{n}/revert", {}),
            ("/branches/delete", {"name": "fix/a"}),
        ):
            res = c.post(f"{REPO}{path}", json=body, headers=SHARED)
            assert _code(res) == (403, "SESSION_REQUIRED"), path
        res = c.post(f"{REPO}/prs/{n}/comment", json={"body": "확인"}, headers=a)
        assert res.status_code == 200, res.text
    acts = [x.text for x in service.state.activity if x.point == "repo_comment"]
    assert acts and acts[-1].endswith("· 가나다")


def test_repo_reads_stay_open_in_session_mode(service: DevService, adapter, sessions) -> None:
    """연결 레포는 회사 공용이라 읽기는 계정으로 나누지 않는다 (계정별 값이 없다)."""
    adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    with TestClient(app) as c:
        res = c.get(f"{REPO}/prs")
    assert res.status_code == 200
    assert "acc-" not in res.text


def test_repo_writes_on_ai_task_pr_are_owner_only(service: DevService, adapter, sessions) -> None:
    task_id = _owned_task(service)
    n = adapter.seed(title=f"{task_id}: 배너", branch="gen/x", files={BANNER: (3, 1)})
    _pr_for(service, task_id, vcs_number=n)
    b = sessions("acc-b", "나")
    with TestClient(app) as c:
        assert _code(c.post(f"{REPO}/prs/{n}/close", json={}, headers=b)) == (403, "VIEW_ONLY")
        assert _code(c.post(f"{REPO}/prs/{n}/merge", json={}, headers=b)) == (403, "VIEW_ONLY")
        own = c.post(f"{REPO}/prs/{n}/close", json={}, headers=sessions("acc-a", "가"))
        assert _code(own) != (403, "VIEW_ONLY")


def test_repo_writes_unchanged_outside_session_mode(service: DevService, adapter) -> None:
    n = adapter.seed(title="사람 PR", branch="fix/a", files={BANNER: (3, 1)})
    with TestClient(app) as c:
        res = c.post(f"{REPO}/prs/{n}/comment", json={"body": "확인"}, headers=SHARED)
    assert res.status_code == 200, res.text
    acts = [x.text for x in service.state.activity if x.point == "repo_comment"]
    assert acts[-1] == f"사람이 레포 PR #{n}에 코멘트"


# ---------------------------------------------------------------- 3. 내 Claude 대기열


def test_work_queue_scopes_to_the_session_account(service: DevService, adapter, sessions) -> None:
    mine = _owned_task(service, "acc-a")
    theirs = _owned_task(service, "acc-b")
    a, b = sessions("acc-a", "가"), sessions("acc-b", "나")
    with TestClient(app) as c:
        rows = c.get("/api/planning/work/tasks", headers=a).json()["data"]
        assert [r["task_id"] for r in rows] == [mine]
        res = c.post(f"/api/planning/work/tasks/{theirs}/claim", headers=a)
        assert _code(res) == (403, "NOT_YOURS")
        got = c.post(f"/api/planning/work/tasks/{mine}/claim", headers=a).json()["data"]
        assert got["claimed_by"] == "acct:acc-a"
        # 남이 쥔 작업은 진행·반납 못 한다
        res = c.post(f"/api/planning/work/tasks/{mine}/release", json={}, headers=b)
        assert _code(res) == (403, "NOT_YOURS")


def test_work_queue_refuses_name_only_tokens_in_session_mode(
    service: DevService, adapter, sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이름만 있는 작업 토큰·공용 사람 토큰은 누구 작업인지 몰라 세션 모드에서 받지 않는다."""
    monkeypatch.setenv("DEV_WORK_TOKENS", "kim:work-test-token")
    _owned_task(service)
    with TestClient(app) as c:
        for h in (SHARED, {"Authorization": "Bearer work-test-token"}):
            assert _code(c.get("/api/planning/work/tasks", headers=h)) == (403, "SESSION_REQUIRED")
        assert c.get("/api/planning/work/tasks", headers={"Authorization": ""}).status_code == 401


def test_handed_task_belongs_to_the_one_who_handed_it(
    service: DevService, adapter, sessions
) -> None:
    out = run(service.create_dev_task(title="배너", plan="배너 문구", mode="subscription"))
    with TestClient(app) as c:
        made = c.post(
            "/api/planning/dev-tasks",
            json={"title": "배너 2", "plan": "배너 문구 2", "mode": "subscription"},
            headers=sessions("acc-a", "가"),
        ).json()["data"]
    task = next(t for t in service.state.tasks if t.id == made["task_id"])
    assert task.coding.assignee == "acct:acc-a"
    assert work_queue.belongs(service, service.state, task, "acc-a")
    assert not work_queue.belongs(service, service.state, task, "acc-b")
    # 주인 없는 작업(대화가 낸 요청이 아님)은 작업 API처럼 누구나
    free = next(t for t in service.state.tasks if t.id == out.output["task_id"])
    assert work_queue.belongs(service, service.state, free, "acc-b")
    with pytest.raises(ActionError) as err:
        run(work_queue.claim(service, task.id, "acct:acc-b", "acc-b"))
    assert err.value.code == "NOT_YOURS"


# ---------------------------------------------------------------- 4. 시연용 경로는 목 모드에서만


def test_demo_paths_are_mock_only(service: DevService, adapter, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "0")
    monkeypatch.setenv("PLANNING_MOCK", "0")
    with pytest.raises(ActionError) as err:
        run(service.dispatch({"type": "generatePrs", "spec": "SPEC-1"}))
    assert err.value.code == "SIMULATOR_DISABLED"
    with pytest.raises(ActionError) as err:
        run(service.dispatch({"type": "reviewPr", "pr": "PR #1"}))
    assert err.value.code == "SIMULATOR_DISABLED"
    # 실제 admin 에이전트 토큰(명부)으로 불러도 시연용 머지 기록은 만들지 않는다
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=real-admin-token-for-test")
    before = len(service.state.prs)
    res = TestClient(app).post(
        "/api/planning/demo/incident-merge",
        json={"deployed_version": "shop-api v2.4.2"},
        headers={"Authorization": "Bearer real-admin-token-for-test"},
    )
    assert _code(res) == (403, "SIMULATOR_DISABLED"), res.text
    assert len(service.state.prs) == before


# ---------------------------------------------------------------- 5. 자동 범위 승급 승인·보류


def test_promotion_needs_a_session_admin_and_records_them(
    service: DevService, adapter, sessions
) -> None:
    pol = service.state.auto_policy
    pol.level, pol.proposal = 0, AutoProposal(to=1, at=service.clock(), why="연속 정상")
    approve = {"type": "approvePromotion"}
    with TestClient(app) as c:
        admin_agent = {"Authorization": "Bearer mock:admin"}
        refused = c.post(ACTIONS, json=approve, headers=admin_agent)
        assert _code(refused) == (403, "SESSION_REQUIRED")
        member = sessions("acc-a", "가")
        assert c.post(ACTIONS, json=approve, headers=member).status_code == 403
        boss = sessions("acc-boss", "대표", role="admin")
        res = c.post(ACTIONS, json=approve, headers=boss)
    assert res.status_code == 200, res.text
    assert service.state.auto_policy.level == 1
    texts = [x.text for x in service.state.activity if x.point == "자동 범위 승급 승인"]
    assert texts and "대표" in texts[-1]


def test_promotion_reject_records_the_session_admin(
    service: DevService, adapter, sessions
) -> None:
    pol = service.state.auto_policy
    pol.proposal = AutoProposal(to=1, at=service.clock(), why="연속 정상")
    boss = sessions("acc-boss", "대표", role="admin")
    with TestClient(app) as c:
        res = c.post(ACTIONS, json={"type": "rejectPromotion"}, headers=boss)
    assert res.status_code == 200, res.text
    assert service.state.auto_policy.log[-1].text.endswith("· 대표")


def test_promotion_keeps_admin_agent_token_outside_session_mode(service: DevService) -> None:
    pol = service.state.auto_policy
    pol.level, pol.proposal = 0, AutoProposal(to=1, at=service.clock(), why="연속 정상")
    with TestClient(app) as c:
        admin_agent = {"Authorization": "Bearer mock:admin"}
        res = c.post(ACTIONS, json={"type": "approvePromotion"}, headers=admin_agent)
        human = c.post(ACTIONS, json={"type": "rejectPromotion"}, headers=SHARED)
    assert res.status_code == 200, res.text
    assert human.status_code == 403  # 사람 토큰은 예전처럼 운영 결정을 못 한다
    texts = [x.text for x in service.state.activity if x.point == "자동 범위 승급 승인"]
    assert texts and TEAM_LEAD in texts[-1]  # 예전처럼 팀장(목)


# ---------------------------------------------------------------- 리뷰 후속


def test_revert_after_closed_revert_pr_needs_a_new_review(service: DevService, adapter) -> None:
    """되돌리기 PR을 열었다가 병합 없이 닫은 뒤 다시 누르면 새 키 → 지난 승인을 재생하지 않는다."""
    from test_hold_resume import KeyGate, _decide

    opened = run(adapter.open_change("gen/pay", "요율", "", files={BANNER: "rate = 0\n"}))
    run(adapter.merge_change(opened.number, "squash"))
    gate = KeyGate("repo_revert_pr")
    service._gate = gate
    client = TestClient(app)
    url = f"{REPO}/prs/{opened.number}/revert"
    assert client.post(url, json={}).status_code != 200  # 보류
    (rid,) = gate.reviews.values()
    gate.approve(rid)
    assert run(_decide(service, rid)).status == "done"
    made = [c for c in run(adapter.list_changes(state="all")) if c.number != opened.number]
    run(adapter.close_change(made[0].number))
    again = client.post(url, json={})
    assert again.status_code != 200, again.text  # 새 키라 새로 보류 (새 판정)
    assert len(gate.reviews) == 2
    assert len([w for w in adapter.writes if w[0] == "revert_change"]) == 1


def test_queue_tasks_from_chats_without_account_stay_open(
    service: DevService, adapter, sessions
) -> None:
    """대시보드 대화방·계정 전 예전 대화가 낸 작업은 계정으로 가를 수 없어 멈추지 않는다."""
    task_id = _owned_task(service, "acc-a")
    chat = service.state.chats[-1]
    chat.origin, chat.account = "dashboard", None
    task = next(t for t in service.state.tasks if t.id == task_id)
    assert work_queue.belongs(service, service.state, task, "acc-b")


def test_session_admin_claims_anyone_s_queue_task(service: DevService, adapter, sessions) -> None:
    task_id = _owned_task(service, "acc-a")
    boss = sessions("acc-boss", "대표", role="admin")
    with TestClient(app) as c:
        rows = c.get("/api/planning/work/tasks", headers=boss).json()["data"]
        assert task_id in [r["task_id"] for r in rows]
        got = c.post(f"/api/planning/work/tasks/{task_id}/claim", headers=boss)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["claimed_by"] == "acct:acc-boss"


def test_held_promotion_resume_records_the_approver(
    service: DevService, adapter, sessions
) -> None:
    """승급 승인이 관문에서 보류됐다가 이어서 해도 누른 관리자를 남긴다 (팀장 목 이름이 아니다)."""
    from test_hold_resume import KeyGate, _decide

    gate = KeyGate("auto_policy_update")
    service._gate = gate
    pol = service.state.auto_policy
    pol.level, pol.proposal = 0, AutoProposal(to=1, at=service.clock(), why="연속 정상")
    boss = sessions("acc-boss", "대표", role="admin")
    with TestClient(app) as c:
        res = c.post(ACTIONS, json={"type": "approvePromotion"}, headers=boss)
    assert _code(res)[1] == "GATE_REJECTED"
    (rid,) = gate.reviews.values()
    gate.approve(rid)
    assert run(_decide(service, rid)).status == "done"
    assert service.state.auto_policy.level == 1
    texts = [x.text for x in service.state.activity if x.point == "자동 범위 승급 승인"]
    assert texts and "대표" in texts[-1] and TEAM_LEAD not in texts[-1]


def _agent_task(service: DevService, account: str) -> str:
    """계정 `account` 대화가 낸, 멈춘 서버 AI 작업 (내 Claude로 넘길 수 있다)."""
    task_id = _owned_task(service, account)
    task = next(t for t in service.state.tasks if t.id == task_id)
    task.coding.mode, task.coding.status = "agent", "failed"
    return task_id


def test_hand_to_subscription_assigns_the_presser(service: DevService, adapter, sessions) -> None:
    task_id = _agent_task(service, "acc-a")
    with TestClient(app) as c:
        url = f"/api/planning/dev-tasks/{task_id}/to-subscription"
        res = c.post(url, headers=sessions("acc-a", "가"))
    assert res.status_code == 200, res.text
    task = next(t for t in service.state.tasks if t.id == task_id)
    assert task.coding.mode == "subscription" and task.coding.assignee == "acct:acc-a"


def test_admin_hand_to_subscription_keeps_the_owner(service: DevService, adapter, sessions) -> None:
    task_id = _agent_task(service, "acc-a")
    boss = sessions("acc-boss", "대표", role="admin")
    with TestClient(app) as c:
        res = c.post(f"/api/planning/dev-tasks/{task_id}/to-subscription", headers=boss)
    assert res.status_code == 200, res.text
    task = next(t for t in service.state.tasks if t.id == task_id)
    assert task.coding.assignee == "acct:acc-a"


def test_hand_to_subscription_has_no_assignee_outside_session_mode(
    service: DevService, adapter
) -> None:
    task_id = _agent_task(service, "acc-a")
    with TestClient(app) as c:
        res = c.post(
            f"/api/planning/dev-tasks/{task_id}/to-subscription", params={"account": "acc-a"}
        )
    assert res.status_code == 200, res.text
    assert next(t for t in service.state.tasks if t.id == task_id).coding.assignee is None


def test_repo_reopen_on_ai_task_pr_is_owner_only(service: DevService, adapter, sessions) -> None:
    task_id = _owned_task(service)
    n = adapter.seed(title=f"{task_id}: 배너", branch="gen/x", files={BANNER: (3, 1)})
    _pr_for(service, task_id, vcs_number=n)
    with TestClient(app) as c:
        res = c.post(f"{REPO}/prs/{n}/reopen", json={}, headers=sessions("acc-b", "나"))
    assert _code(res) == (403, "VIEW_ONLY")

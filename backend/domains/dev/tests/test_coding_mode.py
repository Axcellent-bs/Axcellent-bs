"""요청 작업 코딩 방식(회사 설정)·서버 AI 체험 한도.

넘겨받은 요청 작업은 실제 레포에 올릴 때 코딩 작업이 된다. 누가 코딩할지는 회사 설정
(auto·agent·subscription)으로 정하고, auto면 본인 Claude(MCP)를 연결한 개발자가 있을 때 내 Claude,
없으면 서버 AI다. 서버 AI는 체험 한도(토큰) 안에서만 돈다.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_workflow.core import ActionError
from ax_workflow.models import CodingRun
from ax_workflow_testing import plan
from dev_agent import coder, repo_routing, vcs
from dev_agent.main import app
from dev_agent.vcs import MockVcsAdapter

ANSWER = "품절 상품도 남겨 두고 품절 표시해 주세요"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


def _spec7_tasks(service):
    return [t for t in service.state.tasks if t.spec == "SPEC-7"]


def test_choice_follows_setting_agents_and_trial(service, monkeypatch) -> None:
    s = service.state
    assert service.coding_choice(s) == ("agent", "본인 Claude 연결 없음 → 서버 AI (체험)")
    run(service.mark_agent_seen("operator"))
    assert service.agents_connected(service.state) == ["operator"]
    assert service.coding_choice(service.state)[0] == "subscription"
    run(service.set_coding_mode("agent"))
    assert service.coding_choice(service.state) == ("agent", "회사 설정: 서버 AI")
    # 서버 AI가 쓴 토큰이 한도에 닿으면 설정이 서버 AI여도 내 Claude로
    monkeypatch.setenv("PLANNING_TRIAL_TOKENS", "500")
    t = service.state.tasks[0]
    t.coding = CodingRun(status="merged", updated_at=service.clock(), mode="agent", llm_tokens=500)
    mode, why = service.coding_choice(service.state)
    assert mode == "subscription" and "체험 한도를 다 씀 (500/500 토큰)" in why
    run(service.set_coding_mode("subscription"))
    assert service.coding_choice(service.state) == ("subscription", "회사 설정: 내 Claude")
    with pytest.raises(ActionError):
        run(service.set_coding_mode("robot"))


def test_agent_seen_is_written_once_per_ten_minutes(service) -> None:
    run(service.mark_agent_seen("operator"))
    version = service.version
    run(service.mark_agent_seen("operator"))  # 10분 안 → 다시 적지 않는다
    assert service.version == version


def test_trial_exhausted_blocks_server_ai_task(service, adapter, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_TRIAL_TOKENS", "100")
    t = service.state.tasks[0]
    t.coding = CodingRun(status="merged", updated_at=service.clock(), mode="agent", llm_tokens=100)
    with pytest.raises(ActionError) as err:
        run(service.create_dev_task(title="배너", plan="배너 문구 변경"))
    assert err.value.code == "TRIAL_EXHAUSTED"
    out = run(service.create_dev_task(title="배너", plan="배너 문구 변경", mode="subscription"))
    assert out.output["status"] == "queued"  # 내 Claude는 체험 한도와 상관없다


def test_handed_request_becomes_subscription_coding(service, adapter, monkeypatch) -> None:
    """실제 코딩을 켜면 넘겨받은 작업은 시연용 PR 대신 코딩 작업이 된다 (설정: 내 Claude)."""
    monkeypatch.setenv("PLANNING_REQUEST_CODING", "1")
    run(service.set_coding_mode("subscription"))
    plan(service, "answer_question", "SPEC-7", ANSWER)
    plan(service, "confirm_spec", "SPEC-7")
    tasks = _spec7_tasks(service)
    assert [t.id for t in tasks] == ["T-11", "T-12"]
    assert all(t.coding and t.coding.mode == "subscription" for t in tasks)
    assert all(t.coding.status == "queued" for t in tasks)
    assert not [p for p in service.state.prs if p.task in {"T-11", "T-12"}]  # 시연용 PR 없음
    texts = [a.text for a in service.state.activity if a.point == "dev_task_create"]
    assert any("T-11 요청 작업 → 개발자 Claude 대기 (회사 설정: 내 Claude" in x for x in texts)


def test_handed_request_without_llm_falls_back_to_own_claude(service, adapter, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_REQUEST_CODING", "1")
    coder.set_llm(None)  # 서버 AI 설정이 없으면 서버 AI로 띄울 수 없다
    plan(service, "answer_question", "SPEC-7", ANSWER)
    plan(service, "confirm_spec", "SPEC-7")
    tasks = _spec7_tasks(service)
    assert all(t.coding and t.coding.mode == "subscription" for t in tasks)
    texts = [a.text for a in service.state.activity if a.point == "dev_task_create"]
    assert any("서버 AI 설정(LLM) 없음 → 내 Claude" in x for x in texts)


def test_without_real_coding_keeps_demo_prs(service, adapter) -> None:
    """목 모드·실제 코딩 꺼짐이면 지금처럼 시연용 PR 기록을 만든다."""
    plan(service, "answer_question", "SPEC-7", ANSWER)
    plan(service, "confirm_spec", "SPEC-7")
    assert all(t.coding is None for t in _spec7_tasks(service))
    assert [p.id for p in service.state.prs if p.task in {"T-11", "T-12"}] == ["PR #31", "PR #32"]


def test_settings_api_and_work_call_marks_agent(service, monkeypatch) -> None:
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")
    client = TestClient(app)
    got = client.get("/api/planning/coding-settings").json()["data"]
    assert got["mode"] == "auto" and got["resolved"] == "agent" and got["trial_used"] == 0
    human = {"Authorization": "Bearer human-test-token"}
    admin = {"Authorization": "Bearer admin-test-token"}
    body = {"mode": "subscription"}
    assert client.post("/api/planning/coding-settings", json=body, headers=human).status_code == 403
    res = client.post("/api/planning/coding-settings", json=body, headers=admin)
    assert res.status_code == 200 and res.json()["data"]["resolved"] == "subscription"
    bad = client.post("/api/planning/coding-settings", json={"mode": "x"}, headers=admin)
    assert bad.status_code == 400
    # 본인 Claude가 일감 목록을 보면 '연결됨'
    assert client.get("/api/planning/work/tasks", headers=human).status_code == 200
    assert service.agents_connected(service.state) == ["operator"]


def test_handed_request_with_trial_left_queues_server_ai(service, adapter, monkeypatch) -> None:
    """설정 auto·본인 Claude 연결 없음·체험 한도 남음 → 서버 AI 코딩 작업 (바꿔 끼운 뒤 띄운다)."""
    from ax_agent_template.llm import LLMSettings

    class Idle:
        settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")

    monkeypatch.setenv("PLANNING_REQUEST_CODING", "1")
    coder.set_llm(Idle())
    spawned: list[str] = []
    monkeypatch.setattr(service, "_spawn_coder", spawned.append)
    plan(service, "answer_question", "SPEC-7", ANSWER)
    plan(service, "confirm_spec", "SPEC-7")
    tasks = _spec7_tasks(service)
    assert all(
        t.coding and t.coding.mode == "agent" and t.coding.model == "fake-coder" for t in tasks
    )
    assert spawned == ["T-11", "T-12"]

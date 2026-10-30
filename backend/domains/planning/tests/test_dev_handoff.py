"""기획 → 개발 넘기기: planning 서비스(start_dev) ↔ dev 서비스(receive_dev_result).

두 서비스 인스턴스를 메모리 안 가짜 송신기로 서로 잇는다 (네트워크 없음).
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from pydantic import BaseModel

from planning_agent.dev_handoff import RECEIVE_DEV_RESULT, START_DEV
from planning_agent.domain.models import dump
from planning_agent.domain.service import PlanningService
from planning_agent.main import app


class Wire:
    """보낸 넘기기를 상대 서비스 메서드로 바로 부른다."""

    def __init__(self, forward: bool = True):
        self.forward = forward
        self.sent: list[tuple[str, str]] = []
        self.planning = PlanningService(role="planning", dev_sender=self.send)
        self.dev = PlanningService(role="dev", dev_sender=self.send)

    async def send(self, skill: str, ctx: str, tenant: str, payload: BaseModel, key: str) -> str:
        self.sent.append((skill, key))
        if not self.forward:
            return "보냄"
        body = payload.model_dump(mode="json")
        if skill == START_DEV:
            await self.dev.start_dev(
                context_id=ctx, requester="planning", idempotency_key=key, **body
            )
        else:
            await self.planning.receive_dev_result(
                context_id=ctx, requester="dev", idempotency_key=key, **body
            )
        return "보냄"

    async def settle(self) -> None:
        for _ in range(10):
            await self.planning.wait_sent()
            await self.dev.wait_sent()

    def skills(self) -> list[str]:
        return [k for k, _ in self.sent]


def _new_spec(svc: PlanningService):
    return svc.state.specs[-1]


def test_general_request_handed_off_and_result_comes_back() -> None:
    async def run() -> Wire:
        w = Wire()
        await w.planning.inbound("support_ticket")
        await w.settle()
        return w

    w = asyncio.run(run())
    sp = _new_spec(w.planning)
    assert sp.status == "확정" and sp.handed_to_dev
    assert w.skills().count(START_DEV) == 1
    assert RECEIVE_DEV_RESULT in w.skills()
    # dev가 받은 기획서로 PR을 만든다 (planning은 PR을 만들지 않는다)
    dev_tasks = [t for t in w.dev.state.tasks if t.spec == sp.id]
    dev_prs = [p for p in w.dev.state.prs if p.task in {t.id for t in dev_tasks}]
    assert dev_tasks and dev_prs
    assert any(a.point == START_DEV and a.to == "dev" for a in w.dev.state.activity)
    # planning은 dev가 만든 작업·PR·요청 상태를 그대로 돌려받는다
    req = next(r for r in w.planning.state.requests if r.spec == sp.id)
    dev_req = next(r for r in w.dev.state.requests if r.id == req.id)
    assert req.state == dev_req.state
    assert [dump(t) for t in w.planning.state.tasks if t.spec == sp.id] == [
        dump(t) for t in dev_tasks
    ]
    assert [dump(p) for p in w.planning.state.prs if p.task in {t.id for t in dev_tasks}] == [
        dump(p) for p in dev_prs
    ]
    assert any(
        a.point == RECEIVE_DEV_RESULT and a.from_ == "dev" for a in w.planning.state.activity
    )
    # planning 쪽 넘기기 활동은 다른 부서 창구(outbox)에 쌓지 않는다
    assert all(m.to != "dev" for m in w.planning.state.outbox)


def test_simple_request_merged_by_dev() -> None:
    async def run() -> Wire:
        w = Wire()
        await w.planning.inbound("support_simple")
        await w.settle()
        return w

    w = asyncio.run(run())
    sp = _new_spec(w.planning)
    req = next(r for r in w.planning.state.requests if r.spec == sp.id)
    # planning은 작업 분할까지만 하고 PR 관문을 부르지 않는다
    assert "generate_pr" not in [c.action for c in w.planning.gate_calls]
    assert "merge_pr" not in [c.action for c in w.planning.gate_calls]
    dev_actions = [c.action for c in w.dev.gate_calls]
    assert dev_actions == ["start_dev", "generate_pr", "review_pr", "merge_pr", "record_case"]
    # dev가 간단 요청 규칙대로 자동 머지하고, planning에 완료가 돌아온다
    pr = next(p for p in w.planning.state.prs if p.task.startswith("T-") and p.auto)
    assert pr.merged and pr.auto
    assert req.state == "완료"
    assert all(t.state == "완료" for t in w.planning.state.tasks if t.spec == sp.id)


def test_handed_off_once() -> None:
    async def run() -> Wire:
        w = Wire(forward=False)
        await w.planning.inbound("support_ticket")
        await w.settle()
        await w.planning.mark_read("support")
        await w.planning.post_message("TH-1", "확인했습니다")
        await w.settle()
        return w

    w = asyncio.run(run())
    assert w.skills() == [START_DEV]
    # 결과가 오기 전 planning에는 새 PR이 없다
    sp = _new_spec(w.planning)
    tasks = {t.id for t in w.planning.state.tasks if t.spec == sp.id}
    assert tasks and not [p for p in w.planning.state.prs if p.task in tasks]


def test_dev_sends_result_only_when_changed() -> None:
    async def run() -> Wire:
        w = Wire()
        await w.planning.inbound("support_ticket")
        await w.settle()
        n = w.skills().count(RECEIVE_DEV_RESULT)
        await w.dev.mark_read("support")  # 기획서 작업·PR과 상관없는 변경
        await w.settle()
        assert w.skills().count(RECEIVE_DEV_RESULT) == n
        return w

    asyncio.run(run())


def test_combined_mode_sends_nothing() -> None:
    sent: list[str] = []

    async def record(skill: str, *_args) -> str:
        sent.append(skill)
        return "보냄"

    svc = PlanningService(role="", dev_sender=record)
    asyncio.run(svc.inbound("support_simple"))
    asyncio.run(svc.inbound("support_ticket"))
    assert sent == []
    assert not any(sp.handed_to_dev for sp in svc.state.specs)
    actions = [c.action for c in svc.gate_calls]
    assert actions[:8] == [
        "create_ticket",
        "write_spec",
        "confirm_spec",
        "split_tasks",
        "generate_pr",
        "review_pr",
        "merge_pr",
        "record_case",
    ]


def test_role_from_env(monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_ROLE", "dev")
    assert PlanningService().role == "dev"
    monkeypatch.delenv("PLANNING_ROLE")
    assert PlanningService().role == ""


def test_start_dev_is_idempotent() -> None:
    async def run() -> tuple[Wire, PlanningService]:
        w = Wire(forward=False)
        await w.planning.inbound("support_ticket")
        await w.settle()
        sp = _new_spec(w.planning)
        req = next(r for r in w.planning.state.requests if r.spec == sp.id)
        body = {
            "request": dump(req),
            "spec": dump(sp),
            "tasks": [dump(t) for t in w.planning.state.tasks if t.spec == sp.id],
        }
        first = await w.dev.start_dev(
            context_id=req.context_id, requester="planning", idempotency_key="k1", **body
        )
        prs = len(w.dev.state.prs)
        second = await w.dev.start_dev(
            context_id=req.context_id, requester="planning", idempotency_key="k1", **body
        )
        assert first.changed and not second.changed
        assert second.output == first.output
        assert len(w.dev.state.prs) == prs
        assert [c.action for c in w.dev.gate_calls].count("start_dev") == 1
        await w.settle()
        return w, w.dev

    asyncio.run(run())


def test_send_failure_keeps_state_and_logs() -> None:
    async def boom(*_args) -> str:
        raise RuntimeError("연결 안 됨")

    async def run() -> PlanningService:
        svc = PlanningService(role="planning", dev_sender=boom)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        return svc

    svc = asyncio.run(run())
    assert _new_spec(svc).handed_to_dev
    assert any(a.point == START_DEV and "보내지 못함" in a.text for a in svc.state.activity)


def test_handlers_need_key_and_acl(a2a_body) -> None:
    client = TestClient(app)
    payload = {"request": {}, "spec": {}, "tasks": []}
    no_key = client.post("/a2a", json=a2a_body(START_DEV, payload, token="mock:planning")).json()
    assert (no_key["status"], no_key["outcome"]) == ("failed", "missing_idempotency_key")
    denied = client.post(
        "/a2a", json=a2a_body(START_DEV, payload, token="mock:support", idempotency_key="k")
    ).json()
    assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    bad = client.post(
        "/a2a", json=a2a_body(START_DEV, payload, token="mock:planning", idempotency_key="k")
    ).json()
    assert (bad["status"], bad["outcome"]) == ("failed", "invalid_input")
    result = {"spec_id": "SPEC-9", "request_id": "REQ-1", "request_state": "모름", "tasks": []}
    wrong = client.post(
        "/a2a",
        json=a2a_body(RECEIVE_DEV_RESULT, result, token="mock:dev", idempotency_key="k2"),
    ).json()
    assert (wrong["status"], wrong["outcome"]) == ("failed", "invalid_input")
    other = client.post(
        "/a2a",
        json=a2a_body(RECEIVE_DEV_RESULT, result, token="mock:infra", idempotency_key="k3"),
    ).json()
    assert other["outcome"] == "FORBIDDEN_SKILL"


def test_dev_team_spec_is_sent_to_planning_with_request_and_spec() -> None:
    """개발팀이 직접 낸 기획도 기획 서비스가 알도록 요청·기획서째 보낸다."""
    w = Wire(forward=False)
    w.dev = PlanningService(role="dev", dev_sender=w.send, real_example=True)  # SPEC-D1 (개발팀)
    sp = next(x for x in w.dev.state.specs if x.origin == "dev_team")
    out = [x for x in w.dev._dev_outgoing(w.dev.state) if x[2].spec_id == sp.id]
    assert out and out[0][0] == RECEIVE_DEV_RESULT
    payload = out[0][2]
    assert payload.spec["id"] == sp.id and payload.request["id"] == payload.request_id
    # 기획 서비스에서 온 기획서는 요청·기획서를 다시 싣지 않는다
    other = [x for x in w.dev._dev_outgoing(w.dev.state) if x[2].spec_id != sp.id]
    assert all(x[2].spec is None and x[2].request is None for x in other)


def test_planning_stores_dev_team_request_and_spec() -> None:
    w = Wire(forward=False)
    w.dev = PlanningService(role="dev", dev_sender=w.send, real_example=True)  # SPEC-D1 (개발팀)
    sp = next(x for x in w.dev.state.specs if x.origin == "dev_team")
    req = next(r for r in w.dev.state.requests if r.id == sp.request)
    new_req = req.model_copy(update={"id": "REQ-D9", "spec": "SPEC-D9", "state": "작업 중"})
    new_sp = sp.model_copy(
        update={"id": "SPEC-D9", "request": "REQ-D9", "title": "개발팀 자체 기획"}
    )
    asyncio.run(
        w.planning.receive_dev_result(
            context_id=new_req.context_id,
            requester="dev",
            spec_id="SPEC-D9",
            request_id="REQ-D9",
            request_state="작업 중",
            tasks=[],
            prs=[],
            idempotency_key="k-d9",
            request=dump(new_req),
            spec=dump(new_sp),
        )
    )
    st = w.planning.state
    assert any(r.id == "REQ-D9" and r.via == "dev_team" for r in st.requests)
    assert any(s.id == "SPEC-D9" for s in st.specs)
    assert any("개발팀 기획 SPEC-D9 받음" in a.text for a in st.activity)

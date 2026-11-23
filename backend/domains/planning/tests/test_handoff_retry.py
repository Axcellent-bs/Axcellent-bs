"""넘기기 실패 나누기·다시 보내기 간격 (11/23, 메신저에 "start dev: failed"가 거듭 쌓이던 문제).

- 받는 쪽이 거절한 넘기기(`invalid_input` 등)는 한 번만 보내고 다시 보내지 않는다.
- 닿지 못한 넘기기는 30초·60초·120초… 최대 15분 간격으로 다시 보내고, 횟수는 다시 켜도 남는다.
- 다시 켰을 때 맞추기는 같은 입력으로 거절된 넘기기를 건너뛴다. 사람은 `resend_handoff`로 보낸다.
- 사람 할 일함으로 넘어간 넘기기는 거절이 아니다: 따로 남기고 다시 켰을 때 맞추기가 다시 보낸다.
- 횟수·기록은 상태 파일 옆 `.handoff.json`에 둔다 (예전 빌드가 새 상태 파일을 읽게).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from pydantic import BaseModel

from ax_workflow import base
from ax_workflow.core import ActionResult
from ax_workflow.dev_handoff import HANDED, PERMANENT, SENT, TRANSIENT, SendResult, classify
from ax_workflow.models import State
from dev_agent.service import DevService
from planning_agent.dev_handoff import RECEIVE_DEV_RESULT, START_DEV
from planning_agent.domain.service import PlanningService

REFUSED = "보내지 못함 (failed invalid_input)"
DOWN = "보내지 못함 (failed unreachable)"


class Sender:
    """`how`를 돌려주는 가짜 송신기 (바꿔 끼워 받는 쪽을 고친 것처럼 한다)."""

    def __init__(self, how: str):
        self.how = how
        self.sent: list[str] = []

    async def __call__(self, skill: str, ctx: str, tenant: str, p: BaseModel, key: str) -> str:
        self.sent.append(key)
        return self.how


class Mono:
    """`base._monotonic` 대신 쓰는 시계 (다시 보낼 때를 시험에서 움직인다)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _start_dev_logs(svc: PlanningService) -> list[str]:
    return [a.text for a in svc.state.activity if a.point == START_DEV and "보내지 못함" in a.text]


@pytest.mark.parametrize(
    ("how", "kind"),
    [
        ("보냄", SENT),
        (SendResult("보내지 못함 (failed invalid_input)", "failed", "invalid_input"), PERMANENT),
        (SendResult("x", "failed", "missing_idempotency_key"), PERMANENT),
        (SendResult("x", "failed", "gate_rejected"), PERMANENT),
        (SendResult("x", "failed", "not_found"), PERMANENT),
        (SendResult("x", "rejected", "FORBIDDEN_SKILL"), PERMANENT),
        (SendResult("x", "failed", "INVALID_OUTPUT"), PERMANENT),
        (SendResult("x", "failed", "http_404"), PERMANENT),
        (SendResult("x", "input-required", "handed_to_human"), HANDED),
        (SendResult("x", "submitted", ""), HANDED),
        (SendResult("x", "failed", "inbox_unreachable"), TRANSIENT),
        (SendResult("x", "failed", "UNAUTHORIZED"), PERMANENT),
        (SendResult("x", "failed", "no_assignee"), PERMANENT),
        (SendResult("x", "failed", "unreachable"), TRANSIENT),
        (SendResult("x", "canceled", "TIMEOUT"), TRANSIENT),
        (SendResult("x", "failed", "http_503"), TRANSIENT),
        (SendResult("x", "failed", "http_429"), TRANSIENT),
        (SendResult("x", "failed", "handler_error"), TRANSIENT),
        (SendResult("x", "failed", "service_disabled"), TRANSIENT),
        ("보내지 못함 (오류)", TRANSIENT),  # 보내다 난 예외
        ("보내지 못함 (rejected invalid_input)", PERMANENT),
        ("보내지 못함 (failed invalid_input)", PERMANENT),
        ("보내지 못함 (failed )", TRANSIENT),  # 코드 없음
        ("보내지 못함 (failed 연결 안 됨)", TRANSIENT),  # 코드가 아닌 글
    ],
)
def test_classify(how: str, kind: str) -> None:
    assert classify(how) == kind


def test_send_result_is_still_the_activity_text() -> None:
    r = SendResult("보내지 못함 (failed invalid_input)", "failed", "invalid_input")
    assert r == "보내지 못함 (failed invalid_input)" and r.startswith("보내지 못함")
    assert (r.status, r.outcome) == ("failed", "invalid_input")


def test_permanent_failure_is_sent_once_and_kept_visible() -> None:
    send = Sender(REFUSED)

    async def run() -> PlanningService:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        await svc.resend_unsent()  # 다시 보낼 것이 없다
        await svc.wait_sent()
        return svc

    svc = asyncio.run(run())
    assert len(send.sent) == 1
    assert svc._unsent == {} and not svc._retrying
    [fail] = svc._handoff_failed
    assert fail.key == send.sent[0] and fail.skill == START_DEV and "invalid_input" in fail.reason
    [log] = _start_dev_logs(svc)
    assert "다시 보내지 않음" in log


def test_transient_failures_back_off(monkeypatch) -> None:
    mono = Mono()
    monkeypatch.setattr(base, "_monotonic", mono)
    send = Sender(DOWN)

    async def run() -> PlanningService:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        key = send.sent[0]
        assert svc._attempts(key) == 1 and svc._due[key] == mono.now + 30
        for gap, attempts in ((30, 2), (60, 3), (120, 4)):
            mono.now += gap - 1
            await svc.resend_unsent(due_only=True)  # 아직 때가 아니다
            assert svc._attempts(key) == attempts - 1
            mono.now += 1
            await svc.resend_unsent(due_only=True)
            assert svc._attempts(key) == attempts
        return svc

    svc = asyncio.run(run())
    assert len(send.sent) == 4
    assert len(_start_dev_logs(svc)) == 1  # 처음 실패만 활동에
    assert [base.retry_delay(n) for n in (1, 2, 3, 4, 5, 6, 10)] == [
        30, 60, 120, 240, 480, 900, 900
    ]  # fmt: skip


def test_attempts_survive_reload_and_old_format_still_loads(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    send = Sender(DOWN)

    async def down() -> str:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        await svc.resend_unsent()
        return send.sent[0]

    key = asyncio.run(down())
    again = PlanningService(dev_sender=send)  # 다시 켬
    assert again._attempts(key) == 2
    # 상태 파일은 예전 모양 그대로: _unsent는 [skill, ctx, 입력], State에 새 필드 없음
    state_file = os.path.join(tmp_path, "planning_state_planning.json")
    with open(state_file, encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved["unsent"][key]) == 3
    State.model_validate(saved["state"])
    assert "handoff_failed" not in saved["state"]
    os.remove(f"{state_file}.handoff.json")  # 예전 빌드가 남긴 상태 (옆 파일 없음)
    old = PlanningService(dev_sender=send)
    assert key in old._unsent and old._attempts(key) == 1
    asyncio.run(old.resend_unsent())
    assert old._attempts(key) == 2


def test_broken_sidecar_is_ignored(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    PlanningService(dev_sender=Sender("보냄"))._save()
    side = os.path.join(tmp_path, "planning_state_planning.json.handoff.json")
    with open(side, "w", encoding="utf-8") as f:
        f.write('{"attempts": {"a": "x", "b": 3}, "failed": [{"key": 1}]}')
    svc = PlanningService(dev_sender=Sender("보냄"))
    assert svc._attempt_counts == {"b": 3} and svc._handoff_failed == []
    with open(side, "w", encoding="utf-8") as f:
        f.write("not json")
    assert PlanningService(dev_sender=Sender("보냄"))._handoff_failed == []


def test_success_clears_unsent_and_due() -> None:
    send = Sender(DOWN)

    async def run() -> PlanningService:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        send.how = "보냄"
        await svc.resend_unsent()
        return svc

    svc = asyncio.run(run())
    assert svc._unsent == {} and svc._due == {} and svc._handoff_failed == []


def test_resync_skips_permanent_failure_and_human_resend_clears_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    send = Sender(REFUSED)

    async def first() -> str:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        return send.sent[0]

    key = asyncio.run(first())

    async def restart() -> PlanningService:
        svc = PlanningService(dev_sender=send)  # 다시 켬: 거절 기록은 옆 파일에 남는다
        assert [f.key for f in svc._handoff_failed] == [key]
        await svc.resync_peer()
        assert send.sent.count(key) == 1  # 같은 입력으로 거절된 건 다시 맞추기에서 건너뛴다
        send.how = "보냄"  # 받는 쪽을 고쳤다
        out = await svc.dispatch({"type": "resendHandoff", "key": key})
        assert out.output == {"resent": [key]}
        await svc.wait_sent()
        return svc

    svc = asyncio.run(restart())
    assert send.sent.count(key) == 2
    assert svc._handoff_failed == [] and svc._unsent == {}
    assert any("사람이 다시 보냄" in a.text for a in svc.state.activity)


def test_resend_handoff_without_failures_is_not_found() -> None:
    from ax_workflow.core import ActionError

    svc = PlanningService(dev_sender=Sender("보냄"))
    with pytest.raises(ActionError) as err:
        asyncio.run(svc.dispatch({"type": "resendHandoff"}))
    assert err.value.code == "NOT_FOUND"


def test_dev_result_direction_is_not_retried_and_resync_skips_it() -> None:
    send = Sender(REFUSED)

    async def run() -> DevService:
        dev = DevService(dev_sender=send, real_example=True)
        items = [x for x in dev._dev_outgoing(dev.state, full=True) if x[0] == RECEIVE_DEV_RESULT]
        assert items
        skill, ctx, payload, key = items[0]
        await dev._send_one(skill, ctx, payload, key)
        await dev.resend_unsent()
        assert send.sent == [key] and dev._unsent == {}
        assert [f.key for f in dev._handoff_failed] == [key]
        await dev.resync_peer()
        assert send.sent.count(key) == 1
        return dev

    asyncio.run(run())


def test_handed_to_inbox_is_not_a_refusal_and_resync_resends_it() -> None:
    send = Sender(SendResult("보내지 못함 (input-required handed_to_human)", "input-required",
                             "handed_to_human"))  # fmt: skip

    async def run() -> PlanningService:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        key = send.sent[0]
        assert svc._unsent == {} and not svc._retrying  # 주기적으로 다시 보내지 않는다
        [f] = svc._handoff_failed
        assert (f.kind, f.key) == ("handed", key)
        [log] = _start_dev_logs(svc)
        assert "사람 할 일함으로 넘어감" in log and "거절" not in log
        snap = svc.public_snapshot()["handoff_failed"]
        assert snap[0]["kind"] == "handed" and "payload" not in snap[0]
        send.how = "보냄"  # 받는 쪽이 다시 켜졌다
        await svc.resync_peer()
        assert send.sent.count(key) == 2
        return svc

    svc = asyncio.run(run())
    assert svc._handoff_failed == [] and svc.public_snapshot()["handoff_failed"] == []


def test_invalid_output_refusal_names_where_to_look() -> None:
    send = Sender(SendResult("보내지 못함 (failed INVALID_OUTPUT)", "failed", "INVALID_OUTPUT"))

    async def run() -> PlanningService:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        return svc

    svc = asyncio.run(run())
    [log] = _start_dev_logs(svc)
    assert "버전 확인" in log and svc._handoff_failed[0].kind == "refused"


def test_unreadable_unsent_entry_is_moved_out_and_loop_keeps_going() -> None:
    send = Sender("보냄")

    async def run() -> PlanningService:
        svc = PlanningService(dev_sender=send)
        svc._unsent["bad"] = [START_DEV, "ctx_bad", {"nope": 1}]  # 입력 모델로 읽지 못함
        svc._unsent["short"] = [START_DEV]
        await svc.inbound("support_ticket")  # 정상 넘기기 하나를 꺼내 _unsent에 다시 넣는다
        await svc.wait_sent()
        skill, ctx, payload, _ = svc._dev_outgoing(svc.state, full=True)[0]
        svc._unsent["ok_key"] = [skill, ctx, payload.model_dump(mode="json")]
        send.sent.clear()
        await svc.resend_unsent()  # 못 읽는 줄 뒤에 있는 정상 줄도 보낸다
        assert send.sent == ["ok_key"] and svc._unsent == {}
        await svc.resend_unsent()  # 한 번 뺀 줄은 다시 보지 않는다
        return svc

    svc = asyncio.run(run())
    assert "bad" not in svc._unsent and "short" not in svc._unsent
    reasons = {f.key: f.reason for f in svc._handoff_failed}
    assert reasons == {"bad": "저장된 내용을 읽지 못함", "short": "저장된 내용을 읽지 못함"}
    lines = [a.text for a in svc.state.activity if "저장된 내용을 읽지 못함" in a.text]
    assert len(lines) == 2


def test_failed_action_drops_queued_resend() -> None:
    svc = PlanningService(dev_sender=Sender("보냄"))

    async def boom(s: State) -> ActionResult:
        svc._resend_pending.append(("start_dev", "ctx", None, "k"))  # type: ignore[arg-type]
        raise RuntimeError("액션 실패")

    with pytest.raises(RuntimeError):
        asyncio.run(svc._apply(boom))
    assert svc._resend_pending == []

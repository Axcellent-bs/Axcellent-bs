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


# ---- 다시 켰을 때 맞추기를 조용하게 (11/23, 켤 때마다 "start dev 부탁해요 ×5"·"failed ×2~3")
#
# - 받는 쪽이 받았다고 답한 넘기기(acked, 지문)는 옆 파일에 남고 다시 켜도 다시 보내지 않는다.
# - 켤 때는 받는 서비스가 뜰 때까지(최대 5분) 기다린 뒤 보낸다.
# - 받았다고 한 넘기기는 받는 쪽에 한 번 묻고(`handoff_known`) 받는 쪽에 없는 것만 다시 보낸다.


class Known:
    """받는 쪽에 묻기(`handoff_known`) 대신. `missing`은 받는 쪽이 잃은 키, None이면 묻지 못함."""

    def __init__(self, missing: set[str] | None = None, fail: bool = False):
        self.missing = missing or set()
        self.fail = fail
        self.asked: list[list[str]] = []

    async def __call__(self, peer: str, tenant: str, keys: list[str]) -> list[str] | None:
        self.asked.append(list(keys))
        return None if self.fail else [k for k in keys if k not in self.missing]


@pytest.fixture(autouse=True)
def _peer_keeps_state(monkeypatch) -> None:
    """이 파일의 서비스는 받는 쪽이 상태를 가진 것으로 묻는다 (네트워크 없음)."""
    monkeypatch.setattr(base, "ask_known", Known())


def _start_dev_keys(svc: PlanningService) -> list[str]:
    return [k for skill, _, _, k in svc._dev_outgoing(svc.state, full=True) if skill == START_DEV]


def _handed(tmp_path, monkeypatch) -> tuple[Sender, str]:
    """개발에 기획서 하나를 넘기고 받았다는 답을 받은 상태 파일을 만든다."""
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    send = Sender("보냄")

    async def run() -> str:
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        [key] = send.sent
        assert key in svc._acked
        return key

    return send, asyncio.run(run())


def test_resync_after_restart_sends_nothing_already_acked(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    again = PlanningService(dev_sender=send)  # 다시 켬: 받았다는 기록은 옆 파일에 남는다
    assert key in again._acked and key in _start_dev_keys(again)
    send.sent.clear()
    asyncio.run(again.resync_peer())
    assert send.sent == []
    assert again._acked.get(key)  # 지금 넘길 것이라 지우지 않는다


def test_resync_resends_changed_payload(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    again = PlanningService(dev_sender=send)
    spec = next(sp for sp in again.state.specs if sp.handed_to_dev)
    spec.title = f"{spec.title} (고침)"  # 받았다고 한 뒤 입력이 바뀜
    send.sent.clear()
    asyncio.run(again.resync_peer())
    assert send.sent == [key]
    send.sent.clear()
    asyncio.run(again.resync_peer())  # 바뀐 입력도 받았다고 했으니 또 보내지 않는다
    assert send.sent == []


def test_resync_resends_never_acked_key_and_old_sidecar_loads(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    side = os.path.join(tmp_path, "planning_state_planning.json.handoff.json")
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"attempts": {}, "failed": []}, f)  # 예전 빌드가 남긴 옆 파일 (acked 없음)
    old = PlanningService(dev_sender=send)
    assert old._acked == {} and old._handoff_failed == []
    send.sent.clear()
    asyncio.run(old.resync_peer())
    assert send.sent == [key]  # 받았다는 기록이 없으니 보낸다
    with open(side, encoding="utf-8") as f:
        saved = json.load(f)
    assert set(saved) >= {"attempts", "failed", "acked"} and key in saved["acked"]


def test_resync_prunes_acked_keys_no_longer_handed(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    again = PlanningService(dev_sender=send)
    again._acked["ctx_gone:start_dev:SPEC-gone"] = "f" * 16  # 지운 기획서
    asyncio.run(again.resync_peer())
    assert set(again._acked) == {key}


def test_ack_record_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(base, "ACKED_MAX", 3)
    svc = PlanningService(dev_sender=Sender("보냄"))
    for n in range(5):
        svc._ack(f"k{n}", "fp")
    assert list(svc._acked) == ["k2", "k3", "k4"]


def test_resend_handoff_all_ignores_acked(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    again = PlanningService(dev_sender=send)
    send.sent.clear()

    async def run() -> ActionResult:
        out = await again.dispatch({"type": "resendHandoff", "all": True})
        await again.wait_sent()
        return out

    out = asyncio.run(run())
    assert send.sent == [key] and out.output == {"resent": [key]}
    assert any("모두 다시 보냄" in a.text for a in again.state.activity)


def test_resend_handoff_all_without_anything_is_not_found() -> None:
    from ax_workflow.core import ActionError

    svc = PlanningService(dev_sender=Sender("보냄"))
    svc.state.specs = []
    with pytest.raises(ActionError) as err:
        asyncio.run(svc.dispatch({"type": "resendHandoff", "all": True}))
    assert err.value.code == "NOT_FOUND"


class Peer:
    """받는 서비스: `downs`번 묻는 동안은 안 떠 있고 그 뒤로 뜬다. 안 떴을 때 보내면 닿지 못함."""

    def __init__(self, downs: int):
        self.downs = downs
        self.probes = 0
        self.sent: list[str] = []

    @property
    def up(self) -> bool:
        return self.probes > self.downs

    async def probe(self) -> bool:
        self.probes += 1
        return self.up

    async def __call__(self, skill: str, ctx: str, tenant: str, p: BaseModel, key: str) -> str:
        self.sent.append(key)
        return "보냄" if self.up else DOWN


def _unacked_planning(peer: Peer, monkeypatch) -> tuple[PlanningService, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(base, "_sleep", fake_sleep)

    async def build() -> PlanningService:
        svc = PlanningService(dev_sender=Sender("보냄"))
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        svc._acked.clear()  # 받았다는 기록이 없는 넘긴 기획서 (예전 빌드에서 넘김)
        svc._send_dev = peer
        monkeypatch.setattr(svc, "_peer_up", peer.probe)
        return svc

    return asyncio.run(build()), sleeps


def test_start_resync_waits_for_peer_then_sends_without_failures(monkeypatch) -> None:
    peer = Peer(downs=2)
    svc, sleeps = _unacked_planning(peer, monkeypatch)
    before = len(_start_dev_logs(svc))
    asyncio.run(svc.resync_peer(wait_peer=True))
    assert peer.probes == 3 and sleeps == [2.0, 4.0]
    assert peer.sent and svc._unsent == {}
    assert len(_start_dev_logs(svc)) == before  # 실패 줄이 없다
    assert set(peer.sent) <= set(svc._acked)


def test_start_resync_gives_up_waiting_after_cap_and_still_sends(monkeypatch) -> None:
    peer = Peer(downs=10**6)
    svc, sleeps = _unacked_planning(peer, monkeypatch)
    asyncio.run(svc.resync_peer(wait_peer=True))
    assert sum(sleeps) == base.PEER_WAIT_MAX_SECONDS
    assert max(sleeps) <= base.PEER_WAIT_STEP_MAX_SECONDS
    assert peer.sent  # 그래도 보내고, 닿지 못한 건 간격을 늘려 다시 보낸다
    assert set(peer.sent) <= set(svc._unsent)


def test_resync_without_wait_does_not_probe(monkeypatch) -> None:
    peer = Peer(downs=0)
    svc, sleeps = _unacked_planning(peer, monkeypatch)
    asyncio.run(svc.resync_peer())
    assert peer.probes == 0 and sleeps == [] and peer.sent


def test_peer_service_follows_call_agent_routing() -> None:
    assert PlanningService(dev_sender=Sender("보냄"))._peer_service() == "dev"
    assert DevService(dev_sender=Sender("보냄"))._peer_service() == "planning"


def test_dev_result_direction_resync_is_quiet_once_acked() -> None:
    send = Sender("보냄")

    async def run() -> None:
        dev = DevService(dev_sender=send, real_example=True)
        await dev.resync_peer()
        assert any(k for k in send.sent if ":dev_result:" in k)
        send.sent.clear()
        await dev.resync_peer()  # 기획이 모두 받았다고 했다
        assert send.sent == []

    asyncio.run(run())


def test_dev_result_reack_keeps_start_dev_quiet() -> None:
    """개발이 돌려준 결과로 start_dev 입력(작업·요청 상태)이 바뀌어도
    다시 켰을 때 또 보내지 않는다."""

    async def run() -> None:
        from planning_agent.domain.models import dump

        send = Sender("보냄")
        svc = PlanningService(dev_sender=send)
        await svc.inbound("support_ticket")
        await svc.wait_sent()
        [key] = send.sent
        sp = next(x for x in svc.state.specs if x.handed_to_dev)
        req = svc._request_of_spec(svc.state, sp)
        tasks = [dump(t) for t in svc.state.tasks if t.spec == sp.id]
        assert tasks
        tasks[0]["title"] = f"{tasks[0]['title']} (개발이 고침)"
        await svc.receive_dev_result(
            context_id=req.context_id, requester="dev", spec_id=sp.id, request_id=req.id,
            request_state=req.state, tasks=tasks, prs=[], idempotency_key="dev-result-1",
        )  # fmt: skip
        send.sent.clear()
        await svc.resync_peer()
        assert key not in send.sent

    asyncio.run(run())


def test_resync_resends_acked_key_the_peer_lost(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    again = PlanningService(dev_sender=send)
    again._ask_known = known = Known(missing={key})  # 개발이 상태를 잃었다
    send.sent.clear()
    asyncio.run(again.resync_peer())
    assert known.asked == [[key]]  # 한 번 묻는다
    assert send.sent == [key]


def test_resync_asks_nothing_when_nothing_is_acked(monkeypatch) -> None:
    peer = Peer(downs=0)
    svc, _ = _unacked_planning(peer, monkeypatch)
    svc._ask_known = known = Known()
    asyncio.run(svc.resync_peer())
    assert known.asked == [] and peer.sent


def test_resync_falls_back_to_acked_skip_when_asking_fails(tmp_path, monkeypatch) -> None:
    send, key = _handed(tmp_path, monkeypatch)
    again = PlanningService(dev_sender=send)
    again._ask_known = known = Known(fail=True)
    send.sent.clear()
    asyncio.run(again.resync_peer())
    assert known.asked == [[key]] and send.sent == []  # 모두 다시 보내지 않는다


def test_handoff_known_answers_only_applied_keys() -> None:
    from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
    from ax_workflow.a2a import handoff_known_handler

    svc = PlanningService(dev_sender=Sender("보냄"))
    svc._idempotent["k-applied"] = ActionResult(output={})
    handler = handoff_known_handler(lambda: svc)
    from datetime import UTC, datetime, timedelta

    meta = RequestMeta(
        context_id="handoff_known:planning", tenant_id=svc.tenant_id, token="t", path=[],
        deadline=datetime.now(UTC) + timedelta(seconds=10),
    )  # fmt: skip
    req = AgentRequest(meta=meta, skill="handoff_known", input={"keys": ["k-lost", "k-applied"]})
    out = asyncio.run(handler(req))
    assert out.status == TaskStatus.COMPLETED and out.output == {"known": ["k-applied"]}
    bad = AgentRequest(meta=meta, skill="handoff_known", input={"keys": "x"})
    assert asyncio.run(handler(bad)).outcome == "invalid_input"


def test_ask_known_returns_none_when_the_call_fails(monkeypatch) -> None:
    from ax_contracts.schemas import AgentResult, TaskStatus
    from ax_workflow import dev_handoff

    async def failed(target, req, **_):
        return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unreachable")

    async def boom(target, req, **_):
        raise RuntimeError("토큰 없음")

    async def ok(target, req, **_):
        assert (target, req.skill, req.meta.idempotency_key) == ("dev", "handoff_known", None)
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, output={"known": req.input["keys"][:1]}
        )

    for fake, want in ((failed, None), (boom, None), (ok, ["a"])):
        monkeypatch.setattr(dev_handoff, "call_agent", fake)
        assert asyncio.run(dev_handoff.ask_known("dev", "t", ["a", "b"])) == want


def test_handoff_known_is_on_both_cards_and_skill_lists() -> None:
    from ax_agent_template.services import BY_ID, route_target
    from dev_agent.handlers import DEV_HANDLERS
    from planning_agent.handlers import PLANNING_HANDLERS

    assert "handoff_known" in PLANNING_HANDLERS and "handoff_known" in DEV_HANDLERS
    assert "handoff_known" in BY_ID["planning"].skills and "handoff_known" in BY_ID["dev"].skills
    assert route_target("dev", "handoff_known") == "dev"
    assert route_target("planning", "handoff_known") == "planning"

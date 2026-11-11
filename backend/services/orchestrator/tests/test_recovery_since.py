"""복구 검증(verify_recovery)의 since = 조치가 끝난 시각 (#909).

사건 열린 시각부터 세면 장애 동안의 실패가 늘 섞여, 장애를 풀어도 복구로 판정되지 않았다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from ax_orchestrator.engine import Engine
from ax_orchestrator.guide import DEPTS
from ax_orchestrator.mock.fake_departments import FakeDepartments
from ax_orchestrator.models import Incident, StepRun, iso
from ax_orchestrator.plans.scenario_a import _recovery, fixed_at, scenario_a
from ax_orchestrator.registry import Registry

OPENED = datetime(2026, 11, 11, 1, 0, tzinfo=UTC)
ROLLED = OPENED + timedelta(minutes=4)
PAST_FIX = OPENED + timedelta(minutes=3)
RELEASED = OPENED + timedelta(minutes=9)


def _inc() -> Incident:
    return Incident("ctx_inc_since", "scenario_a", "결제 실패", "payment", opened_at=OPENED)


def _step(step_id: str, status: str, finished_at: datetime | None) -> StepRun:
    return StepRun(step_id, step_id, step_id, status=status, finished_at=finished_at)


def test_rollback_finished_time() -> None:
    inc = _inc()
    inc.steps["rollback"] = _step("rollback", "completed", ROLLED)
    assert fixed_at(inc) == ROLLED
    assert _recovery(inc) == {"component": "payment", "since": iso(ROLLED)}


def test_past_fix_finished_time_when_fault_was_released() -> None:
    inc = _inc()
    inc.steps["past_fix"] = _step("past_fix", "completed", PAST_FIX)
    inc.outputs["past_fix"] = {"fault_released": True}
    inc.steps["rollback"] = _step("rollback", "skipped", PAST_FIX)
    assert fixed_at(inc) == PAST_FIX


def test_past_fix_that_did_not_release_is_not_a_fix() -> None:
    inc = _inc()
    inc.steps["past_fix"] = _step("past_fix", "completed", PAST_FIX)
    inc.outputs["past_fix"] = {"fault_released": False}
    assert fixed_at(inc) == OPENED


def test_person_released_the_fault() -> None:
    """사람이 해제하면 shop.recovered(recovery=fault_released)를 받은 시각."""
    inc = _inc()
    inc.recovered_at = RELEASED
    inc.shared["recovery"] = {"recovery": "fault_released", "released_by": "admin"}
    assert fixed_at(inc) == RELEASED


@pytest.mark.parametrize("how", [None, {"recovery": "metrics_normal", "released_by": None}])
def test_recovery_signal_without_a_release_keeps_opened_at(how: dict | None) -> None:
    inc = _inc()
    inc.recovered_at = RELEASED
    if how is not None:
        inc.shared["recovery"] = how
    assert fixed_at(inc) == OPENED


def test_nothing_done_keeps_opened_at() -> None:
    inc = _inc()
    inc.steps["rollback"] = _step("rollback", "transfer_queued", ROLLED)
    assert fixed_at(inc) == OPENED


@pytest.mark.asyncio
async def test_scenario_a_sends_rollback_finished_time_to_verify_recovery() -> None:
    fakes = FakeDepartments()
    registry = Registry()
    await registry.load(DEPTS, fakes.fetch_card)
    engine = Engine(registry, fakes, hold_timeout=0.1)
    inc = Incident("ctx_inc_flow", "scenario_a", "결제 실패", "payment", opened_at=OPENED)
    await engine.run(inc, scenario_a())

    assert inc.steps["rollback"].status == "completed"
    sent = [r for d, r in fakes.calls if d == "infra" and r.skill == "verify_recovery"]
    assert sent, "복구 검증을 부르지 않았다"
    since = sent[0].input["since"]
    assert since == iso(inc.steps["rollback"].finished_at)
    assert since != iso(OPENED)

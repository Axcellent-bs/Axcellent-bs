"""복구 시간의 끝점: 복구 검증이 복구를 확인한 시각 기준. 마무리 단계가 길어도 늘지 않는다.

끝점 순서: 검증이 `recovered=true`면 쇼핑몰 복구 신호(검증보다 먼저 왔을 때)나 검증 시각,
그것도 없으면 닫힌 시각. 쇼핑몰 신호는 검증이 복구를 확인했을 때만 인정한다.
공통 로그 `incident.resolved`의 `recovered_at`도 같은 값이라 두 탭 숫자가 같다.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template import common_log
from ax_agent_template.common_log import SqliteLogStore
from ax_contracts.data_access.common_log import CommonLogQuery
from ax_orchestrator.models import Incident, StepRun
from ax_orchestrator.resolved_log import log_resolved
from ax_orchestrator.store import incident_from_dict, incident_to_dict

# 11.12 14:26 사건(ctx_inc_07446cd1ffe7)의 시각들 (251 사건 기록)
OPENED = datetime(2026, 11, 12, 5, 26, 23, 778822, tzinfo=UTC)
SIGNAL = datetime(2026, 11, 12, 5, 26, 25, 119130, tzinfo=UTC)  # 쇼핑몰 복구 신호
VERIFIED = datetime(2026, 11, 12, 5, 26, 56, 680263, tzinfo=UTC)  # 복구 검증 checked_at
CLOSED = datetime(2026, 11, 12, 5, 27, 11, 360000, tzinfo=UTC)  # 장부·기록까지 끝난 시각


def _incident(*, recovered: bool | None = True, checked_at: datetime | None = VERIFIED) -> Incident:
    inc = Incident("ctx_inc_end", "scenario_a", "결제 실패", "payment", opened_at=OPENED)
    inc.status = "resolved"
    inc.closed_at = CLOSED
    if recovered is not None:
        run = StepRun("recovery", "verify_recovery", "복구 검증 (infra)", status="completed")
        run.finished_at = VERIFIED + timedelta(milliseconds=5)
        inc.steps["recovery"] = run
        out: dict = {"recovered": recovered, "infra_metrics_ok": recovered}
        if checked_at is not None:
            out["checked_at"] = checked_at.isoformat().replace("+00:00", "Z")
        inc.outputs["recovery"] = out
    return inc


def test_shop_signal_before_a_successful_verification_is_the_end() -> None:
    inc = _incident()
    inc.recovered_at = SIGNAL
    assert inc.recovery_end() == SIGNAL
    assert inc.mttr_seconds() == 1.34


def test_without_a_shop_signal_the_verification_time_is_the_end() -> None:
    inc = _incident()
    assert inc.recovery_end() == VERIFIED
    assert inc.mttr_seconds() == 32.9


def test_without_a_successful_verification_the_closed_time_is_the_end() -> None:
    assert _incident(recovered=None).recovery_end() == CLOSED  # 복구 검증이 없는 계획


def test_a_shop_signal_is_not_trusted_unless_the_verification_confirmed() -> None:
    """검증이 복구를 확인하지 못했으면 쇼핑몰 신호 시각을 끝점으로 쓰지 않는다."""
    inc = _incident(recovered=False)
    inc.recovered_at = SIGNAL
    assert inc.recovery_end() == CLOSED
    assert inc.impact_window()[1] == OPENED + timedelta(minutes=15)  # 영향 구간도 끊지 않는다


def test_a_signal_after_the_verification_does_not_move_the_end() -> None:
    inc = _incident()
    inc.recovered_at = VERIFIED + timedelta(seconds=10)
    assert inc.recovery_end() == VERIFIED


def test_impact_window_ends_at_the_same_point() -> None:
    inc = _incident()
    inc.recovered_at = SIGNAL
    assert inc.impact_window()[1] == inc.recovery_end() == SIGNAL
    assert _incident().impact_window()[1] == VERIFIED


def test_long_wrap_up_does_not_add_to_the_recovery_time() -> None:
    """장부 기록·사례 기록이 오래 걸려 늦게 닫혀도 복구 시간은 그대로다."""
    quick, slow = _incident(), _incident()
    slow.closed_at = CLOSED + timedelta(minutes=10)
    assert quick.mttr_seconds() == slow.mttr_seconds() == 32.9


def test_a_degraded_incident_still_waits_for_the_shop_signal() -> None:
    inc = _incident(recovered=False)
    inc.status = "degraded"
    assert inc.recovery_end() is None
    inc.recovered_at = CLOSED + timedelta(minutes=3)
    inc.settle_after_recovery()
    assert inc.recovery_end() == inc.recovered_at  # 닫힌 뒤라 검증이 다시 돌지 않는다


def test_old_saved_incident_restores_with_the_same_end() -> None:
    """예전 저장분(검증 결과에 checked_at이 없음)도 다시 읽으면 단계가 끝난 시각으로 센다."""
    inc = _incident(checked_at=None)
    saved = incident_to_dict(inc)
    loaded = incident_from_dict(saved)
    assert loaded.recovery_end() == inc.steps["recovery"].finished_at
    with_signal = _incident()
    with_signal.recovered_at = SIGNAL
    assert incident_from_dict(incident_to_dict(with_signal)).mttr_seconds() == 1.34


def test_view_carries_the_recovery_end_and_the_fault_kind() -> None:
    """화면의 "정상 복귀"는 복구 시간 카드와 같은 끝점, 장애 종류는 쇼핑몰 신호에서."""
    inc = _incident()
    inc.recovered_at = SIGNAL
    inc.signal = {"component": "payment", "fault_kind": "switch"}
    view = inc.view()
    assert view["recovery_end_at"] == "2026-11-12T05:26:25.119130Z"
    assert view["fault_kind"] == "switch"
    inc.signal = {"fault_kind": "아무거나"}  # 모르는 값은 싣지 않는다
    assert inc.view()["fault_kind"] is None
    degraded = _incident(recovered=False)
    degraded.status = "degraded"
    assert degraded.view()["recovery_end_at"] is None


@pytest.fixture
def logs(tmp_path) -> Iterator[SqliteLogStore]:
    store = SqliteLogStore(str(tmp_path / "common_log.db"))
    common_log.set_store(store)
    yield store
    common_log.reset_store()
    store.close()


def test_resolved_log_carries_the_same_end(logs: SqliteLogStore) -> None:
    """두 탭(사건 표의 MTTR, 데이터 운영 지표의 복구 시간)이 같은 끝점을 본다."""
    inc = _incident()
    assert log_resolved(inc)
    found = logs.query(CommonLogQuery(context_ids=[inc.context_id], limit=10)).records
    payload = next(r.payload for r in found if r.kind == "event_published")
    end = datetime.fromisoformat(payload["recovered_at"])
    start = datetime.fromisoformat(payload["detected_at"])
    assert end == VERIFIED
    assert round((end - start).total_seconds(), 2) == inc.mttr_seconds()

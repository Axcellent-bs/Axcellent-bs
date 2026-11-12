"""총괄의 incident.resolved 기록에 실린 시각(detected_at·recovered_at)으로 복구 시간을 센다.

총괄은 이벤트가 해결로 끝날 때 공통 로그에 `event_published`(incident.resolved)를 한 번 남기고,
payload에 이벤트를 연 시각과 복구 시간의 끝 시각을 싣는다. 그 차이가 총괄 `metrics.mttr_seconds`다
(총괄 쪽 테스트: services/orchestrator/tests/test_resolved_log.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ax_contracts.data_access.common_log import LogRecord
from data_agent.metrics.kpi import compute_kpi

CTX = "ctx_inc_payload00001"
OPENED = datetime(2026, 11, 12, 7, 52, 18, tzinfo=UTC)
# 총괄이 같은 사건에서 metrics.mttr_seconds로 남긴 값 (복구 신호 − 이벤트를 연 시각)
ORCH_MTTR = 59.0
RECOVERED = OPENED + timedelta(seconds=ORCH_MTTR)


def _rec(n: int, at: datetime, kind: str, payload: dict | None = None, **kw) -> LogRecord:
    return LogRecord.model_validate(
        {
            "log_id": f"log_payload_{n:03d}",
            "tenant_id": "shop-demo",
            "context_id": CTX,
            "occurred_at": at,
            "source": kw.pop("source", "data"),
            "kind": kind,
            "payload": payload or {},
            **kw,
        }
    )


def _records(resolved_payload: dict) -> list[LogRecord]:
    """다른 부서 기록은 이벤트를 연 뒤 0.4초부터, 해결 기록은 복구 뒤 6분에 남는다."""
    return [
        _rec(1, OPENED + timedelta(seconds=0.4), "request_received", skill="impact_analysis"),
        _rec(
            2,
            OPENED + timedelta(seconds=3),
            "result_sent",
            skill="impact_analysis",
            task_status="completed",
        ),
        _rec(
            3,
            RECOVERED + timedelta(minutes=6),
            "event_published",
            {"event_type": "incident.resolved", "event_id": "evt_a", **resolved_payload},
            source="orchestrator",
        ),
    ]


def test_payload_times_give_the_same_mttr_as_the_orchestrator() -> None:
    payload = {"detected_at": OPENED.isoformat(), "recovered_at": RECOVERED.isoformat()}
    computed = compute_kpi(_records(payload))
    incident = computed.output.incidents[0]
    assert incident.detected_at == OPENED  # 첫 기록(0.4초 뒤)이 아니라 이벤트를 연 시각
    assert incident.recovered_at == RECOVERED  # 기록 시각(6분 뒤)이 아니라 실린 복구 시각
    assert incident.mttr_seconds == int(ORCH_MTTR) == 59
    assert computed.recovery_basis[CTX] == "incident.resolved"


def test_old_record_without_payload_times_counts_as_before() -> None:
    computed = compute_kpi(_records({}))
    incident = computed.output.incidents[0]
    first = OPENED + timedelta(seconds=0.4)
    logged = RECOVERED + timedelta(minutes=6)
    assert incident.detected_at == first  # 가장 이른 기록 시각
    assert incident.recovered_at == logged  # 해결 기록의 기록 시각
    assert incident.mttr_seconds == int((logged - first).total_seconds())


def test_unreadable_or_reversed_payload_times_fall_back_to_record_times() -> None:
    logged = RECOVERED + timedelta(minutes=6)
    first = OPENED + timedelta(seconds=0.4)
    for payload in (
        {"detected_at": "어제", "recovered_at": 12},
        # 시간대 없음
        {"detected_at": "2026-11-12T07:52:18", "recovered_at": "2026-11-12T07:53:17"},
        {"detected_at": RECOVERED.isoformat(), "recovered_at": OPENED.isoformat()},  # 앞뒤 바뀜
    ):
        incident = compute_kpi(_records(payload)).output.incidents[0]
        assert (incident.detected_at, incident.recovered_at) == (first, logged), payload

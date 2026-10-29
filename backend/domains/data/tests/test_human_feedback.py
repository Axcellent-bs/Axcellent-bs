"""kpi_summary 운영 중 지표: 받은 요청 반려율·승인 거절률·확인할 수 없음 비율 (공통 로그로 센다)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.data_access.common_log import LogRecord
from data_agent.metrics.kpi import compute_kpi, human_feedback

T0 = datetime(2026, 10, 29, 1, 0, tzinfo=UTC)


def _rec(i: int, kind: str, ctx: str | None = "ctx_req_0001", **payload: Any) -> LogRecord:
    return LogRecord(
        log_id=f"log_{i}",
        context_id=ctx,
        occurred_at=T0 + timedelta(seconds=i),
        source="x",
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
    )


RECORDS = [
    _rec(1, "task_status", outcome="handed_to_human"),  # 넘김 시작은 세지 않는다
    _rec(2, "task_status", outcome="handoff_done"),
    _rec(3, "task_status", outcome="handoff_rejected"),
    _rec(4, "task_status", outcome="handoff_done"),
    _rec(5, "task_status", "ctx_inc_0001", outcome="review_approved"),
    _rec(6, "task_status", "ctx_inc_0001", outcome="review_rejected"),
    _rec(7, "result_sent", "ctx_inc_0001", outcome=None),
    _rec(8, "result_sent", "ctx_inc_0001", outcome="unavailable"),
    _rec(9, "result_sent", "ctx_inc_0001", outcome="service_disabled"),
    _rec(10, "result_sent", "ctx_inc_0001", outcome="invalid_input"),
]


def test_rates_come_from_the_common_log() -> None:
    fb = human_feedback(RECORDS)
    assert fb is not None
    assert (fb.handoffs_closed, fb.handoffs_rejected, fb.handoff_reject_rate) == (3, 1, 0.3333)
    assert (fb.reviews_decided, fb.reviews_rejected, fb.review_reject_rate) == (2, 1, 0.5)
    assert (fb.results_total, fb.results_unknown, fb.unknown_rate) == (4, 2, 0.5)


def test_no_denominator_means_unknown_not_zero() -> None:
    fb = human_feedback([_rec(1, "request_received")])
    assert fb is not None
    assert fb.handoff_reject_rate is None
    assert fb.review_reject_rate is None
    assert fb.unknown_rate is None


def test_no_records_means_no_feedback() -> None:
    assert human_feedback([]) is None
    assert compute_kpi([]).output.human_feedback is None


def test_kpi_summary_output_carries_feedback_including_non_incident_records() -> None:
    out = compute_kpi(RECORDS).output
    assert out.human_feedback is not None
    assert out.human_feedback.handoffs_closed == 3  # ctx_req_ 기록도 센다

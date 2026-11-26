"""새 회사 일간 리포트: 재무가 그때 장부를 못 읽은 날은 다음 날까지 기다리지 않고 다시 묻는다.

11/26 누비투어: #1894 배포 직후(10:44) 지난 20일을 채울 때 재무가 사이트 변환기 첫 읽기에서
"orders, products 자료가 없어"로 답했고, 그 날들이 하루 내내 데이터 홈 총거래액 "확인할 수 없음"
으로 남았다. 캣북스는 예전처럼 다음 날 한 번만 다시 만든다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from data_agent.monitor import FINANCE_RETRY_AFTER, days_to_snapshot
from data_agent.report_store import ReportStore, Snapshot

NUBI = "c-cf2f0d"
DAY = date(2026, 11, 25)
MADE = datetime(2026, 11, 26, 1, 44, tzinfo=UTC)  # 10:44 KST


def _snap(tenant: str, reason: str | None) -> Snapshot:
    return Snapshot(
        tenant_id=tenant,
        day=DAY,
        created_at=MADE,
        payload={"company": {"gmv": None if reason else 1, "finance_reason": reason}},
        has_unknown=reason is not None,
    )


def _store(tenant: str, reason: str | None) -> ReportStore:
    store = ReportStore(":memory:")
    store.put(_snap(tenant, reason))
    return store


LOST = "재무 장부에 orders, products 자료가 없어 매출을 확인할 수 없음"


def test_new_company_transient_finance_day_is_rebuilt_after_retry_wait() -> None:
    store = _store(NUBI, LOST)
    assert (DAY, True) not in days_to_snapshot(store, NUBI, MADE + timedelta(minutes=5))
    assert (DAY, True) in days_to_snapshot(store, NUBI, MADE + FINANCE_RETRY_AFTER)


def test_call_failure_is_also_retried_for_new_company() -> None:
    store = _store(NUBI, "재무 closing_report 호출 실패 (TimeoutError)")
    assert (DAY, True) in days_to_snapshot(store, NUBI, MADE + FINANCE_RETRY_AFTER)


def test_unfinished_utc_day_and_disabled_finance_are_not_retried_early() -> None:
    for reason in (
        "재무 마감 기준 UTC 날짜가 아직 끝나지 않아 다음 날 다시 받음",
        "재무 서비스가 꺼져 있어 closing_report를 받지 못함",
    ):
        store = _store(NUBI, reason)
        assert (DAY, True) not in days_to_snapshot(store, NUBI, MADE + FINANCE_RETRY_AFTER)


def test_catbooks_keeps_next_day_once_rule() -> None:
    for tenant in ("shop-demo", "catbooks"):
        store = _store(tenant, LOST)
        assert (DAY, True) not in days_to_snapshot(store, tenant, MADE + FINANCE_RETRY_AFTER)


def test_complete_day_is_not_rebuilt() -> None:
    store = _store(NUBI, None)
    assert DAY not in [d for d, _ in days_to_snapshot(store, NUBI, MADE + timedelta(hours=3))]

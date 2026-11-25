"""데이터 출처: 일별 집계 병행 비교 한 줄 (3단계 설계 6.3).

- 그 회사 계산 기록이 있으면 수집기 상태에 "일별 집계" 줄이 붙는다
- 마지막 기준일부터 다름 0인 날이 이어진 수, 마지막 날이 다르면 다른 칸 수
- 다른 회사 기록은 보이지 않는다. 마지막 계산이 26시간보다 오래되면 집계 멈춤
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from ax_contracts.data_access.sales_aggregates import AggCompareRow, AggRun
from data_agent.aggregate.store import DB_ENV, AggregateStore
from data_agent.skills import data_sources as ds

NOW = datetime(2026, 11, 25, 3, 0, tzinfo=UTC)


def _fill(path, days: dict[date, bool], tenant: str = "shop-demo") -> None:  # noqa: ANN001
    agg = AggregateStore(path)
    for i, (d, same) in enumerate(sorted(days.items())):
        at = NOW - timedelta(days=len(days) - i)
        agg.save_run(AggRun(tenant_id=tenant, run_id=f"r{i}", as_of=d, input="sales_db",
                            started_at=at, finished_at=at, status="done"))  # fmt: skip
        agg.replace_compare(tenant, d, [
            AggCompareRow(tenant_id=tenant, as_of=d, target="sales_analysis:7d", same=True,
                          compared_at=at),
            AggCompareRow(tenant_id=tenant, as_of=d, target="sales_analysis:30d", same=same,
                          diffs=[] if same else ["revenue: 1 != 2"], compared_at=at),
        ])  # fmt: skip
    agg.close()


def test_streak_of_same_days(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "aggregates.db"
    _fill(path, {date(2026, 11, 21): False, date(2026, 11, 22): True, date(2026, 11, 23): True,
                 date(2026, 11, 24): True})  # fmt: skip
    got = ds.aggregate_state({DB_ENV: str(path)}, "shop-demo", NOW)
    assert got is not None and got.collector_id == "daily_aggregate" and not got.stalled
    assert got.reason == "일별 집계 병행 비교: 3일 연속 같음(마지막 11월 24일)"
    assert ds.aggregate_state({DB_ENV: str(path)}, "other-co", NOW) is None  # 다른 회사는 없음
    assert ds.aggregate_state({DB_ENV: str(tmp_path / "none.db")}, "shop-demo", NOW) is None


def test_last_day_differs_and_stall(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "aggregates.db"
    _fill(path, {date(2026, 11, 23): True, date(2026, 11, 24): False})
    got = ds.aggregate_state({DB_ENV: str(path)}, "shop-demo", NOW)
    assert got is not None and got.reason == "일별 집계 병행 비교: 11월 24일 다름 1"
    late = ds.aggregate_state({DB_ENV: str(path)}, "shop-demo", NOW + timedelta(days=2))
    assert late is not None and late.stalled and late.warning.startswith("집계 멈춤")

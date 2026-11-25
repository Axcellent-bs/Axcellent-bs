"""병행 비교 (설계 6절, 3-1b).

원본 경로(`sales.db`, `sales_sql.tally`)와 집계 경로(`aggregates.db`)가 같은 기간에 같은 숫자를
내는지 기준일마다 남긴다. 3-3(원본 저장 끄기)은 며칠 연속 "다름 0"이 조건이다.

같은 정의로 비교한다: 기간은 기준일 24시까지 꽉 찬 7·30일(원본도 그 기간으로 다시 센다).
- 매출 분석: 매출·주문 수·쿠폰 주문 수·분류별 매출·유입 경로별 매출·상품별 주문 수 (일별 행 합)
- 고객 구성: 구매자·재구매 고객·새/재구매 매출 (기간 스냅샷 `all` 줄, 숨긴 칸은 원본도 k 미만인지)

결과는 `agg_compare`에 한 대상마다 한 줄(다른 칸 이름과 두 값). 구매자 id·주문 id는 남기지 않는다.

지난 날짜 비교(`compare_history`, 3단계 병렬 B, 11/25): 하루씩 쌓이는 비교를 기다리지 않고,
이미 있는 `sales.db`·일별 행으로 지난 N일을 기준일마다 견준다. 저장하지 않고 결과만 돌려준다
(`agg_compare`는 하루 계산이 쓰는 그대로). 지난 기준일의 구매자 스냅샷은 보관 규칙으로 지워졌을 수
있어 매출 분석만 견주고, 스냅샷이 남은 날만 고객 구성도 견준다(없으면 건너뜀, 다름으로 세지 않음).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ax_contracts.data_access.sales_aggregates import AggCompareRow
from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore

PERIODS = (7, 30)


@dataclass
class CompareResult:
    as_of: date
    rows: list[AggCompareRow] = field(default_factory=list)

    @property
    def same(self) -> int:
        return sum(1 for r in self.rows if r.same)

    @property
    def different(self) -> int:
        return sum(1 for r in self.rows if not r.same)

    def line(self) -> str:
        d = self.as_of
        return (
            f"[aggregate] 병행 비교 {d.month}월 {d.day}일: 같음 {self.same} · 다름 {self.different}"
        )


def _diff(name: str, want: object, got: object, out: list[str]) -> None:
    if want != got:
        out.append(f"{name}: {want} != {got}")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v}


def compare_day(  # noqa: PLR0913 - 두 저장소·회사·기준일·시각·저장 여부
    sales: SalesStore,
    agg: AggregateStore,
    tenant_id: str,
    as_of: date,
    now: datetime,
    *,
    save: bool = True,
    history: bool = False,
) -> CompareResult:
    """`history`면 스냅샷 없는 고객 구성은 건너뛴다(지난 기준일). `save`가 아니면 남기지 않는다."""
    end_day = as_of + timedelta(days=1)
    result = CompareResult(as_of)
    snap = {r["period"]: r for r in agg.buyer_rows(tenant_id, as_of) if r["dim_kind"] == "all"}
    for n in PERIODS:
        start_day = end_day - timedelta(days=n)
        t, _ = tally(sales, tenant_id, day_window(start_day)[0], day_window(end_day)[0])
        got = _sum_rows(agg, tenant_id, start_day, end_day)
        diffs: list[str] = []
        _diff("revenue", t.revenue, got["revenue"], diffs)
        _diff("orders", t.orders, got["orders"], diffs)
        _diff("coupon_orders", t.coupon_orders, got["coupon_orders"], diffs)
        _diff("by_category", _clean(dict(t.by_category)), _clean(got["cat"]), diffs)
        chan = {f"{s or ''}/{m or ''}": v for (s, m), v in t.by_channel.items()}
        _diff("by_channel", _clean(chan), _clean(got["chan"]), diffs)
        _diff("product_orders", _clean(dict(t.product_orders)), _clean(got["prod_orders"]), diffs)
        result.rows.append(_row(tenant_id, as_of, f"sales_analysis:{n}d", diffs, now))
        diffs = []
        row = snap.get(f"{n}d")
        if row is None and history:
            continue  # 지난 기준일 스냅샷은 보관 규칙으로 지워졌을 수 있다
        if row is None:
            diffs.append("buyers: 스냅샷 없음")
        else:
            new_buyers = t.buyers - t.repeat_buyers
            for name, want, value, hidden in (
                ("buyers", t.buyers, row["buyers"], row["buyers_hidden"]),
                ("repeat_buyers", t.repeat_buyers, row["repeat_buyers"], row["repeat_hidden"]),
                ("new_buyers", new_buyers, row["new_buyers"], row["new_hidden"]),
            ):
                if not hidden:  # 숨긴 칸은 숫자가 없어 비교하지 않는다(숨김 규칙은 시험이 본다)
                    _diff(name, want, value, diffs)
            _diff("new_revenue", t.new_revenue, row["new_revenue"], diffs)
            _diff("repeat_revenue", t.repeat_revenue, row["repeat_revenue"], diffs)
        result.rows.append(_row(tenant_id, as_of, f"customer_mix:{n}d", diffs, now))
    if save:
        agg.replace_compare(tenant_id, as_of, result.rows)
    return result


def compare_history(
    sales: SalesStore, agg: AggregateStore, tenant_id: str, last: date, days: int, now: datetime
) -> list[CompareResult]:
    """`last`부터 거꾸로 `days`일을 기준일마다 견준다(저장 안 함). 오래된 날부터 돌려준다."""
    out = []
    for i in range(days - 1, -1, -1):
        as_of = last - timedelta(days=i)
        out.append(compare_day(sales, agg, tenant_id, as_of, now, save=False, history=True))
    return out


def _row(
    tenant_id: str, as_of: date, target: str, diffs: list[str], now: datetime
) -> AggCompareRow:
    return AggCompareRow(tenant_id=tenant_id, as_of=as_of, target=target, same=not diffs,
                         diffs=diffs, compared_at=now)  # fmt: skip


def _sum_rows(agg: AggregateStore, tenant_id: str, start: date, end: date) -> dict:
    out: dict = {"revenue": 0, "orders": 0, "coupon_orders": 0, "cat": Counter(),
                 "chan": Counter(), "prod_orders": Counter()}  # fmt: skip
    for r in agg.sales_rows(tenant_id, start, end):
        net = r["revenue_net"] - r["refund_amount"]
        if r["grain"] == "order":
            out["revenue"] += net
            out["orders"] += r["orders"]
            out["coupon_orders"] += r["orders"] if r["coupon_used"] else 0
            out["chan"][f"{r['src_source']}/{r['src_medium']}"] += net
        else:
            out["cat"][r["category_code"]] += net
            if r["product_id"]:
                out["prod_orders"][r["product_id"]] += r["orders"]
    return out

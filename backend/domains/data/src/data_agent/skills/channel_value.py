"""첫 구매 유입 경로별 고객 가치 (`channel_value`, 11/17, grade read).

첫 결제 완료 주문을 어느 경로(src_source)로 했는지로 새 고객을 묶고, 그 뒤 `value_horizon_days`
(카탈로그, 기본 90일) 동안 1인당 매출·주문 수·재구매 비율을 본다. 표준 판매 저장소만 읽고 LLM을 쓰지
않는다. 숫자는 `SalesStore.segment_session`의 SQL 집계로 받는다(주문을 파이썬으로 올리지 않는다).

- 관문: `segment_analysis.snapshot`(period 기간) 그대로. 고객 기록 관문을 주문을 읽기 전에 본다
- 새 고객 = 첫 결제 완료 주문(저장소 전체 기록에서, 접수 시각·주문 id 순 처음)이 period 안인 사람
- 경로 = 그 첫 주문의 src_source. 이름은 카탈로그 `source_labels`. 표시가 없으면 "직접 유입",
  카탈로그에 없는 출처는 "기타 경로"로 묶는다(매출 분석의 유입 경로 이름과 같다)
- 가치를 보는 기간 = 첫 구매 시각 이상 ~ + value_horizon_days일 미만. 그 기간이 아직 다 안 지난
  고객은 빼고 뺀 수를 근거에 남긴다
- 매출 = 기간 안 결제 완료 주문의 상품 금액 − 쿠폰 − 포인트 − 그 사람 결제 완료 주문의 기간 안 환불
  (매출 분석 정의). 1인 매출 = 합 ÷ 새 고객(반올림, 0 미만이면 0). 1인 주문 = 주문 수 ÷ 새 고객(소수
  둘째 자리). 재구매 비율 = 기간 안 결제 완료 주문 2건 이상인 사람 ÷ 새 고객
- 숨김: 새 고객이 `min_group_size` 미만인 경로는 숫자를 숨긴다. 경로 합계(전체 새 고객)를 공개된
  합계로 보고 보완 숨김(`segment_analysis.protect`)을 쓴다
- 비용(cost_krw)은 재무 연결 전까지 늘 None, cost_note "재무 연결 대기"
- 순서: 1인 매출 큰 순(숨긴 경로는 뒤, 이름 순)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CatalogSales,
    ChannelValueOutput,
    ChannelValueRow,
    SegmentPeriod,
)
from data_agent.aggregate import read_buyers as rb
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_store import SalesStore
from data_agent.skills import segment_analysis as seg
from data_agent.skills.retention import HISTORY_FROM
from data_agent.skills.sales_analysis import DIRECT, OTHER_SOURCE

SKILL = "channel_value"
COST_NOTE = "재무 연결 대기"
QUERIES = [*seg.QUERIES, "sales_store.sales_refunds"]


def source_label(source: str | None, cfg: CatalogSales) -> str:
    if not source:
        return DIRECT
    return cfg.source_labels.get(source, OTHER_SOURCE)


def build_rows(
    rows: list[tuple[str | None, int, int, int, int]],
    refunds: dict[str | None, int],
    cfg: CatalogSales,
) -> list[ChannelValueRow]:
    """출처별 집계를 경로 이름으로 묶고(직접 유입·기타 경로), 1인 값과 숨김을 정한다."""
    grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # 사람, 매출, 주문, 재구매
    sources: dict[str, set[str | None]] = defaultdict(set)
    for src, n, net, orders, rep in rows:
        label = source_label(src, cfg)
        g = grouped[label]
        for i, v in enumerate((n, net - refunds.get(src, 0), orders, rep)):
            g[i] += v
        sources[label].add(src or None)
    labels = sorted(grouped)
    counts = [grouped[k][0] for k in labels]
    least = cfg.min_group_size
    hidden = seg.protect(counts, {i for i, n in enumerate(counts) if n < least},
                         [list(range(len(labels)))])  # fmt: skip
    out = []
    for i, label in enumerate(labels):
        n, revenue, orders, rep = grouped[label]
        one = sources[label]
        source = next(iter(one)) if len(one) == 1 and label != OTHER_SOURCE else None
        if i in hidden:
            out.append(ChannelValueRow(source=source, label=label, suppressed=True))
            continue
        out.append(ChannelValueRow(
            source=source, label=label, new_customers=n,
            revenue_per_customer=max(0, round(revenue / n)), repeat_rate=seg.ratio(rep, n),
            orders_per_customer=round(orders / n, 2),
        ))  # fmt: skip
    out.sort(key=lambda r: (r.suppressed, -(r.revenue_per_customer or 0), r.label))
    return out


def from_snapshot(
    agg: AggregateStore, tenant_id: str, period: SegmentPeriod, cfg: CatalogSales, as_of: date
) -> tuple[list[ChannelValueRow], int, date]:
    """어제 기준 채널 가치 스냅샷(묶기·숨김은 저장할 때 끝남)을 화면 행으로 (3-2 ③).
    경로 하나가 카탈로그 출처 하나뿐이면 그 출처를 `source`로 둔다."""
    stored = [r for r in agg.more_rows("agg_channel_value", tenant_id, as_of)
              if r["period"] == period]  # fmt: skip
    by_label: dict[str, list[str]] = defaultdict(list)
    for src, label in cfg.source_labels.items():
        by_label[label].append(src)
    out = []
    for r in stored:
        label = r["source_label"]
        one = by_label.get(label, [])
        source = one[0] if len(one) == 1 and label not in (DIRECT, OTHER_SOURCE) else None
        n = r["new_buyers"]
        if r["new_hidden"] or not n:
            out.append(ChannelValueRow(source=source, label=label, suppressed=True))
            continue
        out.append(ChannelValueRow(
            source=source, label=label, new_customers=n,
            revenue_per_customer=max(0, round(r["horizon_net"] / n)),
            repeat_rate=seg.ratio(r["repeaters"], n),
            orders_per_customer=round(r["horizon_orders"] / n, 2),
        ))  # fmt: skip
    out.sort(key=lambda r: (r.suppressed, -(r.revenue_per_customer or 0), r.label))
    excluded = stored[0]["excluded_incomplete"] if stored else 0
    return out, excluded, as_of


def summary_of(out: ChannelValueOutput) -> str:
    label = seg.SEGMENT_LABEL[out.period]
    if out.status != "ok":
        text = "유입 경로별 고객 가치 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
        return seg.with_source(text, out.basis)
    shown = [r for r in out.rows if not r.suppressed]
    if not shown:
        text = f"{label} 첫 구매 고객의 경로별 가치를 보여 줄 만큼 큰 경로가 없음 (표본 부족)."
        return seg.with_source(text, out.basis)
    best = shown[0]
    text = (
        f"{label} 첫 구매 고객 기준, {best.label}로 들어온 고객이 {out.horizon_days}일 동안 1인 "
        f"{best.revenue_per_customer:,}원으로 가장 높음."
    )
    if len(shown) > 1 and shown[-1].revenue_per_customer:
        low = shown[-1]
        text += f" 가장 낮은 경로는 {low.label} (1인 {low.revenue_per_customer:,}원)."
    text += f" 경로 비용은 {COST_NOTE}."
    return seg.with_source(text, out.basis)


def channel_value(  # noqa: PLR0913 - 입력 칸을 그대로 받는다
    store: SalesStore,
    tenant_id: str,
    period: SegmentPeriod,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> tuple[ChannelValueOutput, int]:
    """(출력, 가치 기간이 아직 안 지나 뺀 새 고객 수)."""
    snap = seg.snapshot(store, tenant_id, period, cfg, catalog_id, now)
    common: dict[str, Any] = {
        "period": period,
        "horizon_days": cfg.value_horizon_days if cfg else ChannelValueOutput().horizon_days,
        "cost_note": COST_NOTE,
        "basis": snap.basis,
        "quality": snap.report,
    }
    if snap.reasons or cfg is None:
        out = ChannelValueOutput(**common, status="insufficient",
                                 insufficient_reasons=snap.reasons)  # fmt: skip
        return out.model_copy(update={"summary": summary_of(out)}), 0
    sales_cfg = cfg
    got = rb.from_snapshot(
        tenant_id, now, lambda agg, day: from_snapshot(agg, tenant_id, period, sales_cfg, day)
    )
    if got is not None:
        rows_out, excluded, as_of = got
        common["basis"] = rb.basis_at(snap.basis, rb.window_of(period, as_of))
        out = ChannelValueOutput(**common, rows=rows_out)
        return out.model_copy(update={"summary": rb.with_note(summary_of(out), as_of)}), excluded
    w = snap.window
    with store.segment_session(tenant_id, HISTORY_FROM, now, []) as s:
        rows, refunds, excluded = s.channel_value(w.start, w.end, cfg.value_horizon_days, now)
    out = ChannelValueOutput(**common, rows=build_rows(rows, refunds, cfg))
    return out.model_copy(update={"summary": summary_of(out)}), excluded


def evidence_of(out: ChannelValueOutput, excluded: int, now: datetime) -> Evidence:
    extra = (
        f"가치 기간 첫 구매부터 {out.horizon_days}일, 기간이 아직 안 지나 뺀 새 고객 {excluded}명, "
        f"경로 비용 {COST_NOTE}"
    )
    ev = seg.evidence(out.basis, now, extra)
    return ev.model_copy(update={"queries": [*QUERIES, "sales_store 구매자별 첫 결제 완료 주문"]})

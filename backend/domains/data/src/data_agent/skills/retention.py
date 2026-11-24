"""재구매·이탈 (`retention`, 11/17, grade read): 첫 구매 달별 재구매율과 떠날 것 같은 단골 묶음.

표준 판매 저장소만 읽고 LLM을 쓰지 않는다. 숫자는 `SalesStore.segment_session`의 SQL 집계
(구매자별 첫·마지막 결제 완료 주문, GROUP BY·COUNT)로 받아 이 파일에서 계산한다. 주문을 파이썬으로
올리지 않는다. 회사마다 다른 이름(속성·값·장르)과 기준(churn_days·repeat_min_orders·
min_group_size)은 카탈로그 `[sales]`에서 온다.

관문 (segment_analysis.snapshot 그대로)
- 고객 기록 관문(고객 없음·고객 내보내기 오류)을 주문을 읽기 전에 본다. 걸리면 바로 insufficient
- 품질 관문은 최근 365일(한국 0시) 기준. 설정에 없는 속성·값 조건, 속성 설정 없음도 insufficient

코호트 (cohorts)
- 코호트 = 고객의 첫 결제 완료 주문(저장소 전체 기록에서)의 한국 달. 지금 달은 빼고 그 전
  `cohorts`개 달(오래된 달부터). 지금 달은 30일도 지나지 않아 모든 칸이 "아직"이라 넣지 않는다
- 재구매(N = 30·60·90일) = 첫 구매 시각부터 N일 안(첫 구매 시각 이상, + N일 미만)에 결제 완료
  주문이 2건 이상(첫 주문 포함). 비율 = 재구매한 사람 ÷ 그 달 첫 구매 고객
- 아직: 그 달 마지막 날 끝(다음 달 1일 0시, 한국) + N일이 지금보다 뒤면 그 칸은 None
- 속성 조건(`filters`)이 있으면 그 조건에 맞는 고객만
- 숨김: 그 달 고객이 `min_group_size` 미만이면 suppressed(숫자 None). 다시 산 사람이 1명 이상
  기준 미만인 칸도 None(작은 묶음 수가 비율로 드러나지 않게)

떠날 것 같은 단골 (at_risk)
- 단골 = 결제 완료 주문이 `repeat_min_orders`건 이상(저장소 전체 기록). 떠날 위험 = 단골 중 마지막
  결제 완료 주문 뒤 `churn_days`일이 지난 사람(가정 기준, 카탈로그)
- 묶음 = 카탈로그 속성 값 전부의 조합(예: 연령대 × 성별 × 직업). 속성이 하나라도 모름이거나
  카탈로그에 없는 값인 사람은 세지 않는다. `filters`가 있으면 그 값만
- 숨김: 단골 또는 떠날 위험이 `min_group_size` 미만인 묶음은 숨긴다(0명도 숨긴 칸으로 센다).
  묶음 합계를 공개된 합계로 보고 보완 숨김(`segment_analysis.protect`)을 단골 수·떠날 위험 수에
  함께 쓴다
- 보이는 묶음 중 떠날 위험이 많은 순(같으면 비율, 이름 순) 상위 `AT_RISK_TOP`개
- top_item_label = 그 묶음 단골이 가장 많이 산 장르(산 단골 수, 같으면 이름·code 순, 카탈로그
  장르만)
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CatalogSales,
    RetentionAtRisk,
    RetentionCohort,
    RetentionOutput,
)
from data_agent.aggregate import read_buyers as rb
from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_quality import KST
from data_agent.sales_store import REPEAT_DAYS, SalesStore
from data_agent.skills import segment_analysis as seg

SKILL = "retention"
GATE_PERIOD = "365d"  # 품질 관문을 보는 기간
AT_RISK_TOP = 5
HISTORY_FROM = datetime(2000, 1, 1, tzinfo=UTC)  # 첫 구매를 찾는 저장소 전체 기록의 시작
QUERIES = [*seg.QUERIES]


@dataclass(frozen=True)
class Month:
    label: str  # "YYYY-MM" (한국)
    start: datetime  # UTC (한국 1일 0시)
    end: datetime  # 다음 달 1일 0시


def month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def cohort_months(count: int, now: datetime) -> list[Month]:
    """지금 달(한국)을 뺀 그 전 `count`개 달, 오래된 달부터."""
    local = now.astimezone(KST)
    out: list[Month] = []
    for back in range(count, 0, -1):
        y, m = month_add(local.year, local.month, -back)
        ny, nm = month_add(y, m, 1)
        start = datetime(y, m, 1, tzinfo=KST).astimezone(UTC)
        end = datetime(ny, nm, 1, tzinfo=KST).astimezone(UTC)
        out.append(Month(f"{y}-{m:02d}", start, end))
    return out


def matches(cell: tuple, keys: list[str], filters: dict[str, str]) -> bool:
    return all(cell[keys.index(k)] == v for k, v in filters.items())


def cohort_rows(
    months: list[Month],
    counts: list[tuple[str, tuple, int, int, int, int]],
    keys: list[str],
    filters: dict[str, str],
    least: int,
    now: datetime,
) -> list[RetentionCohort]:
    sums: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for month, cell, n, r30, r60, r90 in counts:
        if matches(cell, keys, filters):
            for i, v in enumerate((n, r30, r60, r90)):
                sums[month][i] += v
    rows = []
    for m in months:
        n, *repeat = sums.get(m.label, [0, 0, 0, 0])
        if n < least:
            rows.append(RetentionCohort(cohort_month=m.label, suppressed=True))
            continue
        rates: dict[str, float | None] = {}
        for days, r in zip(REPEAT_DAYS, repeat, strict=True):
            done = m.end + timedelta(days=days) <= now
            rates[f"r{days}"] = seg.ratio(r, n) if done and (r == 0 or r >= least) else None
        rows.append(RetentionCohort(cohort_month=m.label, customers=n, **rates))
    return rows


def combos(cfg: CatalogSales, filters: dict[str, str]) -> list[tuple[str, ...]]:
    """카탈로그 속성 값 전부의 조합 (조건이 있는 속성은 그 값만), 카탈로그 순서."""
    return list(itertools.product(*(
        [filters[a.key]] if a.key in filters else list(a.values) for a in cfg.attributes
    )))  # fmt: skip


def hide(reg: list[int], risk: list[int], least: int) -> set[int]:
    """숨길 묶음: 단골·떠날 위험이 기준 미만(0 포함) + 묶음 합계로 풀리지 않게 보완 숨김(둘 다)."""
    everything = [list(range(len(reg)))]
    hidden = {i for i in range(len(reg)) if reg[i] < least or risk[i] < least}
    while True:
        more = seg.protect(risk, hidden, everything) | seg.protect(reg, hidden, everything)
        if more == hidden:
            return hidden
        hidden = more


def top_genre(cell: tuple, genres: list[tuple[tuple, str, int]], cfg: CatalogSales) -> str | None:
    found = [(n, cfg.genres[g], g) for c, g, n in genres if c == cell and g in cfg.genres]
    if not found:
        return None
    return min(found, key=lambda x: (-x[0], x[1], x[2]))[1]


def at_risk_rows(  # noqa: PLR0913 - 집계 재료를 그대로 받는다
    cfg: CatalogSales,
    filters: dict[str, str],
    regular: list[tuple[tuple, int, int]],
    genres: list[tuple[tuple, str, int]],
) -> list[RetentionAtRisk]:
    least = cfg.min_group_size
    keys = [a.key for a in cfg.attributes]
    table = {cell: (n, risk) for cell, n, risk in regular}
    cells = combos(cfg, filters)
    reg = [table.get(c, (0, 0))[0] for c in cells]
    risk = [table.get(c, (0, 0))[1] for c in cells]
    hidden = hide(reg, risk, least)
    shown = []
    for i, cell in enumerate(cells):
        if i in hidden:
            continue
        values = dict(zip(keys, cell, strict=True))
        shown.append(RetentionAtRisk(
            filters=values, label=seg.filter_label(values, cfg), regulars=reg[i],
            at_risk=risk[i], share=seg.ratio(risk[i], reg[i]),
            top_item_label=top_genre(cell, genres, cfg),
        ))  # fmt: skip
    shown.sort(key=lambda r: (-(r.at_risk or 0), -(r.share or 0), r.label))
    return shown[:AT_RISK_TOP]


def _cell(text: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in text.split(";")) if text else {}


def from_snapshot(
    agg: AggregateStore, tenant_id: str, cohorts: int, cfg: CatalogSales, as_of: date
) -> tuple[list[RetentionCohort], list[RetentionAtRisk], date] | None:
    """어제 기준 코호트·떠날 위험 스냅샷을 화면 행으로 (3-2 ④, 조건 없음만).

    숨김은 저장할 때 화면 규칙 그대로 끝났다. 계산 때 단골·떠남 기준이 지금 카탈로그와 다르면
    None(원본 경로)."""
    end = day_window(as_of)[1]
    months = cohort_months(cohorts, end)
    stored = {r["cohort_month"]: r for r in agg.cohort_rows(tenant_id, as_of) if not r["attr_cell"]}
    if any(m.label not in stored for m in months):
        return None
    out_c = []
    for m in months:
        r = stored[m.label]
        n = r["first_buyers"]
        if r["first_hidden"] or n is None:
            out_c.append(RetentionCohort(cohort_month=m.label, suppressed=True))
            continue
        repeat = json.loads(r["repeat"])
        rates: dict[str, float | None] = {}
        for days in REPEAT_DAYS:
            h = repeat.get(str(days))
            ok = h is not None and not h["hidden"] and h["value"] is not None
            rates[f"r{days}"] = seg.ratio(h["value"], n) if ok else None
        out_c.append(RetentionCohort(cohort_month=m.label, customers=n, **rates))
    risk_rows = agg.at_risk_rows(tenant_id, as_of)
    if any(r["churn_days"] != cfg.churn_days or r["repeat_min_orders"] != cfg.repeat_min_orders
           for r in risk_rows):  # fmt: skip
        return None
    shown = []
    for r in risk_rows:
        if r["regulars_hidden"] or r["at_risk_hidden"]:
            continue
        values = _cell(r["attr_cell"])
        shown.append(RetentionAtRisk(
            filters=values, label=seg.filter_label(values, cfg), regulars=r["regulars"],
            at_risk=r["at_risk"], share=seg.ratio(r["at_risk"], r["regulars"]),
            top_item_label=r["top_genre"],
        ))  # fmt: skip
    shown.sort(key=lambda x: (-(x.at_risk or 0), -(x.share or 0), x.label))
    return out_c, shown[:AT_RISK_TOP], as_of


def summary_of(out: RetentionOutput, cfg: CatalogSales | None) -> str:
    if out.status != "ok":
        text = "재구매·이탈 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
        return seg.with_source(text, out.basis)
    shown = [c for c in out.cohorts if not c.suppressed]
    text = f"첫 구매 달 {len(out.cohorts)}개 중 {len(shown)}개 달 재구매율 계산"
    done = [c for c in shown if c.r30 is not None]
    if done:
        last = done[-1]
        text += f" ({last.cohort_month} 첫 구매 고객 30일 안 재구매 {seg.pct(last.r30)})"
    text += "."
    if out.at_risk:
        top = out.at_risk[0]
        text += (
            f" 떠날 것 같은 단골이 가장 많은 묶음은 {top.label} "
            f"({top.at_risk}명, 단골의 {seg.pct(top.share)})."
        )
    else:
        text += " 떠날 것 같은 단골 묶음은 표본 부족으로 보여 주지 않음."
    if cfg is not None:
        text += (
            f" 기준: 단골 = 결제 완료 {cfg.repeat_min_orders}번 이상, 떠남 = 마지막 주문 뒤 "
            f"{cfg.churn_days}일 (가정)."
        )
    return seg.with_source(text, out.basis)


def retention(  # noqa: PLR0913 - 입력 칸을 그대로 받는다
    store: SalesStore,
    tenant_id: str,
    cohorts: int,
    filters: dict[str, str],
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> RetentionOutput:
    snap = seg.snapshot(store, tenant_id, GATE_PERIOD, cfg, catalog_id, now)
    months = cohort_months(cohorts, now)
    basis = snap.basis.model_copy(update={
        "period_from": months[0].start, "previous_from": months[0].start,
        "previous_to": months[0].start,
    })  # fmt: skip
    common: dict[str, Any] = {
        "filters": dict(filters),
        "churn_days": cfg.churn_days if cfg else RetentionOutput().churn_days,
        "basis": basis,
        "quality": snap.report,
    }
    problems: list[str] = []
    if cfg is not None:
        problems += seg.filter_problems(filters, cfg)
        if not cfg.attributes:
            problems.append("설정에 고객 속성(attributes)이 없음")
    reasons = problems + snap.reasons
    if reasons or cfg is None:
        out = RetentionOutput(**common, status="insufficient", insufficient_reasons=reasons)
        return out.model_copy(update={"summary": summary_of(out, cfg)})
    sales_cfg = cfg
    got = (
        None
        if filters
        else rb.from_snapshot(
            tenant_id, now, lambda agg, day: from_snapshot(agg, tenant_id, cohorts, sales_cfg, day)
        )
    )
    if got is not None:
        cohort_out, at_risk_out, as_of = got
        start = cohort_months(cohorts, day_window(as_of)[1])[0].start
        common["basis"] = basis.model_copy(update={
            "period_from": start, "period_to": day_window(as_of)[1], "previous_from": start,
            "previous_to": start,
        })  # fmt: skip
        out = RetentionOutput(**common, cohorts=cohort_out, at_risk=at_risk_out)
        return out.model_copy(update={"summary": rb.with_note(summary_of(out, cfg), as_of)})
    keys = [a.key for a in cfg.attributes]
    with store.segment_session(tenant_id, HISTORY_FROM, now, keys) as s:
        counts = s.cohort_counts(months[0].start, months[-1].end)
        regular = s.regular_counts(cfg.repeat_min_orders, now - timedelta(days=cfg.churn_days))
        genres = s.regular_genres(cfg.repeat_min_orders)
    out = RetentionOutput(
        **common,
        cohorts=cohort_rows(months, counts, keys, filters, cfg.min_group_size, now),
        at_risk=at_risk_rows(cfg, filters, regular, genres),
    )
    return out.model_copy(update={"summary": summary_of(out, cfg)})


def evidence_of(out: RetentionOutput, now: datetime, cfg: CatalogSales | None) -> Evidence:
    extra = (
        f"단골 기준 결제 완료 {cfg.repeat_min_orders}번 이상, 떠남 기준 {cfg.churn_days}일(가정), "
        f"재구매 {'·'.join(str(d) for d in REPEAT_DAYS)}일"
        if cfg
        else ""
    )
    ev = seg.evidence(out.basis, now, extra)
    return ev.model_copy(update={"queries": [*QUERIES, "sales_store 구매자별 첫·마지막 주문"]})

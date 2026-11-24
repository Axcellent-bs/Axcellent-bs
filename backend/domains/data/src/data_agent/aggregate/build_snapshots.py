"""기간 스냅샷: 구매자 수·코호트·떠날 위험 단골 (설계 2.2·3.3~3.5, 3-1a 뒷부분 1).

서로 다른 구매자 수는 날짜끼리 더할 수 없어서(같은 사람을 두 번 센다), 정해진 기간마다 기준일
(어제, 한국) 24시까지로 미리 센다. 기간: 최근 1·7·30·90·365일, 이번 달(1일~기준일), 지난달.

- 숫자는 지금 분석 함수를 그 기간으로 그대로 불러 얻는다(`sales_sql.tally`,
  `SalesStore.segment_session`의 `buyer_cells`·`item_counts`·`cohort_counts`·`regular_counts`·
  `regular_genres`). 그래서 같은 기간이면 지금 화면 숫자와 같다.
- 사람 수 칸은 **저장할 때** 카탈로그 `[sales] min_group_size`(k)로 숨긴다(설계 0.1).
  숨김 규칙은 지금 화면 그대로다.
  - 속성 값 칸: `segment_analysis.profile_hidden` · 떠날 위험: `retention.hide`
  - 코호트: 첫 구매 고객 < k면 줄 전체, 0 < 재구매 < k면 그 칸
  - 전체 구매자와 새·재구매: 합이 공개되므로 `protect`
- 구매자 id는 세션 안(메모리·임시 표)에서만 쓰고 행에는 남기지 않는다.
- 함께 산 상품·속성 묶음 프로필·채널 가치(설계 3.6~3.8)는 다음 PR이다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ax_contracts.data_access.sales_aggregates import (
    AggPeriod,
    AtRiskRow,
    BuyerPeriodRow,
    CohortRow,
    HiddenCount,
)
from ax_contracts.schemas.data import CatalogSales
from data_agent.aggregate.build_day import day_window
from data_agent.sales_quality import KST
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore
from data_agent.skills import retention
from data_agent.skills import segment_analysis as seg

DAY_PERIODS: dict[AggPeriod, int] = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}
COHORT_MONTHS = 6  # retention 입력 cohorts 최대
UNKNOWN = ""  # 속성 값을 모르는 사람 칸 (attr_cell 값)
OTHER_PRODUCTS = ""  # k 미만 상품을 숫자 없이 한 줄로 (dim_code)


@dataclass(frozen=True)
class Window:
    period: AggPeriod
    start: datetime  # UTC
    end: datetime


def windows(as_of: date) -> list[Window]:
    """정해진 기간들. 끝은 기준일 다음날 0시(한국), 지난달은 이번 달 1일 0시."""
    end = day_window(as_of)[1]
    out = [Window(p, end - timedelta(days=n), end) for p, n in DAY_PERIODS.items()]
    first = datetime(as_of.year, as_of.month, 1, tzinfo=KST).astimezone(UTC)
    py, pm = retention.month_add(as_of.year, as_of.month, -1)
    prev = datetime(py, pm, 1, tzinfo=KST).astimezone(UTC)
    out += [Window("this_month", first, end), Window("last_month", prev, first)]
    return out


def shown(n: int, hidden: bool) -> HiddenCount:
    return HiddenCount(hidden=True) if hidden else HiddenCount(value=n)


def build_snapshots(  # noqa: PLR0913 - 저장소·회사·기준일·카탈로그·계산 번호·시각
    sales: SalesStore,
    tenant_id: str,
    as_of: date,
    cfg: CatalogSales,
    run_id: str,
    now: datetime,
    *,
    source_cursor: str | None = None,
) -> tuple[list[BuyerPeriodRow], list[CohortRow], list[AtRiskRow]]:
    """그 회사 기준일 `as_of`의 구매자·코호트·떠날 위험 스냅샷 행 (숨김 적용 뒤)."""
    common = {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "computed_at": now,
        "source_cursor": source_cursor,
    }
    k = cfg.min_group_size
    buyers: list[BuyerPeriodRow] = []
    for w in windows(as_of):
        buyers += _period_rows(sales, tenant_id, w, cfg, k, common, as_of)
    cohorts, at_risk = _retention_rows(sales, tenant_id, as_of, cfg, k, common)
    return buyers, cohorts, at_risk


# ---------------------------------------------------------------- 3.3 기간 구매자


def _period_rows(  # noqa: PLR0913
    sales: SalesStore,
    tenant_id: str,
    w: Window,
    cfg: CatalogSales,
    k: int,
    common: dict,
    as_of: date,
) -> list[BuyerPeriodRow]:
    base = {**common, "as_of": as_of, "period": w.period}
    none = HiddenCount()  # 이 줄에서 세지 않는 칸 (속성·항목 줄의 새·재구매)
    t, _ = tally(sales, tenant_id, w.start, w.end)
    new, rep = t.buyers - t.repeat_buyers, t.repeat_buyers
    all_hidden = 0 < t.buyers < k
    hidden = {i for i, v in enumerate((new, rep)) if 0 < v < k}
    hidden = {0, 1} if all_hidden else seg.protect([new, rep], hidden, [[0, 1]])
    rows = [BuyerPeriodRow(
        **base, dim_kind="all", buyers=shown(t.buyers, all_hidden),
        new_buyers=shown(new, 0 in hidden), repeat_buyers=shown(rep, 1 in hidden),
        new_revenue=t.new_revenue, repeat_revenue=t.repeat_revenue,
    )]  # fmt: skip
    keys = [a.key for a in cfg.attributes]
    with sales.segment_session(tenant_id, w.start, w.end, keys) as s:
        cells = s.buyer_cells()
        total = sum(cells.values())
        for i, attr in enumerate(cfg.attributes):
            values = list(attr.values)
            counts = [sum(n for c, n in cells.items() if c[i] == v) for v in values]
            unknown = total - sum(counts)
            hid, unk_hidden = seg.profile_hidden(counts, unknown, k)
            for j, v in enumerate(values):
                rows.append(BuyerPeriodRow(
                    **base, dim_kind="attr", attr_cell={attr.key: v},
                    buyers=shown(counts[j], j in hid), new_buyers=none, repeat_buyers=none,
                ))  # fmt: skip
            rows.append(BuyerPeriodRow(
                **base, dim_kind="attr", attr_cell={attr.key: UNKNOWN},
                buyers=shown(unknown, unk_hidden), new_buyers=none, repeat_buyers=none,
            ))  # fmt: skip
        if w.period == "365d":  # 인구 속성 조합 칸 (3-2 ⑥ 시장 대비 구매자 구성)
            rows += _coverage_rows(base, cells, keys, cfg)
        items = s.item_counts({})
        for kind, code, n, _ in items:  # 대상 × 속성 값 (3-2 ⑥ 고객 프로필)
            if n >= k:
                rows += _profile_rows(base, kind, code, n, s.item_cells(kind, code), cfg, k)
        products = [(code, n) for kind, code, n, _ in items if kind == "product" and n >= k]
        titles = s.titles(ids=[c for c, _ in products]) if products else {}
    for kind, code, n, _ in items:
        if kind == "product":
            continue
        rows.append(BuyerPeriodRow(
            **base, dim_kind=kind, dim_code=code, buyers=shown(n, n < k),
            new_buyers=none, repeat_buyers=none,
        ))  # fmt: skip
    for code, n in products:
        rows.append(BuyerPeriodRow(
            **base, dim_kind="product", dim_code=code, buyers=HiddenCount(value=n),
            new_buyers=none, repeat_buyers=none, title=titles.get(code),
        ))  # fmt: skip
    if any(kind == "product" and n < k for kind, _, n, _ in items):
        rows.append(BuyerPeriodRow(
            **base, dim_kind="product", dim_code=OTHER_PRODUCTS, buyers=HiddenCount(hidden=True),
            new_buyers=none, repeat_buyers=none,
        ))  # fmt: skip
    return rows


def _coverage_rows(
    base: dict, cells: dict, keys: list[str], cfg: CatalogSales
) -> list[BuyerPeriodRow]:
    """카탈로그 인구 조합마다 칸별 구매자(숨김은 화면 `coverage_buyers` 그대로)와 답한 묶음 합."""
    none = HiddenCount()
    out = []
    for pop in cfg.population:
        at = [keys.index(k) for k in pop.attribute_keys]
        counts: dict[tuple, int] = defaultdict(int)
        for cell, n in cells.items():
            counts[tuple(cell[i] for i in at)] += n
        combos, found, hidden, answered = seg.coverage_cells(pop, cfg, counts)
        key = seg.coverage_key(pop)
        for i, combo in enumerate(combos):
            out.append(BuyerPeriodRow(
                **base, dim_kind="attr_combo", dim_code=key,
                attr_cell=dict(zip(pop.attribute_keys, combo, strict=True)),
                buyers=shown(found[i], i in hidden), new_buyers=none, repeat_buyers=none,
            ))  # fmt: skip
        out.append(BuyerPeriodRow(
            **base, dim_kind="attr_combo", dim_code=f"{key}:{seg.ANSWERED}",
            buyers=HiddenCount(value=sum(found[i] for i in answered)), new_buyers=none,
            repeat_buyers=none,
        ))  # fmt: skip
    return out


def _profile_rows(  # noqa: PLR0913
    base: dict, kind: str, code: str, target_n: int, cells: dict, cfg: CatalogSales, k: int
) -> list[BuyerPeriodRow]:
    """대상(분류·장르·상품, 구매자 k 이상)을 산 사람의 속성 값별 수. 숨김은 화면(`profile`) 그대로
    `profile_hidden`(값 칸 < k, 모름 0 < n < k, 보완 숨김)."""
    none = HiddenCount()
    out = []
    for i, attr in enumerate(cfg.attributes):
        values = list(attr.values)
        counts = [sum(n for c, n in cells.items() if c[i] == v) for v in values]
        unknown = target_n - sum(counts)
        hid, unk_hidden = seg.profile_hidden(counts, unknown, k)
        for j, v in enumerate(values):
            out.append(BuyerPeriodRow(
                **base, dim_kind=kind, dim_code=code, attr_cell={attr.key: v},
                buyers=shown(counts[j], j in hid), new_buyers=none, repeat_buyers=none,
            ))  # fmt: skip
        out.append(BuyerPeriodRow(
            **base, dim_kind=kind, dim_code=code, attr_cell={attr.key: UNKNOWN},
            buyers=shown(unknown, unk_hidden), new_buyers=none, repeat_buyers=none,
        ))  # fmt: skip
    return out


# ---------------------------------------------------------------- 3.4 코호트 · 3.5 떠날 위험


def _retention_rows(  # noqa: PLR0913
    sales: SalesStore, tenant_id: str, as_of: date, cfg: CatalogSales, k: int, common: dict
) -> tuple[list[CohortRow], list[AtRiskRow]]:
    end = day_window(as_of)[1]  # retention의 now 자리 (기준일 24시)
    months = retention.cohort_months(COHORT_MONTHS, end)
    keys = [a.key for a in cfg.attributes]
    with sales.segment_session(tenant_id, retention.HISTORY_FROM, end, keys) as s:
        counts = s.cohort_counts(months[0].start, months[-1].end)
        regular = s.regular_counts(cfg.repeat_min_orders, end - timedelta(days=cfg.churn_days))
        genres = s.regular_genres(cfg.repeat_min_orders)
    base = {**common, "as_of": as_of}
    filters: list[dict[str, str]] = [{}]
    filters += [{a.key: v} for a in cfg.attributes for v in a.values]
    cohorts = [_cohort_row(base, m, f, counts, keys, k, end) for f in filters for m in months]
    cells = retention.combos(cfg, {})
    table = {cell: (n, risk) for cell, n, risk in regular}
    reg = [table.get(c, (0, 0))[0] for c in cells]
    risk = [table.get(c, (0, 0))[1] for c in cells]
    hidden = retention.hide(reg, risk, k)
    at_risk = [
        AtRiskRow(
            **base, attr_cell=dict(zip(keys, cell, strict=True)),
            regulars=shown(reg[i], i in hidden), at_risk=shown(risk[i], i in hidden),
            top_genre=None if i in hidden else retention.top_genre(cell, genres, cfg),
            churn_days=cfg.churn_days, repeat_min_orders=cfg.repeat_min_orders,
        )
        for i, cell in enumerate(cells)
    ]  # fmt: skip
    return cohorts, at_risk


def _cohort_row(  # noqa: PLR0913
    base: dict,
    m: retention.Month,
    filters: dict[str, str],
    counts: list[tuple[str, tuple, int, int, int, int]],
    keys: list[str],
    k: int,
    now: datetime,
) -> CohortRow:
    sums = defaultdict(int)
    for month, cell, n, r30, r60, r90 in counts:
        if month == m.label and retention.matches(cell, keys, filters):
            for name, v in (("n", n), ("30", r30), ("60", r60), ("90", r90)):
                sums[name] += v
    n = sums["n"]
    repeat: dict = {}
    for days in retention.REPEAT_DAYS:
        if m.end + timedelta(days=days) > now:
            repeat[str(days)] = None  # 아직 다 안 지남
        else:
            r = sums[str(days)]
            repeat[str(days)] = shown(r, n < k or 0 < r < k)
    return CohortRow(
        **base, cohort_month=m.label, attr_cell=filters, first_buyers=shown(n, n < k),
        repeat=repeat,
    )  # fmt: skip

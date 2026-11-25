"""기간 스냅샷 2: 함께 산 상품·속성 묶음 즐겨 사는 것·채널 가치 (설계 3.6~3.8, 3-1a 뒷부분 2).

#1695(`build_snapshots`)와 같은 방식이다.
- 숫자는 지금 skill의 순수 함수에 그 기간의 세션 숫자를 넣어 얻는다
  (함께 산 상품 `basket_pairs.ranked_pairs`·`hidden_pairs`, 즐겨 사는 것 `segment_analysis.ranked`,
  채널 가치 `channel_value.source_label` 묶기와 `protect`). 같은 기간이면 지금 화면 숫자와 같다.
- 사람 수 칸은 **저장할 때** 카탈로그 `[sales] min_group_size`(k)로 지금 화면 규칙 그대로 숨긴다.
- 구매자 id는 세션 안에서만 쓰고 행에는 남기지 않는다.
- 묶음(attr_cell): 함께 산 상품·즐겨 사는 것은 "조건 없음 + 속성 값 하나씩"과 즐겨 사는 것은
  속성 두 개 조합까지(이벤트 제안이 1~2개 속성 묶음을 쓴다). 기간은 1·7·30·90·365일.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime

from ax_contracts.data_access.sales_aggregates import (
    ChannelValueRow,
    HiddenCount,
    PairRow,
    SegmentProfileRow,
)
from ax_contracts.schemas.data import CatalogSales
from data_agent.aggregate.build_snapshots import DAY_PERIODS, Window, shown, windows
from data_agent.sales_store import SalesStore
from data_agent.skills import basket_pairs, channel_value, retention
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import NO_PRODUCT, category_label

MAX_PAIRS = 20  # 화면 입력 max_pairs 상한
TOP_ITEMS = 20  # 즐겨 사는 것 입력 top 상한
GROUP = ""  # 묶음 크기 < k라 숨긴 묶음 한 줄 (item_code)


def day_windows(as_of: date) -> list[Window]:
    return [w for w in windows(as_of) if w.period in DAY_PERIODS]


def attr_filters(cfg: CatalogSales, *, pairs_of_two: bool) -> list[dict[str, str]]:
    """조건 없음 + 속성 값 하나씩 (+ 속성 두 개 값 조합). 카탈로그 순서."""
    out: list[dict[str, str]] = [{}]
    out += [{a.key: v} for a in cfg.attributes for v in a.values]
    if pairs_of_two:
        for x, y in itertools.combinations(cfg.attributes, 2):
            out += [{x.key: vx, y.key: vy} for vx in x.values for vy in y.values]
    return out


def build_more(  # noqa: PLR0913 - 저장소·회사·기준일·카탈로그·계산 번호·시각
    sales: SalesStore,
    tenant_id: str,
    as_of: date,
    cfg: CatalogSales,
    run_id: str,
    now: datetime,
    *,
    source_cursor: str | None = None,
) -> tuple[list[PairRow], list[SegmentProfileRow], list[ChannelValueRow]]:
    common = {"tenant_id": tenant_id, "run_id": run_id, "computed_at": now,
              "source_cursor": source_cursor, "as_of": as_of}  # fmt: skip
    keys = [a.key for a in cfg.attributes]
    pairs: list[PairRow] = []
    profiles: list[SegmentProfileRow] = []
    channels: list[ChannelValueRow] = []
    for w in day_windows(as_of):
        base = {**common, "period": w.period}
        with sales.segment_session(tenant_id, w.start, w.end, keys) as s:
            cells = s.buyer_cells()
            for f in attr_filters(cfg, pairs_of_two=False):
                for level in ("genre", "product"):
                    pairs += _pairs(s, cells, keys, f, level, cfg, base)
            for f in attr_filters(cfg, pairs_of_two=True)[1:]:  # 즐겨 사는 것은 조건이 있어야 한다
                profiles += _favorites(s, cells, keys, f, cfg, base)
        channels += _channels(sales, tenant_id, w, cfg, base)
    return pairs, profiles, channels


# ---------------------------------------------------------------- 3.6 함께 산 상품


def _pairs(  # noqa: PLR0913
    s,
    cells: dict,
    keys: list[str],
    f: dict[str, str],
    level: str,
    cfg: CatalogSales,
    base: dict,  # noqa: ANN001
) -> list[PairRow]:
    k = cfg.min_group_size
    overall = seg.cell_count(cells, keys, f) if f else sum(cells.values())
    if overall < k:
        return []  # 화면도 쌍을 내지 않는다
    items, together = s.pair_counts(level, f, k)
    known: Callable[[str], bool] = (
        (lambda c: c in cfg.genres) if level == "genre" else (lambda c: True)
    )
    top = basket_pairs.ranked_pairs(items, together, overall, known, MAX_PAIRS)
    slots = 0
    if len(top) < MAX_PAIRS:
        hidden = [p for p in s.hidden_pairs(level, f, k, MAX_PAIRS * 4 + 20)
                  if known(p[0]) and known(p[1])]  # fmt: skip
        slots = min(len(hidden), MAX_PAIRS - len(top))
    if not top and slots:
        # 보이는 쌍이 없고 숨긴 자리만 있으면 code 없는 표시 줄 하나에 자리 수를 남긴다
        # (줄이 없으면 화면이 숨긴 자리를 못 채워 원본과 달라진다, 11/25 지난 날짜 비교)
        return [
            PairRow(
                **base,
                level=level,
                attr_cell=f,
                code_a="",
                code_b="",
                together_buyers=0,
                a_buyers=0,
                b_buyers=0,
                overall_buyers=overall,
                hidden_slots=slots,
            )
        ]
    return [
        PairRow(**base, level=level, attr_cell=f, code_a=a, code_b=b, together_buyers=n,
                a_buyers=items[a], b_buyers=items[b], overall_buyers=overall, hidden_slots=slots)
        for a, b, n, _ in top
    ]  # fmt: skip


# ---------------------------------------------------------------- 3.7 속성 묶음 즐겨 사는 것


def _favorites(  # noqa: PLR0913
    s,
    cells: dict,
    keys: list[str],
    f: dict[str, str],
    cfg: CatalogSales,
    base: dict,  # noqa: ANN001
) -> list[SegmentProfileRow]:
    k = cfg.min_group_size
    overall_n = sum(cells.values())
    segment_n = seg.cell_count(cells, keys, f)
    if segment_n < k:  # 묶음 크기 < k면 묶음 한 줄만 숨겨 둔다
        return [SegmentProfileRow(**base, attr_cell=f, item_level="category", item_code=GROUP,
                                  buyers=HiddenCount(hidden=True), item_buyers=0)]  # fmt: skip
    bought = s.item_counts(f)
    totals = {(kind, code): total for kind, code, total, _ in bought}
    # 묶음 크기 한 줄 (3-2 ⑥: 읽을 때 비율 분모. item_buyers = 그 기간 전체 구매자)
    size = SegmentProfileRow(
        **base, attr_cell=f, item_level="category", item_code=GROUP,
        buyers=HiddenCount(value=segment_n), item_buyers=overall_n,
    )  # fmt: skip
    titles = s.titles(ids=seg.products_to_name(bought, segment_n, overall_n, k, TOP_ITEMS))
    names: dict[str, Callable[[str], str | None]] = {
        "genre": cfg.genres.get,
        "category": lambda c: category_label(cfg, c),
        "product": lambda c: titles.get(c, NO_PRODUCT),
    }
    out: list[SegmentProfileRow] = [size]
    for kind in ("genre", "category", "product"):
        for item in seg.ranked(kind, bought, segment_n, overall_n, names[kind], k, TOP_ITEMS):
            out.append(SegmentProfileRow(
                **base, attr_cell=f, item_level=kind, item_code=item.code,
                buyers=shown(item.buyers or 0, item.suppressed),
                item_buyers=totals.get((kind, item.code), 0),
                title=item.label if kind == "product" else None,
            ))  # fmt: skip
    return out


# ---------------------------------------------------------------- 3.8 채널 가치


def _channels(
    sales: SalesStore, tenant_id: str, w: Window, cfg: CatalogSales, base: dict
) -> list[ChannelValueRow]:
    """첫 구매가 기간 안인 새 고객을 첫 유입 경로 이름(직접 유입·기타 경로 묶은 뒤)별로.
    첫 구매 + value_horizon_days가 기준일 24시보다 뒤인 사람은 빼고 그 수를 남긴다
    (지금 화면 그대로)."""
    with sales.segment_session(tenant_id, retention.HISTORY_FROM, w.end, []) as s:
        rows, refunds, excluded = s.channel_value(w.start, w.end, cfg.value_horizon_days, w.end)
    grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for src, n, net, orders, rep in rows:
        g = grouped[channel_value.source_label(src, cfg)]
        for i, v in enumerate((n, net - refunds.get(src, 0), orders, rep)):
            g[i] += v
    labels = sorted(grouped)
    counts = [grouped[x][0] for x in labels]
    k = cfg.min_group_size
    hidden = seg.protect(counts, {i for i, n in enumerate(counts) if n < k},
                         [list(range(len(labels)))])  # fmt: skip
    out = []
    for i, label in enumerate(labels):
        n, net, orders, rep = grouped[label]
        h = i in hidden
        out.append(ChannelValueRow(
            **base, source_label=label, new_buyers=shown(n, h),
            horizon_net=None if h else net, horizon_orders=None if h else orders,
            repeaters=None if h else rep, excluded_incomplete=excluded,
            horizon_days=cfg.value_horizon_days,
        ))  # fmt: skip
    return out

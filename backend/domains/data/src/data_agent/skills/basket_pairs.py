"""함께 사는 장르·상품 (`basket_pairs`, 11/17, grade read).

표준 판매 저장소만 읽고 LLM을 쓰지 않는다. 규칙은 고객·시장 분석(`segment_analysis`)과 같다:
기간은 한국 시간 0시 기준 N일, 결제 완료 주문만, 품질 관문 + 고객 기록 관문(걸리면 주문 집계 전에
insufficient), 설정에 없는 속성 조건도 insufficient와 이유.

계산 (SQL, `SegmentSession.pair_counts`)
- 단위는 "같은 사람이 기간 안에 둘 다 산 것"이다(주문 단위 아님). 기간 안 결제 완료 주문 → 사람 ×
  장르(또는 상품) 구분 표 → 쌍마다 둘 다 산 사람 수
- 대분류(category)는 쌍에서 뺀다. level genre면 카탈로그 genres에 있는 장르끼리, product면 상품끼리
- `filters`가 있으면 그 속성 조건이 모두 맞는 사람 안에서만 센다(구매자 N도 그 사람들)
- 쌍의 a = 산 사람이 더 많은 쪽(같으면 code 순), b = 다른 쪽
  - confidence = 함께 산 사람 ÷ a 구매자 (a를 산 사람 중 b도 산 비율)
  - lift = 함께 산 사람 × 전체 구매자 ÷ (a 구매자 × b 구매자). 1보다 크면 따로 살 때 기대보다 자주
    같이 산다
- 개인정보: 함께 산 사람이 `min_group_size` 미만이면 `suppressed=True`이고 숫자 칸은 모두 None.
  조건에 맞는 구매자가 기준 미만이면 쌍을 내지 않는다
- 순서: 숨기지 않은 쌍은 lift 큰 순 → 함께 산 사람 많은 순 → code 순. 자리가 남으면 숨긴 쌍을 그
  뒤에 code 순으로. 최대 `max_pairs`개
- 성능: 쌍은 a마다 SQL로 세고 기준 이상만 돌려받는다. 숨긴 쌍은 자리가 남을 때만 따로 읽고,
  상품 이름은 내보낼 상품만 찾는다
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from ax_contracts.schemas.data import (
    BasketLevel,
    BasketPair,
    BasketPairsOutput,
    CatalogSales,
    SegmentPeriod,
)
from data_agent.aggregate import read_buyers as rb
from data_agent.aggregate.store import AggregateStore, cell_text
from data_agent.sales_store import SalesStore
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import NO_PRODUCT

GREEN_LIFT = 1.2  # 화면이 초록으로 보이는 배수 (이벤트 제안의 MIN_LIFT와 같은 값)


def level_noun(level: BasketLevel, cfg: CatalogSales | None) -> str:
    return "장르" if level == "genre" else (cfg.item_noun if cfg else "상품")


def direction(x: str, y: str, items: dict[str, int]) -> tuple[str, str]:
    """(a, b): a = 산 사람이 더 많은 쪽, 같으면 code 순."""
    a, b = sorted((x, y), key=lambda c: (-items[c], c))
    return a, b


def ranked_pairs(
    items: dict[str, int],
    together: dict[tuple[str, str], int],
    overall: int,
    known: Callable[[str], bool],
    limit: int,
) -> list[tuple[str, str, int, float | None]]:
    """(a, b, 함께 산 사람, 배수): 배수 큰 순 → 함께 산 사람 많은 순 → code 순, 최대 `limit`개.
    `together`는 기준(`min_group_size`) 이상인 쌍만, `known`이 False인 code는 뺀다."""
    rows: list[tuple[str, str, int, float | None]] = []
    for (x, y), n in together.items():
        if n <= 0 or not known(x) or not known(y):
            continue
        a, b = direction(x, y, items)
        lift = round(n * overall / (items[a] * items[b]), 4) if overall > 0 else None
        rows.append((a, b, n, lift))
    rows.sort(key=lambda r: (-(r[3] or 0), -r[2], r[0], r[1]))
    return rows[:limit]


def pair_rows(  # noqa: PLR0913 - 집계 숫자와 이름표
    items: dict[str, int],
    together: dict[tuple[str, str], int],
    overall: int,
    name: Callable[[str], str | None],
    limit: int,
    hidden: Sequence[tuple[str, str]] = (),
) -> list[BasketPair]:
    """집계 숫자 → 쌍 목록. 숨기지 않은 쌍(배수 순) 뒤에 숨긴 쌍(`hidden`, 숫자 없음)을 채워 최대
    `limit`개. 이름이 None인 code(카탈로그에 없는 장르)는 뺀다."""
    out: list[BasketPair] = []
    for a, b, n, lift in ranked_pairs(items, together, overall, lambda c: name(c) is not None,
                                      limit):  # fmt: skip
        out.append(BasketPair(
            a_code=a, a_label=name(a) or "", b_code=b, b_label=name(b) or "", together_buyers=n,
            a_buyers=items[a], b_buyers=items[b], confidence=seg.ratio(n, items[a]), lift=lift,
        ))  # fmt: skip
    for x, y in hidden:
        if len(out) >= limit:
            break
        if name(x) is None or name(y) is None:
            continue
        a, b = direction(x, y, items)
        out.append(BasketPair(a_code=a, a_label=name(a) or "", b_code=b, b_label=name(b) or "",
                              suppressed=True))  # fmt: skip
    return out


def basket(  # noqa: PLR0913 - 입력 칸을 그대로 받는다
    store: SalesStore,
    tenant_id: str,
    level: BasketLevel,
    period: SegmentPeriod,
    filters: dict[str, str],
    max_pairs: int,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> BasketPairsOutput:
    snap = seg.snapshot(store, tenant_id, period, cfg, catalog_id, now)
    common: dict[str, Any] = {
        "level": level,
        "period": period,
        "filters": dict(filters),
        "quality": snap.report,
        "basis": snap.basis,
    }
    problems = seg.filter_problems(filters, cfg) if cfg else []
    if cfg is not None and level == "genre" and not cfg.genres:
        problems.append("설정에 장르(genres)가 없음")
    reasons = problems + snap.reasons
    if reasons or cfg is None:
        out = BasketPairsOutput(**common, status="insufficient", insufficient_reasons=reasons)
        return out.model_copy(update={"summary": summary_of(out, cfg)})
    sales_cfg = cfg
    got = None if len(filters) > 1 else rb.from_snapshot(
        tenant_id, now,
        lambda agg, day: from_snapshot(agg, tenant_id, level, period, filters, max_pairs,
                                       sales_cfg, day),
    )  # fmt: skip
    if got is not None:
        pairs_out, total, as_of = got
        common["basis"] = rb.basis_at(snap.basis, rb.window_of(period, as_of))
        out = BasketPairsOutput(**common, pairs=pairs_out)
        return out.model_copy(update={"summary": rb.with_note(summary_of(out, cfg, total), as_of)})
    least = cfg.min_group_size
    keys = [a.key for a in cfg.attributes if a.key in filters]
    genres = cfg.genres
    with snap.session(keys) as s:
        cells = s.buyer_cells()
        overall = seg.cell_count(cells, keys, filters) if filters else sum(cells.values())
        items: dict[str, int] = {}
        together: dict[tuple[str, str], int] = {}
        hidden: list[tuple[str, str]] = []
        titles: dict[str, str] = {}
        if overall >= least:
            items, together = s.pair_counts(level, filters, least)
            known: Callable[[str], bool] = (
                (lambda c: c in genres) if level == "genre" else (lambda c: True)
            )
            top = ranked_pairs(items, together, overall, known, max_pairs)
            if len(top) < max_pairs:  # 숨긴 쌍은 자리가 남을 때만 (code 순)
                hidden = [
                    p
                    for p in s.hidden_pairs(level, filters, least, max_pairs * 4 + 20)
                    if known(p[0]) and known(p[1])
                ][: max_pairs - len(top)]
            if level == "product":
                titles = s.titles(ids={c for r in top for c in r[:2]} | {c for p in hidden
                                                                        for c in p})  # fmt: skip
    name: Callable[[str], str | None] = (
        genres.get if level == "genre" else (lambda c: titles.get(c, NO_PRODUCT))
    )
    pairs = pair_rows(items, together, overall, name, max_pairs, hidden)
    out = BasketPairsOutput(**common, pairs=pairs)
    return out.model_copy(update={"summary": summary_of(out, cfg, overall)})


def from_snapshot(  # noqa: PLR0913 - 입력 칸 그대로
    agg: AggregateStore,
    tenant_id: str,
    level: BasketLevel,
    period: SegmentPeriod,
    filters: dict[str, str],
    max_pairs: int,
    cfg: CatalogSales,
    as_of: date,
) -> tuple[list[BasketPair], int, date] | None:
    """어제 기준 함께 산 상품 스냅샷(상위 20쌍 + 숫자 없이 채운 자리 수)을 화면 쌍으로 (3-2 ⑤).

    - 조건은 없음·속성 하나만 저장돼 있다(부르는 쪽이 둘 이상이면 원본으로 간다)
    - 숨긴 쌍은 저장하지 않고 자리 수만 남기므로 code·이름 없이 `suppressed` 쌍으로 채운다
      (설계 3.6·6.2: 비교는 상위 순서와 수)
    - 상품 이름은 같은 기간 구매자 스냅샷 상품 줄의 이름(쌍에 든 상품은 그 기간 구매자가 k 이상)
    - 쌍이 없고 그 조건 구매자 수를 스냅샷에서 알 수 없으면(숨긴 칸) None(원본 경로)
    """
    cell = cell_text(filters)
    rows = [
        r for r in agg.more_rows("agg_pairs", tenant_id, as_of)
        if (r["period"], r["level"], r["attr_cell"]) == (period, level, cell)
    ]  # fmt: skip
    buyers = [r for r in agg.buyer_rows(tenant_id, as_of) if r["period"] == period]
    if rows:
        overall = rows[0]["overall_buyers"]
    else:
        kind = "attr" if filters else "all"
        found = [r for r in buyers if r["dim_kind"] == kind and r["attr_cell"] == cell]
        if not found or found[0]["buyers_hidden"]:
            if found and kind == "all":
                overall = 0  # 0 < 전체 < k: 화면도 "k명 미만이라 숨김"
                return [], overall, as_of
            return None
        overall = found[0]["buyers"] or 0
    ranked = sorted(
        rows,
        key=lambda r: (-_lift(r), -r["together_buyers"], r["code_a"], r["code_b"]),
    )[:max_pairs]
    titles = {r["dim_code"]: r["title"] for r in buyers
              if r["dim_kind"] == "product" and not r["attr_cell"]}  # fmt: skip
    name: Callable[[str], str | None] = (
        cfg.genres.get if level == "genre" else (lambda c: titles.get(c) or NO_PRODUCT)
    )
    out = [
        BasketPair(
            a_code=r["code_a"], a_label=name(r["code_a"]) or "", b_code=r["code_b"],
            b_label=name(r["code_b"]) or "", together_buyers=r["together_buyers"],
            a_buyers=r["a_buyers"], b_buyers=r["b_buyers"],
            confidence=seg.ratio(r["together_buyers"], r["a_buyers"]), lift=_lift(r) or None,
        )
        for r in ranked
    ]  # fmt: skip
    if rows and len(out) < max_pairs:
        slots = min(rows[0]["hidden_slots"], max_pairs - len(out))
        out += [BasketPair(a_code="", b_code="", suppressed=True) for _ in range(slots)]
    return out, overall, as_of


def _lift(r) -> float:  # noqa: ANN001 - sqlite 행
    overall = r["overall_buyers"]
    if overall <= 0:
        return 0.0
    return round(r["together_buyers"] * overall / (r["a_buyers"] * r["b_buyers"]), 4)


def summary_of(out: BasketPairsOutput, cfg: CatalogSales | None, overall: int = 0) -> str:
    label = seg.SEGMENT_LABEL[out.period]
    who = (seg.filter_label(out.filters, cfg) if cfg else "") or "전체"
    noun = level_noun(out.level, cfg)
    if out.status != "ok":
        text = f"{label} {who} 함께 사는 {noun} 숫자를 내지 않음 — " + "; ".join(
            out.insufficient_reasons
        )
        return seg.with_source(text, out.basis)
    least = cfg.min_group_size if cfg else 0
    if overall < least:
        return seg.with_source(f"{label} {who} 구매자가 {least}명 미만이라 숫자를 숨김.", out.basis)
    shown = [p for p in out.pairs if not p.suppressed]
    if not shown:
        text = f"{label} {who} 구매자 {overall:,}명 중 {least}명 이상이 함께 산 {noun} 쌍이 없음."
        return seg.with_source(text, out.basis)
    best = shown[0]
    text = (
        f"{label} {who} 구매자 {overall:,}명 기준, 가장 두드러지게 함께 사는 {noun}은 "
        f"{best.a_label} + {best.b_label} (따로 살 때 기대의 {best.lift or 0:.2f}배, "
        f"함께 산 {best.together_buyers:,}명, {best.a_label} 구매자 중 {seg.pct(best.confidence)})."
    )
    strong = sum(1 for p in shown if (p.lift or 0) >= GREEN_LIFT)
    text += f" {GREEN_LIFT}배 이상인 쌍 {strong}개."
    return seg.with_source(text, out.basis)

"""매출 분석 (`sales_analysis`): 무엇이 팔리고, 누가 사고, 어디서 들어오는지.

표준 판매 저장소(`sales_store`)만 읽는다. 판매 서버를 직접 읽지 않는다. LLM을 쓰지 않고 회사
상태를 바꾸지 않는다(grade read). 숫자는 모두 이 파일의 코드가 계산하고 금액은 정수 원이다.

숫자 정의 (카탈로그 `[sales] revenue_definition`과 같은 뜻)
- 매출 = 결제 완료 주문의 상품 금액 합 − 쿠폰 할인 − 포인트 사용 − 기간 안 환불 (배송비 제외).
  주문은 접수 시각, 환불은 환불 시각으로 기간을 정한다. 취소·결제 실패·결제 대기 주문은 빼고
  그 환불도 빼지 않는다(`sales_clean.counts_as_revenue`)
- 객단가 = 매출 ÷ 결제 완료 주문 수 (반올림)
- 재구매 고객 = 이 기간 시작 전에도 결제 완료 주문이 있는 buyer_id. 비율 = 재구매 고객 ÷ 기간 구매자
- 직전 기간 = 같은 길이로 하루 단위만큼 앞 (`sales_quality.window`)

나누기
- 주문의 할인(쿠폰 + 포인트)은 줄마다 받은 몫(`coupon_share`·`points_share`)이 합과 맞으면 그대로,
  안 맞으면 줄 금액 비율로 나눈다(나머지는 큰 몫부터 1원씩). 환불도 줄 순매출 비율로 나눈다.
  그래서 카테고리·상품 매출의 합이 전체 매출과 같다
- 상품 증감: 두 기간 모두 최소 표본(`min_product_orders`, 상품이 든 결제 완료 주문 수) 이상인
  상품만 비교한다. 아니면 "표본 부족"으로 센다
- 유입 경로: (src_source, src_medium)의 카탈로그 이름. 표시가 없으면 "직접 유입", 카탈로그에 없는
  출처는 "기타 경로"로 묶는다(영문 코드를 화면에 내지 않는다)
- 품질 관문(`sales_quality.gate`)에 걸리면 숫자 칸을 비우고 "데이터 부족: 이유"만 낸다
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CatalogSales,
    SalesAnalysisOutput,
    SalesBasis,
    SalesCategoryRow,
    SalesChannelRow,
    SalesCustomerMix,
    SalesKpi,
    SalesPeriod,
    SalesProductMove,
)
from data_agent.aggregate.read import read_tally
from data_agent.sales_quality import KST, PERIOD_LABEL, Window, gate, quality_counted, window
from data_agent.sales_sql import LineNet, Tally, allocate, line_nets, product_names, tally
from data_agent.sales_store import FAKE_SOURCE, SalesStore

FAKE_DATA = "가짜 데이터(데모용)"  # 저장소를 가짜 출처(`sales_fake`)가 채웠을 때 근거·요약에 붙인다
TOP_PRODUCTS = 5
NO_CATEGORY = "이름 없는 분류"


def category_label(cfg: CatalogSales | None, code: str) -> str:
    """분류 이름.

    카탈로그에 분류 표가 없는 회사(사이트 변환기로 연결한 회사, 11/25)는 사이트가 준 분류 값
    그대로(예: 누비투어 지역 JP, 스터디온 과목 DEV). 표가 있으면 표에 없는 값은 이름 없는 분류.
    """
    known = cfg.categories if cfg else {}
    if not known:
        return code or NO_CATEGORY
    return known.get(code, NO_CATEGORY)


NO_PRODUCT = "이름 없는 상품"
DIRECT = "직접 유입"
OTHER_SOURCE = "기타 경로"
QUERIES = ["sales_store.sales_orders", "sales_store.sales_order_lines", "sales_store.sales_refunds"]


# 나누기(`allocate`·`line_nets`)와 한 기간 집계(`Tally`·`tally`)는 `sales_sql`에 있다
# (11/18, SQL 집계).
# 예전 이름 그대로 여기서도 쓸 수 있게 다시 내보낸다
__all__ = ["LineNet", "Tally", "allocate", "line_nets", "tally"]


# ---------------------------------------------------------------- 출력 만들기


def change(value: float | None, previous: float | None) -> float | None:
    """변화율. 직전이 0 이하이거나 없으면 None."""
    if value is None or previous is None or previous <= 0:
        return None
    return round((value - previous) / previous, 4)


def share(part: int, total: int) -> float | None:
    if total <= 0 or part < 0 or part > total:
        return None
    return round(part / total, 4)


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole > 0 else None


def kpis(cur: Tally, prev: Tally) -> list[SalesKpi]:
    aov = round(cur.revenue / cur.orders) if cur.orders else None
    prev_aov = round(prev.revenue / prev.orders) if prev.orders else None
    repeat = rate(cur.repeat_buyers, cur.buyers)
    prev_repeat = rate(prev.repeat_buyers, prev.buyers)
    repeat_change = (
        round(repeat - prev_repeat, 4) if repeat is not None and prev_repeat is not None else None
    )
    return [
        SalesKpi(key="revenue", label="매출", unit="won", value=cur.revenue,
                 previous=prev.revenue, change=change(cur.revenue, prev.revenue)),
        SalesKpi(key="order_count", label="주문 수", unit="count", value=cur.orders,
                 previous=prev.orders, change=change(cur.orders, prev.orders)),
        SalesKpi(key="aov", label="객단가", unit="won", value=aov, previous=prev_aov,
                 change=change(aov, prev_aov)),
        SalesKpi(key="repeat_rate", label="재구매 고객 비율", unit="rate", value=repeat,
                 previous=prev_repeat, change=repeat_change, change_kind="points"),
    ]  # fmt: skip


def categories(cur: Tally, prev: Tally, cfg: CatalogSales) -> list[SalesCategoryRow]:
    codes = set(cur.by_category) | set(prev.by_category)
    rows = [
        SalesCategoryRow(
            code=code,
            label=category_label(cfg, code),
            revenue=cur.by_category.get(code, 0),
            share=share(cur.by_category.get(code, 0), cur.revenue),
            previous_revenue=prev.by_category.get(code, 0),
            change=change(cur.by_category.get(code, 0), prev.by_category.get(code, 0)),
        )
        for code in codes
    ]
    return sorted(rows, key=lambda r: (-r.revenue, r.label, r.code))


def _top_ties(changes: list[tuple[float, str]], ascending: bool) -> list[str]:
    """변화율 순 상위 `TOP_PRODUCTS`개와, 그 경계와 같은 변화율인 상품 (이름 순 동점 처리용)."""
    ordered = sorted(changes, key=lambda x: x[0] if ascending else -x[0])
    if len(ordered) <= TOP_PRODUCTS:
        return [pid for _, pid in ordered]
    edge = ordered[TOP_PRODUCTS - 1][0]
    return [pid for c, pid in ordered if (c <= edge if ascending else c >= edge)]


def products(
    cur: Tally,
    prev: Tally,
    cfg: CatalogSales,
    names: Callable[[set[str]], dict[str, tuple[str | None, str]]] | None = None,
) -> tuple[list[SalesProductMove], list[SalesProductMove], int]:
    """많이 늘어난·줄어든 상품. 이름·분류는 `names`(필요한 상품만 찾기,
    `sales_sql.product_names`)로, 없으면 집계에 담긴 `product_meta`로. 순위는 변화율, 같으면
    이름 순 (예전과 같다)."""
    ids = set(cur.by_product) | set(prev.by_product)
    least = cfg.min_product_orders
    low = 0
    rated: list[tuple[float, str]] = []
    numbers: dict[str, tuple[int, int, float | None]] = {}
    for pid in ids:
        orders, prev_orders = cur.product_orders.get(pid, 0), prev.product_orders.get(pid, 0)
        if orders < least or prev_orders < least:
            low += 1
            continue
        moved = change(cur.by_product.get(pid, 0), prev.by_product.get(pid, 0))
        numbers[pid] = (orders, prev_orders, moved)
        if moved is not None:
            rated.append((moved, pid))
    up_ids = _top_ties([x for x in rated if x[0] > 0], ascending=False)
    down_ids = _top_ties([x for x in rated if x[0] < 0], ascending=True)
    wanted = set(up_ids) | set(down_ids)
    if names is None:
        found = {p: cur.product_meta.get(p) or prev.product_meta.get(p) for p in wanted}
    else:
        found = dict(names(wanted))

    def move(pid: str) -> SalesProductMove:
        title, code = found.get(pid) or (None, "")
        orders, prev_orders, moved = numbers[pid]
        return SalesProductMove(
            product_id=pid,
            label=title or NO_PRODUCT,
            category_label=category_label(cfg, code),
            revenue=cur.by_product.get(pid, 0),
            previous_revenue=prev.by_product.get(pid, 0),
            change=moved,
            orders=orders,
            previous_orders=prev_orders,
        )

    up = sorted((move(p) for p in up_ids), key=lambda m: (-(m.change or 0), m.label))
    down = sorted((move(p) for p in down_ids), key=lambda m: (m.change or 0, m.label))
    return up[:TOP_PRODUCTS], down[:TOP_PRODUCTS], low


def channel_label(source: str | None, medium: str | None, cfg: CatalogSales) -> str:
    if not source and not medium:
        return DIRECT
    name = cfg.source_labels.get(source or "")
    if name is None:
        return OTHER_SOURCE
    how = cfg.medium_labels.get(medium or "")
    return f"{name} · {how}" if how else name


def channels(cur: Tally, cfg: CatalogSales) -> list[SalesChannelRow]:
    grouped: dict[str, list[tuple[str | None, str | None]]] = defaultdict(list)
    for key in set(cur.by_channel) | set(cur.channel_orders):
        grouped[channel_label(*key, cfg)].append(key)
    rows = []
    for label, keys in grouped.items():
        revenue = sum(cur.by_channel.get(k, 0) for k in keys)
        source, medium = keys[0] if len(keys) == 1 and label != OTHER_SOURCE else (None, None)
        rows.append(
            SalesChannelRow(
                source=source,
                medium=medium,
                label=label,
                revenue=revenue,
                orders=sum(cur.channel_orders.get(k, 0) for k in keys),
                share=share(revenue, cur.revenue),
            )
        )
    return sorted(rows, key=lambda r: (-r.revenue, r.label))


def won(value: int | float | None) -> str:
    return "확인할 수 없음" if value is None else f"{round(value):,}원"


def pct(value: float | None, points: bool = False) -> str:
    if value is None:
        return "비교 없음"
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}{'%p' if points else '%'}"


def summary(out: SalesAnalysisOutput) -> str:
    """요약 한 문장 (코드 템플릿, 숫자는 출력값만). 가짜 출처면 앞에 표시를 붙인다."""
    text = _summary(out)
    return f"[{out.basis.data_source}] {text}" if out.basis.data_source else text


def _summary(out: SalesAnalysisOutput) -> str:
    label = PERIOD_LABEL[out.period]
    if out.status != "ok":
        return f"{label} 매출 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
    k = {x.key: x for x in out.kpis}
    text = (
        f"{label} 매출 {won(k['revenue'].value)} (직전 대비 {pct(k['revenue'].change)}), "
        f"결제 완료 주문 {k['order_count'].value}건, 객단가 {won(k['aov'].value)}."
    )
    if out.categories and out.categories[0].share is not None:
        top = out.categories[0]
        text += f" 가장 많이 팔린 분류는 {top.label} (매출 비중 {top.share * 100:.1f}%)."
    return text


def _kst(moment: datetime) -> str:
    local = moment.astimezone(KST)
    return f"{local:%Y-%m-%d %H:%M}"


def analyze(
    store: SalesStore,
    tenant_id: str,
    period: SalesPeriod,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> SalesAnalysisOutput:
    w: Window = window(period, now)
    # 다 지난 날은 일별 집계, 덜 끝난 날만 원본 (`DATA_SALES_READ=aggregate`일 때, 3-2 ①)
    cur, created = read_tally(store, tenant_id, w.start, w.end)
    prev, _ = read_tally(store, tenant_id, w.previous_start, w.previous_end)
    report = quality_counted(store, tenant_id, w, created, cfg)
    fake = store.sync_state(tenant_id).source == FAKE_SOURCE
    basis = SalesBasis(
        period_from=w.start,
        period_to=w.end,
        previous_from=w.previous_start,
        previous_to=w.previous_end,
        rows_read=cur.rows + prev.rows,
        last_synced_at=report.last_synced_at,
        catalog_id=catalog_id,
        catalog_version=cfg.version if cfg else None,
        data_source=FAKE_DATA if fake else None,
    )
    reasons = gate(report, cur.orders, cfg, now)
    common = {
        "period": period,
        "as_of": w.end,
        "definition": cfg.revenue_definition if cfg else "",
        "quality": report,
        "basis": basis,
    }
    if reasons or cfg is None:
        out = SalesAnalysisOutput(**common, status="insufficient", insufficient_reasons=reasons)
    else:
        up, down, low = products(
            cur, prev, cfg, names=lambda pids: product_names(store, tenant_id, cur, pids)
        )
        out = SalesAnalysisOutput(
            **common,
            kpis=kpis(cur, prev),
            categories=categories(cur, prev, cfg),
            products_up=up,
            products_down=down,
            products_low_sample=low,
            min_product_orders=cfg.min_product_orders,
            customers=SalesCustomerMix(
                buyers=cur.buyers,
                repeat_buyers=cur.repeat_buyers,
                new_revenue=cur.new_revenue,
                repeat_revenue=cur.repeat_revenue,
                new_share=share(cur.new_revenue, cur.revenue),
                repeat_share=share(cur.repeat_revenue, cur.revenue),
                coupon_orders=cur.coupon_orders,
                coupon_order_rate=rate(cur.coupon_orders, cur.orders),
            ),
            channels=channels(cur, cfg),
        )
    return out.model_copy(update={"summary": summary(out)})


def evidence(out: SalesAnalysisOutput, now: datetime) -> Evidence:
    b = out.basis
    synced = _kst(b.last_synced_at) if b.last_synced_at else "없음"
    return Evidence(
        queries=list(QUERIES),
        data_summary=(
            f"기간 {_kst(b.period_from)}~{_kst(b.period_to)} (한국 시간), 비교 "
            f"{_kst(b.previous_from)}~{_kst(b.previous_to)}, 읽은 행 {b.rows_read}, "
            f"마지막 수집 {synced}, 카탈로그 {b.catalog_id} 판 {b.catalog_version or '없음'}"
            + (f", 출처 {b.data_source}" if b.data_source else "")
        ),
        collected_at=now,
        ref_ids=[],
    )

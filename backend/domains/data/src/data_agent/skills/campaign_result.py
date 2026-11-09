"""campaign_result: 성과 결과 1건 (campaign_id 기준). 읽기 전용(grade read), LLM 안 씀.

① 연결(insight_id·proposal_id·campaign_id·측정 기간·schema_version), ② 영상 성과(video_performance
그대로), ③ 매출 효과(campaign_effect 계산 그대로 + 판정), ④ 성과 요인·개선 방향·한계 문구,
⑤ 알림(loss·data_gap). `view`로 부서별 묶음을 고른다 (기획 ①~⑤, 마케팅 ①④ 개선 방향만,
재무 ①③⑤). skill은 하나다.

- 매출 판정 (계산은 campaign_effect 그대로, 판정만 붙인다): 판매액 증분이 기준선 날들의 흔들림
  범위(구간 − 가장 많은 날 ~ 구간 − 가장 적은 날) 안이면 "효과 확인 안 됨", 위면 "더 팔림",
  아래면 "덜 팔림" + 알림 loss
- 표본 적음(`low_sample`): 기준선 구간이 `LOW_SAMPLE_WINDOWS`(4)개 미만이거나, 캠페인 주문과
  기준선 평균 주문이 둘 다 `LOW_SAMPLE_ORDERS`(5)건 미만. 그러면 더·덜 팔림을 단정하지 않고
  "효과 확인 안 됨(표본 적음)" (알림 loss도 내지 않음)
- 마이너스는 그대로 둔다 (0으로 바꾸거나 빼지 않음). 네 칸(귀속·상품·쿠폰·전체 매장)은 같은 주문이
  겹쳐 따로 계산·표시하고 더하지 않는다
- 영상에 프로모션 링크·쿠폰(promo_id·coupon_id)이 없으면 그 영상의 매출은 측정 불가.
  캠페인 카드에 "영상 N개 중 매출 측정 가능 M개"
- 측정 기간: 캠페인 영상(시험 영상 제외, 없으면 전부)의 첫 게시 ~ 7일 뒤 (아직이면 지금)
- 만들지 않는 것: 조회와 매출을 합친 점수, 수익·ROI(재무 몫), 캠페인 자동 중단
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CampaignAlert,
    CampaignEffectInput,
    CampaignEffectOutput,
    CampaignFinanceRecord,
    CampaignResultOutput,
    CampaignResultRequest,
    CampaignResultSection,
    CampaignResultView,
    CampaignSalesCell,
    CampaignSalesEffect,
    CampaignSalesVerdict,
    CampaignVideoAdvice,
    ShortsStatsOutput,
    VideoPerformanceOutput,
)

SCHEMA_VERSION = "campaign_result.v2"  # v2: finance_record(재무 묶음)·send_status 칸
LOW_SAMPLE_WINDOWS = 4  # 4주 기준선 (7·14·21·28일 창)
LOW_SAMPLE_ORDERS = 5
CAMPAIGN_DAYS = 7
LABELS = {"more": "더 팔림", "less": "덜 팔림", "no_effect": "효과 확인 안 됨"}
LOW_LABEL = "효과 확인 안 됨(표본 적음)"
UNMEASURABLE = "측정 불가: 프로모션 링크/쿠폰 없음"
LOSS_NOTE = "기획·재무 확인 필요 · 캠페인을 멈출지는 사람이 정합니다"
DATA_GAP = "놓침: 그 시점에 재지 못함 (데이터 끊김)"
SALES_LIMITS = [
    "네 칸은 겹치는 매출이라 더하지 않습니다. 표본이 적으면 '효과 확인 안 됨(표본 적음)'으로"
    " 표시합니다.",
    "수익·손실(광고비·제작비 포함)은 재무에서 판단합니다.",
]
SECTIONS: dict[CampaignResultView, list[CampaignResultSection]] = {
    "planning": ["link", "videos", "sales", "advice", "alerts"],
    "marketing": ["link", "advice"],
    "finance": ["link", "sales", "alerts"],
}
CELL_LABELS = {
    "attribution": "귀속 (쿠폰·링크로 온 주문)",
    "products": "상품 (캠페인 대상 책)",
    "coupons": "쿠폰",
    "store": "전체 매장",
}


def won(v: float) -> str:
    return f"{round(v):+,}원"


def count(v: float) -> str:
    return f"{round(v):+,}건"


def _int(v: float | None) -> int | None:
    return None if v is None else round(v)


# ---------------------------------------------------------------- 판정·네 칸 (평가 채점기가 부른다)


def verdict_of(effect: CampaignEffectOutput) -> CampaignSalesVerdict:
    """판매액 증분 vs 기준선 흔들림 범위. 표본이 적으면 단정하지 않는다. 숫자는 그대로 옮긴다."""
    s = effect.sales
    if s is None or s.status != "known":
        why = (s.unknown_reason if s else None) or "판매액을 받지 못함"
        return CampaignSalesVerdict(
            status="unknown",
            label=f"측정 불가: {why}",
            reason=why,
            incremental_orders=effect.incremental_orders,
            baseline_days=s.baseline_days if s else 0,
            baseline_rule=s.baseline_rule if s else None,
        )
    raw = {"up": "more", "down": "less"}.get(s.direction or "", "no_effect")
    small = []
    if s.baseline_days < LOW_SAMPLE_WINDOWS:
        small.append(f"기준선 {s.baseline_days}구간 ({LOW_SAMPLE_WINDOWS}구간 미만)")
    period = s.period.orders if s.period else 0
    base = s.baseline.orders if s.baseline else 0
    if period < LOW_SAMPLE_ORDERS and base < LOW_SAMPLE_ORDERS:
        small.append(f"주문 {period}건 · 기준선 평균 {base}건 (둘 다 {LOW_SAMPLE_ORDERS}건 미만)")
    low = bool(small)
    verdict = "no_effect" if low else raw
    return CampaignSalesVerdict(
        status="known",
        verdict=verdict,
        label=LOW_LABEL if low else LABELS[verdict],
        low_sample=low,
        low_sample_reason="; ".join(small) or None,
        reason=s.direction_reason,
        incremental_gross_krw=s.incremental_gross_krw,
        incremental_low_krw=s.incremental_low_krw,
        incremental_high_krw=s.incremental_high_krw,
        incremental_orders=effect.incremental_orders,
        baseline_days=s.baseline_days,
        baseline_rule=s.baseline_rule,
    )


def cells_of(effect: CampaignEffectOutput) -> list[CampaignSalesCell]:
    """네 칸 (순서 고정, 더하지 않음). 값은 campaign_effect 그대로, 마이너스도 그대로."""
    s, a, p, c = effect.sales, effect.attribution_effect, effect.products_effect, effect.coupons
    # 귀속: 링크(유입 표시)로 온 주문 + 캠페인 쿠폰을 쓴 주문 (둘도 겹칠 수 있어 따로 둔다)
    att = CampaignSalesCell(key="attribution", label=CELL_LABELS["attribution"])
    parts, whys = [], []
    if a is not None and a.status == "known":
        att.orders, att.gross_krw = a.orders, a.gross_krw
        parts.append(f"링크 {a.orders}건 · {a.gross_krw:,}원")
    else:
        whys.append(f"링크: {a.unknown_reason if a else '유입 기록을 보지 않음'}")
    if s is not None and s.direct_attributed_krw is not None:
        att.coupon_orders, att.coupon_gross_krw = s.direct_orders, s.direct_attributed_krw
        parts.append(f"쿠폰 {s.direct_orders or 0}건 · {s.direct_attributed_krw:,}원")
    else:
        whys.append(f"쿠폰: {(s.direct_reason if s else None) or '쿠폰 귀속을 모름'}")
    att.status = "known" if parts else "unknown"
    att.line = " / ".join(parts) or None
    att.reason = "; ".join(whys) if not parts else None
    # 상품: 관련 상품 판매액 증분 (범위)
    prod = CampaignSalesCell(key="products", label=CELL_LABELS["products"])
    if p is None:
        prod.reason = "관련 상품을 받지 않음 (인사이트에 연결 상품 없음)"
    elif p.status != "known" or p.gross_krw is None:
        prod.reason = p.unknown_reason or "상품 기준을 모름"
    else:
        g = p.gross_krw
        prod.status, prod.direction = "known", g.direction
        prod.incremental_gross_krw = _int(g.incremental)
        prod.incremental_low_krw, prod.incremental_high_krw = (
            _int(g.incremental_low),
            _int(g.incremental_high),
        )
        if prod.incremental_gross_krw is not None:
            prod.line = won(prod.incremental_gross_krw)
            if prod.incremental_low_krw is not None and prod.incremental_high_krw is not None:
                prod.line += (
                    f" (범위 {won(prod.incremental_low_krw)} ~ {won(prod.incremental_high_krw)})"
                )
    # 쿠폰: 누적 사용률
    cou = CampaignSalesCell(key="coupons", label=CELL_LABELS["coupons"])
    if c.status == "known" and c.use_rate is not None:
        cou.status, cou.use_rate = "known", c.use_rate
        cou.line = f"사용률 {c.use_rate * 100:.1f}% ({c.used_units or 0}/{c.issued_units or 0}장)"
    else:
        cou.reason = c.unknown_reason
    # 전체 매장: 판매액·주문 증분 (참고, 범위)
    store = CampaignSalesCell(key="store", label=CELL_LABELS["store"])
    if s is not None and s.status == "known":
        store.status, store.direction = "known", s.direction
        store.incremental_gross_krw = s.incremental_gross_krw
        store.incremental_low_krw, store.incremental_high_krw = (
            s.incremental_low_krw,
            s.incremental_high_krw,
        )
        store.incremental_orders = effect.incremental_orders
        store.line = won(s.incremental_gross_krw or 0)
        if effect.incremental_orders is not None:
            store.line += f" · 주문 {count(effect.incremental_orders)}"
    else:
        store.reason = (s.unknown_reason if s else None) or "판매액을 받지 못함"
    return [att, prod, cou, store]


def _seven(v: VideoPerformanceOutput) -> float | None:
    seven = next((c for c in v.checkpoints if c.checkpoint == "7d"), None)
    return seven.target_ratio if seven else None


def judge(
    effect: CampaignEffectOutput | None,
    videos: Sequence[VideoPerformanceOutput],
    name: str,
    unknown_reason: str | None = None,
) -> tuple[CampaignSalesEffect, list[CampaignAlert]]:
    """③ 매출 효과와 ⑤ 알림. effect가 없으면(기간 부족·쇼핑몰 꺼짐) 판정 없이 이유만."""
    real = [v for v in videos if not v.test]
    for v in videos:
        v.sales_measurable = bool(v.promo_id or v.coupon_id)
        v.sales_reason = None if v.sales_measurable else UNMEASURABLE
    m = sum(1 for v in real if v.sales_measurable)
    sales = CampaignSalesEffect(
        videos_total=len(real),
        videos_measurable=m,
        measurable_line=f"영상 {len(real)}개 중 매출 측정 가능 {m}개",
    )
    alerts: list[CampaignAlert] = []
    if effect is None:
        sales.reason = unknown_reason or "매출을 재지 못함"
        sales.verdict = CampaignSalesVerdict(
            label=f"측정 불가: {sales.reason}", reason=sales.reason
        )
    else:
        verdict = verdict_of(effect)
        sales.status, sales.verdict, sales.cells = verdict.status, verdict, cells_of(effect)
        sales.reason = verdict.reason if verdict.status == "unknown" else None
        if verdict.incremental_gross_krw is not None:
            sales.headline = won(verdict.incremental_gross_krw)
            if verdict.incremental_orders is not None:
                sales.headline += f" · 주문 {count(verdict.incremental_orders)}"
            sales.headline += f" ({verdict.baseline_rule or '기준선'} 대비)"
        won_ratios = [r for v in real if v.verdict == "achieved" and (r := _seven(v)) is not None]
        lost_ratios = [r for v in real if v.verdict == "missed" and (r := _seven(v)) is not None]
        if verdict.verdict == "less" and won_ratios:
            sales.notes.append(f"조회는 목표의 {max(won_ratios):.2f}배였지만 매출은 줄었습니다")
        if verdict.verdict == "more" and lost_ratios and not won_ratios:
            sales.notes.append(
                f"조회는 목표의 {max(lost_ratios):.2f}배에 그쳤지만 매출은 늘었습니다"
            )
        if verdict.verdict == "less":
            alerts.append(
                CampaignAlert(
                    kind="loss", message=f"{name} 캠페인 매출이 평소보다 줄었습니다", note=LOSS_NOTE
                )
            )
        if verdict.status == "unknown" and "공백" in (verdict.reason or ""):
            alerts.append(
                CampaignAlert(
                    kind="data_gap",
                    message=f"{name} 캠페인 주문 기록에 공백이"
                    f" 있어 매출을 재지 못했습니다: {verdict.reason}",
                )
            )
    gaps = [
        f"{v.title or v.video_id} {c.checkpoint}"
        for v in real
        for c in v.checkpoints
        if c.reason == DATA_GAP
    ]
    if gaps:
        alerts.append(
            CampaignAlert(
                kind="data_gap",
                message="영상 수집이 멈춰 못 잰 시점: " + ", ".join(gaps),
                note="영상 문제가 아니라 데이터 문제",
            )
        )
    return sales, alerts


def finance_record(out: CampaignResultOutput, generated_at: str | None) -> CampaignFinanceRecord:
    """재무 기록용 값: finance `CampaignResultInput` 중 데이터가 아는 값만 (집행액 칸 없음).

    gmv_krw·orders는 증가분이 음수면 0, 잰 값은 measured_*에 그대로. 모르면 None.
    기간은 측정 기간(첫 게시 ~ 7일 뒤)의 한국 날짜.
    """
    from data_agent.youtube_store import KST

    verdict = out.sales.verdict if out.sales else None
    gross = verdict.incremental_gross_krw if verdict else None
    orders = verdict.incremental_orders if verdict else None
    start = out.window_from
    return CampaignFinanceRecord(
        campaign_id=out.campaign_id,
        period_from=start.astimezone(KST).date().isoformat() if start else None,
        period_to=(start + timedelta(days=CAMPAIGN_DAYS)).astimezone(KST).date().isoformat()
        if start
        else None,
        gmv_krw=None if gross is None else max(gross, 0),
        orders=None if orders is None else max(orders, 0),
        measured_gmv_krw=gross,
        measured_orders=orders,
        effect_generated_at=generated_at,
    )


def _send_status(out: CampaignResultOutput, env: Mapping[str, str], now: datetime) -> None:
    """기획에 결과 보내기 상태 (모든 묶음). 묶음을 고르기 전에(영상 칸이 있을 때) 붙인다."""
    from data_agent import campaign_update

    out.send_status = campaign_update.read_status(out, env, now)


# ---------------------------------------------------------------- skill


@dataclass
class CampaignResultResult:
    output: CampaignResultOutput
    evidence: Evidence


MeasureFn = Callable[..., Awaitable[CampaignEffectOutput]]


async def campaign_result(
    request: CampaignResultRequest,
    connector: Any,
    *,
    env: Mapping[str, str],
    shorts: ShortsStatsOutput | None = None,
    store: Any = None,
    now: datetime | None = None,
    measure: MeasureFn | None = None,
) -> CampaignResultResult:
    """캠페인 하나의 성과 결과. 영상은 저장분만 읽고(새로 재지 않음), 매출은 campaign_effect."""
    from data_agent.clients.shop_client import ShopUnavailable
    from data_agent.connectors import ConnectorUnavailable
    from data_agent.skills import video_factors
    from data_agent.skills.campaign_effect import measure as measure_effect
    from data_agent.skills.video_performance import describe_all, find_insight, store_of

    now = now or datetime.now(UTC)
    own = store is None
    store = store or store_of(env)
    queries: list[str] = []
    out = CampaignResultOutput(
        campaign_id=request.campaign_id,
        view=request.view,
        sections=list(SECTIONS[request.view]),
        schema_version=SCHEMA_VERSION,
    )
    try:
        mine = [
            o for o in describe_all(store, env, now, shorts) if o.campaign_id == request.campaign_id
        ]
    finally:
        if own:
            store.close()
    queries.append(f"video_performance 저장분 campaign_id={request.campaign_id}")
    if not mine:
        out.reason = f"캠페인 {request.campaign_id}에 등록된 영상이 없음"
        out.summary = out.reason
        _send_status(out, env, now)
        return CampaignResultResult(
            out, Evidence(queries=queries, data_summary=out.summary, collected_at=now)
        )
    real = [o for o in mine if not o.test] or mine
    out.test = all(o.test for o in mine)
    out.insight_id = next((o.insight_id for o in real if o.insight_id), None)
    out.proposal_id = next((o.proposal_id for o in real if o.proposal_id), None)
    name = next((o.topic for o in real if o.topic), None) or request.campaign_id
    out.campaign_name = name
    pubs = [o.published_at for o in real if o.published_at is not None]
    effect: CampaignEffectOutput | None = None
    why: str | None = None
    if not pubs:
        why = "영상 게시 시각을 아직 모름"
    else:
        start = min(pubs)
        end = min(start + timedelta(days=CAMPAIGN_DAYS), now)
        out.window_from, out.window_to = start, end
        if end - start < timedelta(hours=1):
            why = "캠페인 기간이 아직 1시간이 안 됨"
        elif connector is None:
            why = "쇼핑몰 연결이 없음"
        else:
            insight = find_insight(env, out.insight_id) if out.insight_id else None
            products = [p.product_id for p in (insight.products if insight else [])][:10]
            data = CampaignEffectInput(
                campaign_id=request.campaign_id,
                window_from=start,
                window_to=end,
                insight_id=out.insight_id,
                proposal_id=out.proposal_id,
                products=products,
            )
            try:
                effect = await (measure or measure_effect)(data, connector, env=env)
                queries.append(
                    f"campaign_effect 계산 {start.isoformat()}~{end.isoformat()}"
                    f" 관련 상품 {len(products)}개"
                )
            except (ShopUnavailable, ConnectorUnavailable) as error:
                why = f"쇼핑몰 주문을 읽지 못함: {error}"
    sales, alerts = judge(effect, mine, name, why)
    out.status = "known"
    out.videos = mine
    out.sales = sales
    out.advice = [
        CampaignVideoAdvice(
            video_id=o.video_id or "",
            name=o.title or o.video_id or "",
            verdict=o.verdict,
            factors=list(o.factors),
            actions=list(o.actions),
        )
        for o in real
        if not o.test
    ]
    out.alerts = alerts
    out.limits = [video_factors.ANALYTICS_LIMIT, *SALES_LIMITS]
    label = sales.verdict.label if sales.verdict else "측정 불가"
    out.summary = f"{name}: 영상 {sales.videos_total}개 · 매출 {label} · {sales.measurable_line}"
    if request.view == "finance":
        made = effect.generated_at.isoformat() if effect and effect.generated_at else None
        out.finance_record = finance_record(out, made)
    _send_status(out, env, now)
    _keep(out)
    return CampaignResultResult(
        out, Evidence(queries=queries, data_summary=out.summary, collected_at=now)
    )


def _keep(out: CampaignResultOutput) -> None:
    """부서별 묶음: 고른 칸만 남긴다 (마케팅은 성과 요인 없이 개선 방향만)."""
    keep = set(out.sections)
    if "videos" not in keep:
        out.videos = []
    if "sales" not in keep:
        out.sales = None
    if "advice" not in keep:
        out.advice, out.limits = [], [lim for lim in out.limits if lim in SALES_LIMITS]
    if out.view == "marketing":
        for a in out.advice:
            a.factors = []
        out.limits = [lim for lim in out.limits if lim not in SALES_LIMITS]
    if "alerts" not in keep:
        out.alerts = []

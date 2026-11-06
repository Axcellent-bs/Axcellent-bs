"""인사이트 "지난번 결과" (브리프 v4 `past_results`): 성과 → 기획.

같은 insight_id로 한 캠페인의 성과 결과(`campaign_result`)를 인사이트마다 최근 3개까지, 최신순으로
붙인다. 브리프를 응답할 때 붙이고(저장된 브리프도), 인사이트 판정·순서는 바꾸지 않는다.

- 연결: 인사이트의 insight_id·예전 id(legacy_insight_id·legacy_insight_ids)와 캠페인 결과의
  insight_id가 같으면 연결 (주제 이름은 보지 않음). 시험 캠페인은 뺀다
- 조회 판정: 7일이 끝난 영상(달성·미달)의 목표 대비 배수 중앙값 ≥ 1이면 달성, 미만이면 미달,
  끝난 영상이 없고 측정 중이 있으면 측정 중 (예상치는 넣지 않음)
- 매출 판정·증분·개선 방향·알림은 campaign_result 그대로
- 띠: 매출 덜 팔림이거나 알림 loss → red, 조회 달성 + 매출 더 팔림 → green, 그 밖 → gray.
  둘째 줄 "조회 목표의 N배 (영상 M개 중 K개 달성) · 매출 +N원 · 주문 +N건", 셋째 줄
  "지난번 개선 방향: …" (조회 달성 + 매출 덜 팔림이면 반대 문구)
- 조회와 매출을 합친 점수는 만들지 않는다
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import (
    CampaignResultOutput,
    CampaignResultRequest,
    InsightPastResult,
    ShortsStatsOutput,
    TrendInsightBriefOutput,
)

LIMIT = 3
SCHEMA_VERSION = "trend_insight_brief.v4"  # past_results를 붙인 응답
CONTRA = "조회와 매출이 반대로 나왔습니다. 다시 한다면 쿠폰 조건을 기획·재무와 먼저 확인하세요."
VIEWS_LABEL = {
    "achieved": "조회 달성",
    "missed": "조회 미달",
    "measuring": "측정 중",
    "none": "조회 판정 없음",
}
CACHE_SECONDS = 600  # 캠페인 결과(쇼핑몰 주문 읽기)를 브리프 요청마다 다시 계산하지 않게


def won(v: float) -> str:
    return f"{round(v):+,}원"


def _seven(video: Any) -> float | None:
    seven = next((c for c in video.checkpoints if c.checkpoint == "7d"), None)
    return seven.target_ratio if seven else None


def to_past(r: CampaignResultOutput) -> InsightPastResult:
    """캠페인 결과 하나 → 지난번 결과 한 줄 (값은 그대로, 띠 색·문장만 붙임)."""
    real = [v for v in r.videos if not v.test]
    finished = [v for v in real if v.verdict in ("achieved", "missed")]
    ratios = [x for v in finished if (x := _seven(v)) is not None]
    achieved = sum(1 for v in real if v.verdict == "achieved")
    if ratios:
        views = "achieved" if statistics.median(ratios) >= 1 else "missed"
    elif any(v.verdict == "measuring" for v in real):
        views = "measuring"
    else:
        views = "none"
    ratio = round(statistics.median(ratios), 2) if ratios else None
    verdict = r.sales.verdict if r.sales else None
    sales = verdict.verdict if verdict else None
    gross = verdict.incremental_gross_krw if verdict else None
    orders = verdict.incremental_orders if verdict else None
    actions = list(dict.fromkeys(a for adv in r.advice for a in adv.actions))
    loss = any(a.kind == "loss" for a in r.alerts)
    band = (
        "red"
        if sales == "less" or loss
        else "green"
        if views == "achieved" and sales == "more"
        else "gray"
    )
    if views in ("achieved", "missed") and ratio is not None:
        first = f"조회 목표의 {ratio:.2f}배 (영상 {len(real)}개 중 {achieved}개 달성)"
    elif views == "measuring":
        first = f"조회 측정 중 (영상 {len(real)}개 중 {achieved}개 달성)"
    else:
        first = VIEWS_LABEL["none"]
    if gross is not None:
        second = f"매출 {won(gross)}" + (
            f" · 주문 {round(orders):+,}건" if orders is not None else ""
        )
    else:
        second = f"매출 {verdict.label if verdict else '측정 불가'}"
    if views == "achieved" and sales == "less":
        note: str | None = CONTRA
    else:
        note = f"지난번 개선 방향: {actions[0]}" if actions else None
    return InsightPastResult(
        campaign_id=r.campaign_id,
        campaign_name=r.campaign_name,
        window_from=r.window_from,
        window_to=r.window_to,
        views_verdict=views,
        views_label=VIEWS_LABEL[views],
        target_ratio=ratio,
        videos_total=len(real),
        videos_achieved=achieved,
        sales_verdict=sales,
        sales_label=verdict.label if verdict else "측정 불가",
        sales_low_sample=bool(verdict and verdict.low_sample),
        incremental_gross_krw=gross,
        incremental_orders=orders,
        actions=actions,
        alerts=list(r.alerts),
        band=band,
        summary_line=f"{first} · {second}",
        note_line=note,
    )


def _start(r: CampaignResultOutput) -> datetime:
    return r.window_from or datetime.min.replace(tzinfo=UTC)


def attach_past(
    brief: TrendInsightBriefOutput, results: Sequence[CampaignResultOutput]
) -> TrendInsightBriefOutput:
    """인사이트마다 past_results를 붙인 새 브리프. 다른 칸·순서는 그대로."""
    out = brief.model_copy(deep=True)
    real = sorted(
        (r for r in results if not r.test and r.insight_id),
        key=lambda r: (_start(r), r.campaign_id),
        reverse=True,
    )
    for insight in out.insights:
        ids = {insight.insight_id, insight.legacy_insight_id, *insight.legacy_insight_ids}
        mine = [r for r in real if r.insight_id in ids][:LIMIT]
        insight.past_results = [to_past(r) for r in mine]
    out.schema_version = SCHEMA_VERSION
    return out


# ---------------------------------------------------------------- 캠페인 결과 모으기

_CACHE: dict[str, tuple[float, CampaignResultOutput]] = {}


def clear_cache() -> None:
    _CACHE.clear()


async def campaign_results(
    env: Mapping[str, str],
    connector: Any,
    shorts: ShortsStatsOutput | None = None,
    *,
    now: datetime | None = None,
    measure: Callable[..., Awaitable[Any]] | None = None,
) -> list[CampaignResultOutput]:
    """인사이트가 있는 캠페인마다 campaign_result (기획 묶음). 10분 동안 다시 계산하지 않는다."""
    from data_agent.skills import campaign_result as cr
    from data_agent.skills.video_performance import DB_ENV, DEFAULT_DB, VideoStore

    path = env.get(DB_ENV) or DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return []  # 등록된 영상이 없다 (캠페인 없음)
    store = VideoStore(path)
    try:
        ids = list(
            dict.fromkeys(
                v.campaign_id
                for v in store.videos(include_test=False)
                if v.campaign_id and v.insight_id
            )
        )
    finally:
        store.close()
    out: list[CampaignResultOutput] = []
    clock = time.monotonic()
    for cid in ids:
        hit = _CACHE.get(cid)
        if hit is not None and clock - hit[0] < CACHE_SECONDS:
            out.append(hit[1])
            continue
        result = await cr.campaign_result(
            CampaignResultRequest(campaign_id=cid),
            connector,
            env=env,
            shorts=shorts,
            now=now,
            measure=measure,
        )
        _CACHE[cid] = (clock, result.output)
        out.append(result.output)
    return out

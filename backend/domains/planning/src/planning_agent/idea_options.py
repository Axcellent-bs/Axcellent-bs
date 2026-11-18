"""기획 방향 선택지 (11/18 예진): 인사이트를 받으면 방향 3개를 내고 하나에 "추천"을 붙인다.

- 갈래는 코드에 고정: 유행에 올라타기 · 자체 기획 퍼뜨리기 · 우리 상품과 엮기
- 올라탈 후보: 데이터분석 `trend_insight_brief` 주제 중 같은 숏츠 형식·광고 재료·실존 아님
  (값은 그대로 옮긴다)
- 추천은 고객사 규모 숫자(팔로워·지난 캠페인 조회)로 코드가 정한다. 같은 입력이면 같은 결과
- LLM을 부르지 않는다. 모르는 숫자는 "확인할 수 없음"이고 지어내지 않는다
"""

from __future__ import annotations

import os
import statistics

from ax_contracts.schemas.data import TrendInsight
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from ax_contracts.schemas.planning import InsightPast, ReceiveInsightInput
from ax_workflow.models import IdeaAngle, IdeaOption, ScaleFacts, TrendRef

ANGLE_LABEL: dict[IdeaAngle, str] = {
    "ride_trend": "유행에 올라타기",
    "own_trend": "자체 기획 퍼뜨리기",
    "with_product": "우리 상품과 엮기",
}
MAX_REFS = 3
UNKNOWN = "확인할 수 없음"
NO_SCALE = "규모 숫자를 받지 못해 추천 없음"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def small_followers() -> int:
    """이 수보다 팔로워가 적으면 새 유행을 퍼뜨리기 어렵다고 본다."""
    return _env_int("PLANNING_SMALL_FOLLOWERS", 10000)


def small_views() -> int:
    """지난 캠페인 조회 중앙값이 이보다 낮으면 작다고 본다."""
    return _env_int("PLANNING_SMALL_VIEWS", 10000)


def trend_candidates(inp: ReceiveInsightInput, trends: list[TrendInsight]) -> list[TrendRef]:
    """인사이트와 같은 숏츠 형식이면서 광고 재료로 쓸 수 있고 실존 인물·상표가 없는 다른 주제.

    11/18 재현 님: `shorts_format`은 카테고리·전체 통계로 채워질 수 있어 주제 영상과 상관없을 수
    있다. 그래서 주제 자기 숏츠 칸(`shorts_format_topic_median_views`)이 있는 주제만 후보로 쓴다.
    예전 응답(그 칸 없음)이면 후보가 없다(지어내지 않음)."""
    fmt = inp.shorts_format
    if not fmt:
        return []
    picked = [
        t
        for t in trends
        if t.shorts_format == fmt
        and t.ad_material is True
        and t.real_entity is False
        and t.insight_id != inp.insight_id
        and t.topic != inp.title
        and t.shorts_format_topic_median_views is not None
    ]
    picked.sort(key=lambda t: -t.score)
    return [
        TrendRef(
            topic=t.topic,
            median_views=t.shorts_format_topic_median_views,
            at=t.shorts_format_topic_at,
            examples=[e.title[:120] for e in t.shorts_format_topic_examples if e.title][:MAX_REFS],
        )
        for t in picked[:MAX_REFS]
    ]


def scale_facts(past: list[InsightPast], followers: ChannelFollowersOutput | None) -> ScaleFacts:
    """가장 큰 공식 채널 팔로워와 지난 캠페인 조회 중앙값 (숫자가 있는 것만)."""
    top = None
    if followers is not None and followers.status == "known" and followers.channels:
        top = max(followers.channels, key=lambda c: c.followers)
    views = [p.views for p in past if p.views is not None]
    return ScaleFacts(
        followers=top.followers if top else None,
        followers_channel=top.channel if top else None,
        past_views_median=float(statistics.median(views)) if views else None,
        past_views_count=len(views),
    )


def _scale_text(scale: ScaleFacts) -> str:
    if scale.followers is not None:
        ch = f"({scale.followers_channel})" if scale.followers_channel else ""
        fol = f"팔로워 {scale.followers:,}{ch}"
    else:
        fol = f"팔로워 {UNKNOWN}"
    if scale.past_views_median is not None:
        views = (
            f"지난 캠페인 조회 중앙값 {scale.past_views_median:,.0f}({scale.past_views_count}건)"
        )
    else:
        views = f"지난 캠페인 조회 {UNKNOWN}"
    return f"{fol} · {views}"


def _is_small(scale: ScaleFacts) -> bool:
    if scale.followers is not None and scale.followers < small_followers():
        return True
    return scale.past_views_median is not None and scale.past_views_median < small_views()


def build_options(
    inp: ReceiveInsightInput, trends: list[TrendInsight] | None, scale: ScaleFacts
) -> list[IdeaOption]:
    """선택지 3개(순서 고정)와 추천 하나(또는 없음). `trends`가 None이면 목록을 못 받은 것."""
    refs = trend_candidates(inp, trends or [])
    if not inp.shorts_format:
        ride_block: str | None = "형식 정보 없음"
    elif trends is None:
        ride_block = "트렌드 목록을 받지 못함"
    elif not refs:
        ride_block = "지금 뜨는 후보 없음"
    else:
        ride_block = None
    prod_block = None if inp.products else "연결된 우리 상품 없음"
    opts = [
        IdeaOption(
            id="ride_trend", label=ANGLE_LABEL["ride_trend"], available=ride_block is None,
            blocked_reason=ride_block, refs=refs,
        ),
        IdeaOption(id="own_trend", label=ANGLE_LABEL["own_trend"], available=True),
        IdeaOption(
            id="with_product", label=ANGLE_LABEL["with_product"], available=prod_block is None,
            blocked_reason=prod_block,
        ),
    ]  # fmt: skip
    by = {o.id: o for o in opts}
    known = scale.followers is not None or scale.past_views_median is not None
    if not known:
        by["own_trend"].reason = NO_SCALE
        return opts
    facts = _scale_text(scale)
    if by["ride_trend"].available and _is_small(scale):
        pick, why = by["ride_trend"], f"{facts} → 새로 퍼뜨리기 어려움"
    elif by["with_product"].available:
        pick, why = by["with_product"], f"{facts} → 우리 상품을 앞에 세우는 편이 맞음"
    else:
        pick, why = by["own_trend"], f"{facts} → 자체 기획을 퍼뜨릴 만한 규모"
    pick.recommended = True
    pick.reason = why
    return opts

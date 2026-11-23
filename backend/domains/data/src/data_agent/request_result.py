"""의뢰 영상(`wr_*`) 7일 성과를 기획에 보내기 (11/23, 게시 → 성과 → 다음 기획).

캠페인 경로(`campaign_update.send_one`, campaign_result 매출 판정)와 같은 루프·같은 보내기 길에 한
갈래를 더한다. 매출 효과는 프로모션 링크·쿠폰이 없어 재지 않는다 (한계 문장만).

- 대상: video_performance 등록 영상 중 campaign_id가 `wr_`로 시작하고 시험 아니고
  proposal_id가 있는 것
- 언제: 7일 시점을 쟀거나, 7일 허용 창이 지나 놓친 뒤 (게시 시각을 끝내 모르면 등록 + 7일 + 창 뒤)
- direction: 7일 조회 ÷ 우리 릴스 중앙값(그 시점에 같이 잰 값) ≥ `UP` 늘었음, ≤ `DOWN` 줄었음,
  그 사이 같음, 못 잼·low_sample 확인 불가. LLM 안 씀
- summary: "의뢰 영상 7일 조회 N회 · 우리 릴스 중앙값의 X배" 고정형
- facts: 시점별 조회·좋아요·댓글, 배수(기준 포함), 한계 한 줄. 출처 "instagram business_discovery"
  (보낼 때 마케팅에서 읽은 광고 숫자·유튜브 성과 두 줄은 한계 줄 바로 앞, `marketing_reads`)
- 다음 인사이트 "지난번 결과": 보낸 의뢰 영상 결과를 최근 3개까지 `past_of`로 (trend_report_insight)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.planning import InsightFact, InsightPast, ReceiveCampaignUpdateInput
from data_agent.skills import video_performance as vp

PREFIX = "wr_"
UP = 1.5  # 7일 조회가 우리 릴스 중앙값의 1.5배 이상이면 늘었음
DOWN = 0.67  # 0.67배 이하면 줄었음
SOURCE = "instagram business_discovery"
SALES_LIMIT = "매출은 프로모션 링크가 없어 측정 불가"
SEVEN: vp.VideoCheckpointName = "7d"
PAST_MAX = 3
LABEL_MAX, VALUE_MAX = 120, 80
FACTS_MAX = 12  # ReceiveCampaignUpdateInput.facts 최대


def is_request_video(v: vp.Video) -> bool:
    return bool(v.campaign_id and v.campaign_id.startswith(PREFIX) and not v.test
                and v.proposal_id)  # fmt: skip


def _end(v: vp.Video) -> datetime:
    """7일 시점을 놓쳤다고 보는 시각 (게시 시각 모르면 등록 시각 기준)."""
    start = v.published_at or v.registered_at
    return start + timedelta(hours=vp.CHECKPOINTS[SEVEN] + vp.WINDOW_HOURS[SEVEN])


def ready(v: vp.Video, store: vp.VideoStore, now: datetime) -> bool:
    """보낼 차례: 7일을 쟀거나 7일 창이 지났다."""
    return SEVEN in store.points(v.key) or now >= _end(v)


def send_after(v: vp.Video) -> datetime:
    start = v.published_at or v.registered_at
    return start + timedelta(hours=vp.CHECKPOINTS[SEVEN])


def seven_day(v: vp.Video, store: vp.VideoStore) -> tuple[int | None, float | None, int | None]:
    """(7일 조회, 그때 잰 우리 릴스 중앙값(3개 미만이면 None), 표본 수). 못 쟀으면 None들."""
    p = store.points(v.key).get(SEVEN)
    base = store.baselines(v.key).get(SEVEN)
    return (p.views if p else None), (base[0] if base else None), (base[1] if base else None)


def direction_of(views: int | None, median: float | None) -> str:
    if views is None or not median:
        return "확인 불가"
    ratio = views / median
    if ratio >= UP:
        return "늘었음"
    if ratio <= DOWN:
        return "줄었음"
    return "같음"


def summary_of(views: int | None, median: float | None, n: int | None) -> str:
    if views is None:
        return "의뢰 영상 7일 조회 확인 불가 (7일 시점을 재지 못함)"
    if not median:
        return f"의뢰 영상 7일 조회 {views:,}회 · 비교 기준 표본 적음 (우리 릴스 {n or 0}개)"
    return f"의뢰 영상 7일 조회 {views:,}회 · 우리 릴스 중앙값의 {views / median:.2f}배"


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def facts_of(
    v: vp.Video, store: vp.VideoStore, extras: Sequence[InsightFact] = ()
) -> list[InsightFact]:
    """시점별 조회·좋아요·댓글과 배수 (잰 시점만), `extras`(광고 숫자·유튜브 성과), 마지막에 한계
    한 줄. 최대 12."""
    points = store.points(v.key)
    bases = store.baselines(v.key)
    rows: list[tuple[str, str]] = []
    for c in vp.CHECKPOINTS:
        p = points.get(c)
        if p is None or p.views is None:
            continue
        name = vp.NAMES[c]
        bits = [f"{p.views:,}회"]
        if p.likes is not None:
            bits.append(f"좋아요 {p.likes:,}")
        if p.comments is not None:
            bits.append(f"댓글 {p.comments:,}")
        rows.append((f"{name} 조회·좋아요·댓글", " · ".join(bits)))
        median, n = bases.get(c, (None, 0))
        if median:
            ratio = f"{p.views / median:.2f}배 (중앙값 {median:,.0f}회, 릴스 {n}개)"
            rows.append((f"{name} 우리 릴스 대비 배수", ratio))
        else:
            rows.append((f"{name} 우리 릴스 대비 배수", f"low_sample (릴스 {n}개, 3개 미만)"))
    rows = rows[: FACTS_MAX - 1 - len(extras)]
    facts = [InsightFact(label=_clip(a, LABEL_MAX), value=_clip(b, VALUE_MAX), source=SOURCE,
                         link=v.video_url) for a, b in rows]  # fmt: skip
    facts.extend(extras)
    facts.append(InsightFact(label="한계", value=SALES_LIMIT, source="data.request_result"))
    return facts


def build_update(
    v: vp.Video, store: vp.VideoStore, extras: Sequence[InsightFact] = ()
) -> ReceiveCampaignUpdateInput:
    views, median, n = seven_day(v, store)
    return ReceiveCampaignUpdateInput(
        proposal_id=v.proposal_id or "",
        campaign_id=v.campaign_id or "",
        stage="result",
        direction=direction_of(views, median),  # type: ignore[arg-type]  # 네 값 중 하나
        summary=summary_of(views, median, n),
        facts=facts_of(v, store, extras),
    )


# ---------------------------------------------------------------- 다음 인사이트 "지난번 결과"


def past_of(env: Mapping[str, str], sends: Sequence[tuple[str, str | None, datetime | None]] | None
            = None) -> list[InsightPast]:  # fmt: skip
    """기획에 보낸 의뢰 영상 결과 (최근 3개, 최신순). 보낸 기록(`campaign_update` 저장소)과 영상
    저장분에서 다시 만든다. 파일이 없으면 만들지 않고 빈 목록."""
    from data_agent import campaign_update as cu

    if sends is None:
        path = env.get(cu.DB_ENV) or cu.DEFAULT_DB
        if str(path) != ":memory:" and not Path(path).is_file():
            return []
        store = cu.SendStore(path)
        try:
            sends = store.sent_with_prefix(PREFIX)
        finally:
            store.close()
    if not sends:
        return []
    path = env.get(vp.DB_ENV) or vp.DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return []
    videos = vp.VideoStore(path)
    try:
        out: list[InsightPast] = []
        for cid, direction, _at in sends[:PAST_MAX]:
            v = videos.find(campaign_id=cid)
            if v is None:
                continue
            views, median, n = seven_day(v, videos)
            p = videos.points(v.key).get(SEVEN)
            out.append(InsightPast(
                campaign_id=cid[:80], views_label=_clip(f"조회 {direction or '확인 불가'}", 40),
                sales_label=_clip(SALES_LIMIT, 80), band="gray",
                summary_line=_clip(summary_of(views, median, n), 300),
                views=views, views_at=p.measured_at if p else None,
            ))  # fmt: skip
        return out
    finally:
        videos.close()

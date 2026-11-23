"""광고 숫자·유튜브 성과를 마케팅에서 읽어 기획에 보낼 근거 줄로 만든다 (11/23).

성과 결과를 기획에 보낼 때(`campaign_update`, 캠페인 경로·의뢰 영상 경로 둘 다, 같은 7일 시점·같은
기능 플래그) 마케팅 read skill을 A2A(`call_agent`)로 부른다. domains끼리 import하지 않는다.

- 광고 숫자: `get_campaign_ad_metrics`(campaign_id `cmp_…`). 의뢰(`wr_…`)는 기획 제안 번호로
  마케팅 캠페인 번호(`cmp_` + 제안 번호)를 만든다. 칸별 합계의 `None`은 "모름"이고 0과 구분한다
- 유튜브 성과: 데이터에 등록된 유튜브 영상 + 마케팅이 그 번호에 연결해 올린 영상
  (`youtube_uploads_list`)을 `youtube_video_stats`로 읽는다. 읽은 때 누적 값이다
- 숫자를 지어내지 않는다. 못 읽으면 "못 읽음: <사유>"
- 근거 줄은 계약(`InsightFact`) 그대로라 기획으로 가는 본문 모양은 바뀌지 않는다
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.ad_metrics import (
    AD_CAMPAIGN_ID_PATTERN,
    GET_AD_METRICS_SKILL,
    AdMetricsTotals,
    GetAdMetricsOutput,
)
from ax_contracts.schemas.marketing_youtube_stats import (
    YOUTUBE_STATS_MAX,
    YOUTUBE_UPLOADS_LIST_SKILL,
    YOUTUBE_VIDEO_ID_PATTERN,
    YOUTUBE_VIDEO_STATS_SKILL,
    YoutubeUploadsListOutput,
    YoutubeVideoStat,
    YoutubeVideoStatsOutput,
)
from ax_contracts.schemas.planning import InsightFact
from data_agent import company_scope

TARGET = "marketing"
AD_LABEL = "광고 숫자 (노출·클릭·광고비)"
YOUTUBE_LABEL = "유튜브 조회·좋아요·댓글 (보낸 때 누적)"
AD_SOURCE = "marketing.get_campaign_ad_metrics"
YOUTUBE_SOURCE = "marketing.youtube_video_stats"
UNKNOWN = "모름"
VALUE_MAX = 80
DEADLINE = timedelta(seconds=20)
YOUTUBE_REASONS = {
    "no_videos": "연결된 유튜브 영상 없음",
    "missing_keys": "유튜브 채널 연결 안 됨",
    "invalid_credentials": "유튜브 연결이 만료됨",
}

Read = Callable[[str, AgentRequest], Awaitable[AgentResult]]


def _clip(text: str) -> str:
    return text if len(text) <= VALUE_MAX else text[: VALUE_MAX - 1] + "…"


def _n(value: int | None, unit: str = "") -> str:
    return UNKNOWN if value is None else f"{value:,}{unit}"


def ad_campaign_of(campaign_id: str, proposal_id: str | None) -> str | None:
    """광고 숫자를 적는 마케팅 캠페인 번호. 의뢰면 `cmp_` + 제안 번호(마케팅 briefs와 같은 규칙)."""
    cid = campaign_id if campaign_id.startswith("cmp_") else (
        f"cmp_{proposal_id}" if proposal_id else "")  # fmt: skip
    return cid if re.fullmatch(AD_CAMPAIGN_ID_PATTERN, cid) else None


def youtube_ids_of(ids: Iterable[str | None]) -> list[str]:
    """유튜브 video_id 모양(11자)만, 순서 유지·중복 없이."""
    return list(dict.fromkeys(i for i in ids if i and re.fullmatch(YOUTUBE_VIDEO_ID_PATTERN, i)))


def ad_value(out: GetAdMetricsOutput) -> str:
    if out.status == "unknown":
        return _clip(f"{UNKNOWN} ({out.reason or '아직 입력 없음'})")
    t = out.totals or AdMetricsTotals()
    text = f"노출 {_n(t.impressions)} · 클릭 {_n(t.clicks)} · 광고비 {_n(t.spend_krw, '원')}"
    if t.missing_dates:
        text += " (빈 날짜 있음)"
    return _clip(text)


def _sum(items: list[YoutubeVideoStat], field: str) -> int | None:
    values = [getattr(i, field) for i in items]
    return None if any(v is None for v in values) else sum(values)


def youtube_value(out: YoutubeVideoStatsOutput) -> tuple[str, str | None]:
    """(값, 링크). 여러 영상이면 합(한 칸이라도 모르면 그 칸은 모름)."""
    if out.status != "ok":
        reason = YOUTUBE_REASONS.get(out.reason or "", out.reason or "이유 없음")
        return _clip(f"못 읽음: {reason}"), None
    ok = [i for i in out.items if i.status == "ok"]
    if not ok:
        return "못 읽음: 영상을 찾지 못함 (지웠거나 비공개)", None
    text = (f"조회 {_n(_sum(ok, 'views'), '회')} · 좋아요 {_n(_sum(ok, 'likes'))}"
            f" · 댓글 {_n(_sum(ok, 'comments'))}")  # fmt: skip
    if len(ok) > 1:
        text += f" (영상 {len(ok)}개 합)"
    return _clip(text), (ok[0].url if len(ok) == 1 else None)


def _meta(context_id: str, key: str) -> RequestMeta:
    return RequestMeta(
        context_id=context_id,
        token="internal:data",  # call_agent가 data 토큰으로 바꿔 보낸다
        tenant_id=company_scope.meta_tenant(),
        deadline=datetime.now(UTC) + DEADLINE,
        idempotency_key=key,
        requester="data",
    )


async def _ask(read: Read, context_id: str, skill: str, body: dict[str, Any], key: str,
               model: type[Any]) -> tuple[Any | None, str | None]:  # fmt: skip
    """(출력, 못 읽은 사유). 마케팅이 답하지 않거나 모양이 틀리면 사유만."""
    try:
        reply = await read(TARGET, AgentRequest(meta=_meta(context_id, key), skill=skill,
                                                input=body))  # fmt: skip
    except Exception as error:  # noqa: BLE001 - 읽기 실패는 근거 줄 "못 읽음"으로 남긴다
        return None, f"마케팅 호출 실패 ({type(error).__name__})"
    if reply.status != TaskStatus.COMPLETED:
        return None, f"마케팅이 답하지 않음 ({reply.outcome or reply.status.value})"
    try:
        return model.model_validate(reply.output or {}), None
    except ValidationError:
        return None, "마케팅 응답 모양이 맞지 않음"


async def ad_fact(read: Read, context_id: str, campaign_id: str,
                  proposal_id: str | None) -> InsightFact:  # fmt: skip
    cid = ad_campaign_of(campaign_id, proposal_id)
    if cid is None:
        value = f"{UNKNOWN} (광고 캠페인 번호 없음)"
    else:
        out, why = await _ask(read, context_id, GET_AD_METRICS_SKILL, {"campaign_id": cid},
                              f"{campaign_id}:{GET_AD_METRICS_SKILL}",
                              GetAdMetricsOutput)  # fmt: skip
        value = _clip(f"못 읽음: {why}") if out is None else ad_value(out)
    return InsightFact(label=AD_LABEL, value=value, source=AD_SOURCE)


async def youtube_fact(read: Read, context_id: str, campaign_id: str,
                       known_ids: Iterable[str | None]) -> InsightFact:  # fmt: skip
    ids = youtube_ids_of(known_ids)
    listed, list_why = await _ask(
        read, context_id, YOUTUBE_UPLOADS_LIST_SKILL, {"campaign_id": campaign_id[:80]},
        f"{campaign_id}:{YOUTUBE_UPLOADS_LIST_SKILL}", YoutubeUploadsListOutput)  # fmt: skip
    if listed is not None:
        ids = youtube_ids_of([*ids, *(u.video_id for u in listed.items),
                              *(u.video_id for u in listed.request_items)])  # fmt: skip
    ids = ids[:YOUTUBE_STATS_MAX]
    link: str | None = None
    if not ids:
        why = list_why or YOUTUBE_REASONS["no_videos"]
        value = _clip(f"못 읽음: {why}")
    else:
        out, why = await _ask(read, context_id, YOUTUBE_VIDEO_STATS_SKILL, {"video_ids": ids},
                              f"{campaign_id}:{YOUTUBE_VIDEO_STATS_SKILL}",
                              YoutubeVideoStatsOutput)  # fmt: skip
        value, link = (_clip(f"못 읽음: {why}"), None) if out is None else youtube_value(out)
    return InsightFact(label=YOUTUBE_LABEL, value=value, source=YOUTUBE_SOURCE, link=link)


async def performance_facts(read: Read, context_id: str, campaign_id: str,
                            proposal_id: str | None,
                            youtube_ids: Iterable[str | None]) -> list[InsightFact]:  # fmt: skip
    """기획 본문에 더할 근거 줄 2개: 광고 숫자, 유튜브 성과."""
    return [
        await ad_fact(read, context_id, campaign_id, proposal_id),
        await youtube_fact(read, context_id, campaign_id, youtube_ids),
    ]

"""원인 추정에 쓸 사실을 다른 부서에서 모은다 (`root_cause_hypotheses`). LLM은 쓰지 않는다.

- 모든 호출은 `call_agent`(A2A)로 한다. 다른 domains 코드는 import하지 않으므로
  응답은 dict로 읽는다.
- 범위: 이상 구간 앞 30분 ~ 구간 끝.
  - 배포·머지된 PR: planning `find_related_prs`
  - 게시 중이던 공지: marketing `list_active_notices` (이상이 시작된 시각 기준)
  - 문의 급증: support `detect_inquiry_surge`
    (지금 기준 최근 N분이라, 구간이 24시간보다 오래면 확인할 수 없음)
  - 지난 비슷한 사건: memory `card_search`
- 서비스가 꺼져 있으면(`service_disabled`) 건너뛰고 "확인할 수 없음(서비스 꺼짐)" 사실을 남긴다.
  다른 실패도 지어내지 않고 "확인할 수 없음"으로 남긴다.
- 숫자·시각은 받은 응답 그대로 옮긴다. 문장은 코드가 정한 틀로만 쓴다.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import RootCauseInput

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]

LOOKBACK = timedelta(minutes=30)  # 이상 구간 앞으로 더 보는 시간
TIMEOUT_SECONDS = 5.0  # 보조 조회라 오래 기다리지 않는다
SURGE_MAX_MINUTES = 1440  # support `detect_inquiry_surge`가 받는 최대 창
PAST_INCIDENTS = 3


@dataclass(frozen=True)
class RawFact:
    """fact_id를 붙이기 전의 사실."""

    kind: str
    at: datetime | None
    source: str
    ref: str | None
    summary: str


def span(data: RootCauseInput) -> tuple[datetime, datetime]:
    """사실을 모으는 범위: 이상 구간 앞 30분 ~ 구간 끝."""
    return data.window_from - LOOKBACK, data.window_to


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _unavailable(source: str, result: AgentResult | None, error: str | None = None) -> RawFact:
    """출처에서 사실을 받지 못했다. 이유만 남긴다(지어내지 않음)."""
    if (
        result is not None
        and result.status == TaskStatus.REJECTED
        and (result.outcome == "service_disabled")
    ):
        service = (result.output or {}).get("to_service")
        where = f"{service} 서비스" if service else "서비스"
        reason = f"확인할 수 없음(서비스 꺼짐): {where}가 꺼져 있어 {source}를 건너뜀"
    elif result is not None:
        reason = f"확인할 수 없음: {source} 응답 {result.status.value} ({result.outcome or '-'})"
    else:
        reason = f"확인할 수 없음: {source} 호출 실패 ({error or '-'})"
    return RawFact("unavailable", None, source, None, reason)


async def _ask(
    send: SendFn, target: str, skill: str, payload: dict[str, Any], meta: RequestMeta
) -> tuple[AgentResult | None, str | None]:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=TIMEOUT_SECONDS))
    request = AgentRequest(
        meta=meta.model_copy(update={"deadline": deadline}), skill=skill, input=payload
    )
    try:
        return await asyncio.wait_for(send(target, request), timeout=TIMEOUT_SECONDS), None
    except Exception as error:  # 보조 조회라 어떤 실패든 확인할 수 없음으로 남기고 계속한다
        return None, type(error).__name__


async def deploys(data: RootCauseInput, meta: RequestMeta, send: SendFn) -> list[RawFact]:
    source = "planning.find_related_prs"
    start, end = span(data)
    payload = {"window_from": _iso(start), "window_to": _iso(end), "component": data.component}
    result, error = await _ask(send, "planning", "find_related_prs", payload, meta)
    if result is None or result.status != TaskStatus.COMPLETED:
        return [_unavailable(source, result, error)]
    facts = []
    for pr in (result.output or {}).get("candidates") or []:
        number, merged_at = pr.get("pr_number"), _parse_time(pr.get("merged_at"))
        if not isinstance(number, int) or merged_at is None or not start <= merged_at <= end:
            continue
        title = str(pr.get("title") or "").strip()
        summary = f"PR #{number} 병합·배포 {_iso(merged_at)}" + (f": {title}" if title else "")
        facts.append(RawFact("deploy", merged_at, source, f"pr:{number}", summary))
    return facts


async def notices(data: RootCauseInput, meta: RequestMeta, send: SendFn) -> list[RawFact]:
    _, end = span(data)
    return await notices_at(data.window_from, end, meta, send, exclude_context=data.context_id)


async def notices_at(
    at: datetime,
    end: datetime,
    meta: RequestMeta,
    send: SendFn | None = None,
    *,
    exclude_context: str | None = None,
) -> list[RawFact]:
    """`at` 시각에 게시 중이던 공지·캠페인 (`end` 뒤에 게시한 것은 뺀다).

    원인 추정과 이상 탐지(세일·캠페인 근거)가 같이 쓴다.
    """
    send = send or call_agent
    source = "marketing.list_active_notices"
    payload = {"at": _iso(at), "limit": 20}
    result, error = await _ask(send, "marketing", "list_active_notices", payload, meta)
    if result is None or result.status != TaskStatus.COMPLETED:
        return [_unavailable(source, result, error)]
    facts = []
    for item in (result.output or {}).get("items") or []:
        if exclude_context and item.get("context_id") == exclude_context:
            continue  # 이 사건에 대응해 올린 공지는 원인이 아니다
        published = _parse_time(item.get("published_at")) or _parse_time(item.get("valid_from"))
        if published is None or published > end:
            continue
        notice_id = item.get("notice_id")
        channel = item.get("channel_code") or "-"
        summary = f"공지 게시 {_iso(published)} (채널 {channel})"
        facts.append(RawFact("notice", published, source, notice_id, summary))
    return facts


async def inquiry_surges(
    data: RootCauseInput, meta: RequestMeta, send: SendFn, now: datetime
) -> list[RawFact]:
    source = "support.detect_inquiry_surge"
    start, _ = span(data)
    minutes = math.ceil((now - start).total_seconds() / 60)
    if minutes > SURGE_MAX_MINUTES:
        reason = "확인할 수 없음: 문의 급증은 최근 24시간만 볼 수 있어 이 구간은 건너뜀"
        return [RawFact("unavailable", None, source, None, reason)]
    payload = {"window_minutes": max(1, minutes)}
    result, error = await _ask(send, "support", "detect_inquiry_surge", payload, meta)
    if result is None or result.status != TaskStatus.COMPLETED:
        return [_unavailable(source, result, error)]
    output = result.output or {}
    at = _parse_time(output.get("evaluated_at")) or now
    facts = []
    for surge in output.get("surges") or []:
        topic, count = surge.get("topic_code"), surge.get("count")
        if not topic or not isinstance(count, int):
            continue
        ratio = surge.get("ratio")
        times = f", 평소의 {ratio}배" if isinstance(ratio, int | float) else ""
        summary = (
            f"문의 급증 {_iso(at)}: {topic} 최근 {payload['window_minutes']}분 {count}건{times}"
        )
        facts.append(RawFact("inquiry_surge", at, source, f"topic:{topic}", summary))
    return facts


async def past_incidents(data: RootCauseInput, meta: RequestMeta, send: SendFn) -> list[RawFact]:
    source = "memory.card_search"
    query = " ".join(p for p in (data.component, data.metric, "장애 원인") if p)
    payload = {"query": query, "kind": ["incident"], "top_k": PAST_INCIDENTS}
    result, error = await _ask(send, "memory", "card_search", payload, meta)
    if result is None or result.status != TaskStatus.COMPLETED:
        return [_unavailable(source, result, error)]
    facts = []
    for hit in (result.output or {}).get("hits") or []:
        card = hit.get("card") or {}
        if hit.get("low_confidence") or card.get("context_id") == data.context_id:
            continue  # 확신도 낮은 카드는 근거로 쓰지 않고, 이 사건 자신은 지난 사건이 아니다
        if card.get("candidate"):
            continue  # 사람 확인 전 후보 카드도 근거로 쓰지 않는다
        title = str(card.get("title") or "").strip()
        if not title:
            continue
        created = _parse_time(card.get("created_at"))
        when = f" ({_iso(created)})" if created else ""
        summary = f"지난 비슷한 사건{when}: {title}"
        facts.append(RawFact("past_incident", created, source, card.get("card_id"), summary))
    return facts


async def collect(
    data: RootCauseInput,
    meta: RequestMeta,
    *,
    send: SendFn | None = None,
    now: datetime | None = None,
) -> list[RawFact]:
    """네 출처를 함께 묻는다. 한 곳이 실패해도 나머지로 계속한다."""
    send = send or call_agent
    moment = now or datetime.now(UTC)
    groups = await asyncio.gather(
        deploys(data, meta, send),
        notices(data, meta, send),
        inquiry_surges(data, meta, send, moment),
        past_incidents(data, meta, send),
    )
    return [fact for group in groups for fact in group]

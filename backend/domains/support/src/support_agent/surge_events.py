"""문의 급증 이벤트 발행: 문의가 들어올 때마다 그 분류가 몰렸는지 보고, 몰렸으면 한 번 알린다.

- 급증 판정은 `insights.detect_surge`(코드, LLM 없음). 기준은 `surge_settings` 한 곳이다.
- 같은 분류는 창 길이 동안 한 번만 내보낸다(문의가 더 들어와도 다시 내보내지 않는다). 상태는
  프로세스 메모리라 재시작하면 지워지지만, 같은 창이면 멱등 키가 같아 받는 쪽에서 한 번만 처리된다.
- 알리기만 한다. 발행이 실패해도 문의 접수는 막지 않는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import AgentRequest
from ax_contracts.schemas.event import SupportInquirySurgeData
from support_agent import events, insights, surge_settings
from support_agent.store import InquiryStore

logger = logging.getLogger("ax.support.surge")
_last_emitted: dict[tuple[str, str], datetime] = {}  # (회사, 분류 코드) → 마지막으로 내보낸 때


def reset() -> None:
    _last_emitted.clear()


async def check_and_emit(
    request: AgentRequest, store: InquiryStore, now: datetime | None = None
) -> list[str]:
    """몰린 분류마다 이벤트를 한 번 내보내고, 내보낸 분류 코드를 돌려준다."""
    if not surge_settings.enabled():
        return []
    try:
        return await _check(request, store, now or datetime.now(UTC))
    except Exception:
        logger.warning("문의 급증 확인 실패", exc_info=True)
        return []


async def _check(request: AgentRequest, store: InquiryStore, now: datetime) -> list[str]:
    window = surge_settings.window_minutes()
    report = insights.detect_surge(
        store,
        now,
        window_minutes=window,
        baseline_hours=surge_settings.BASELINE_HOURS,
        min_count=surge_settings.min_count(),
        ratio=surge_settings.RATIO,
        baseline_method=surge_settings.BASELINE_METHOD,  # type: ignore[arg-type]
    )
    window_start = (now - timedelta(minutes=window)).replace(second=0, microsecond=0)
    emitted: list[str] = []
    for surge in report.surges:
        slot = (request.meta.tenant_id, surge.topic_code)
        last = _last_emitted.get(slot)
        if last is not None and now < last + timedelta(minutes=window):
            continue  # 이 분류는 이 창에서 이미 알렸다
        data = SupportInquirySurgeData(
            topic_code=surge.topic_code,
            count=surge.count,
            window_minutes=window,
            window_start=window_start,
            baseline_per_window=surge.baseline_per_window,
            ratio=surge.ratio,
            inquiry_ids=surge.sample_inquiry_ids,
            triage_reason=insights.triage_reason_in_window(  # type: ignore[arg-type]
                store, surge.topic_code, now, window_minutes=window
            ),
        )
        await events.emit_inquiry_surge(request, data)
        _last_emitted[slot] = now
        emitted.append(surge.topic_code)
    return emitted

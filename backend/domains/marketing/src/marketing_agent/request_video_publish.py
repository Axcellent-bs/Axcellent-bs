"""의뢰 영상 게시 → 데이터 성과 측정 등록 (11/23, 게시 → 성과 → 다음 기획).

의뢰(`wr_*`) 홍보 영상 초안이 게시되면(`published`) 데이터 `video_performance`에 그 게시물을
등록한다. 캠페인 경로(`campaign._register_video`)와 같은 방식이다. 등록 실패는 게시를 바꾸지
않고 경고만 남긴다.

- 대상: 게시 대기 건의 초안이 brief 초안이고 초안 context_id가 `ctx_wr_`로 시작 (의뢰 영상,
  `prepare_video_draft_from_plan`이 만든 것). source_ref = 기획 제안 번호
- 넘기는 값: campaign_id = 의뢰 번호(`wr_…`), video_url = permalink, proposal_id = source_ref,
  test=false
- 자동 게시(media_publish)와 사람이 직접 올리고 permalink를 넣은 경로(manual) 둘 다
  (`publisher.after_published`가 부른다)
- 같은 게시물은 한 번만: 결과에 `performance.registered`가 있으면 다시 부르지 않는다 (데이터 쪽도
  같은 게시물 키면 그대로)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import VideoPerformanceOutput

_log = logging.getLogger(__name__)

DATA = "data"
SKILL = "video_performance"
CONTEXT_PREFIX = "ctx_wr_"
REQUEST_ID = re.compile(r"^wr_[A-Za-z0-9_-]{1,60}$")
DEADLINE = timedelta(seconds=30)
Send = Callable[[str, AgentRequest], Awaitable[AgentResult]]


def request_id_of(context_id: str | None) -> str | None:
    """의뢰 context_id(`ctx_wr_…`) → 의뢰 번호(`wr_…`). 아니면 None."""
    if not context_id or not context_id.startswith(CONTEXT_PREFIX):
        return None
    rid = context_id.removeprefix("ctx_")
    return rid if REQUEST_ID.match(rid) else None


def _drafts() -> Any:
    from marketing_agent.handlers import STATE  # 게시 대기 건의 초안 저장소 (서버 하나에 하나)

    return STATE.sns


async def register_published(
    row: Any, *, drafts: Any | None = None, send: Send | None = None
) -> dict[str, Any] | None:
    """게시된 건이 의뢰 영상이면 데이터에 등록한다. 의뢰 영상이 아니면 None (아무것도 안 함)."""
    permalink = row.result.get("permalink") if isinstance(row.result, dict) else None
    store = drafts if drafts is not None else _drafts()
    draft = store.get(row.post_draft_id) if store is not None else None
    if draft is None or draft.source_kind != "brief":
        return None
    rid = request_id_of(draft.context_id)
    if rid is None:
        return None
    if not permalink:
        _log.warning("의뢰 %s 영상 등록 안 됨: 게시물 주소(permalink) 없음", rid)
        return {"registered": False, "warning": "게시물 주소(permalink)가 없어 등록하지 못함"}
    if send is None:
        from ax_agent_template.client import call_agent as send
    meta = RequestMeta(
        context_id=draft.context_id,
        tenant_id=row.tenant_id,
        token="",  # call_agent가 마케팅 토큰으로 바꿔 보낸다
        deadline=datetime.now(UTC) + DEADLINE,
        idempotency_key=f"{rid}:{SKILL}:{row.idempotency_key}"[:200],
        requester="marketing",
    )
    sent = AgentRequest(meta=meta, skill=SKILL, input={
        "campaign_id": rid, "video_url": permalink, "proposal_id": draft.source_ref, "test": False,
    })  # fmt: skip
    try:
        answer = await send(DATA, sent)
        out = VideoPerformanceOutput.model_validate(answer.output)
    except Exception as exc:  # noqa: BLE001 - 등록 실패가 이미 게시된 건을 바꾸지 않는다
        _log.warning("의뢰 %s 영상 등록 실패: %s", rid, type(exc).__name__)
        why = "영상 등록 실패(데이터에 닿지 못하거나 답이 이상함)"
        return {"registered": False, "warning": why}
    if answer.status != TaskStatus.COMPLETED or out.video_id is None:
        reason = out.reason or answer.outcome or answer.status.value
        _log.warning("의뢰 %s 영상 등록 안 됨: %s", rid, reason)
        return {"registered": False, "warning": f"영상 등록 안 됨: {reason}"}
    return {"registered": True, "campaign_id": rid, "video_id": out.video_id,
            "tracking": out.tracking}  # fmt: skip


async def on_published(row: Any) -> None:
    """`publisher.after_published` 기본 훅: 등록하고 결과를 게시 결과에 `performance`로 남긴다."""
    from marketing_agent.sns_publish import publisher

    done = row.result.get("performance") if isinstance(row.result, dict) else None
    if isinstance(done, dict) and done.get("registered"):
        return  # 같은 게시물은 한 번만
    got = await register_published(row)
    if got is not None:
        publisher.store().patch_result(row.idempotency_key, {"performance": got})

"""개인정보 삭제 skill: 고객 요청 삭제와 3년 보유기간 삭제.

- `preview_data_purge`(읽기): 무엇이 지워지고 무엇이 남는지 미리 본다.
- `purge_customer_data`(쓰기, 사람 승인 C): 삭제를 요청한 고객의 문의 원문을 `[삭제됨]`으로 바꾼다.
  안전 관문 `approve_data_deletion`이 늘 사람에게 보류한다. 처음 부르면 `held`로 돌아오고,
  사람이 승인한 뒤 같은 키로 다시 부르면 집행한다. 기한 안에 결정이 없으면 자동 거절된다.
  처리 중인 문의와 클레임이 이어진 문의는 보존 의무로 남기고 `retained_until`을 알린다.
- `purge_expired_inquiries`(쓰기 A): 받은 지 3년이 지난 문의 원문을 지운다. 개인정보를 3년을 넘겨
  들고 있지 않게 하려고 대시보드가 하루에 한 번 부른다. 상태·클레임에 상관없이 지운다.
- 행은 지우지 않고 원문 필드만 치환한다(개인정보 감사 기록이 문의 ID로 묶여 있다). 감사 기록
  (`retention_purge`)에는 기준·건수·실행자 역할만 남기고 문의 내용은 담지 않는다.
- 클레임 서비스의 클레임 기록(구매자 이름 등)은 이 skill의 범위가 아니다(클레임 서비스 몫, 후속).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import claim_handlers, retention
from support_agent import handlers as h
from support_agent.purge_schemas import (
    PreviewPurgeInput,
    PurgeCustomerDataInput,
    PurgeExpiredInput,
    PurgeItem,
    PurgeMode,
    PurgeReport,
)

logger = logging.getLogger("ax.support.purge")


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _report(mode: PurgeMode, plans: list[retention.Plan], applied: bool) -> dict[str, Any]:
    items = [
        PurgeItem(
            inquiry_id=p.inquiry_id,
            action=p.action,  # type: ignore[arg-type]
            reasons=list(p.reasons),
            purged=dict(p.purged),
            retained_until=p.retained_until,
        )
        for p in plans
    ]
    return PurgeReport(
        mode=mode,
        items=items,
        purge_count=sum(1 for p in plans if p.action == "purge"),
        retained_count=sum(1 for p in plans if p.action == "blocked" and p.retained_until),
        applied=applied,
    ).model_dump(mode="json")


async def _claimed_ids(request: AgentRequest) -> set[int] | None:
    """클레임이 이어진 문의 번호. 클레임 서비스가 다른 프로세스에 있으면 거기서 읽는다.

    읽지 못하면 None이다. 클레임 연결(보존 의무)을 모르고 지우면 되돌릴 수 없어서, 호출한 쪽은
    이때 지우지 않는다.
    """
    ids = {c.conversation_id for c in claim_handlers.CLAIMS.list() if c.conversation_id is not None}
    try:
        reply = await h.SEND(
            "support", AgentRequest(meta=h._peer_meta(request), skill="list_claims", input={})
        )
    except Exception:  # noqa: BLE001 - 못 읽으면 지우지 않는다
        logger.warning("클레임 서비스에서 클레임을 읽지 못했다", exc_info=True)
        return None
    if reply.status != TaskStatus.COMPLETED:
        return None
    for claim in reply.output.get("items", []):
        conversation_id = claim.get("conversation_id")
        if isinstance(conversation_id, int):
            ids.add(conversation_id)
    return ids


def _claims_unavailable(request: AgentRequest) -> AgentResult:
    return error_result(
        request.meta,
        TaskStatus.FAILED,
        "claims_unavailable",
        "클레임 연결을 확인하지 못해 지우지 않았다 (보존 의무가 있는 문의를 지울 수 없다)",
    )


async def preview_data_purge(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, PreviewPurgeInput)
    if isinstance(parsed, AgentResult):
        return parsed
    if parsed.mode == "expired":
        plans = retention.plan_expired(h.STORE, datetime.now(UTC), False, _stamp())
        return h._done(request, _report("expired", plans, False), outcome="purge_preview")
    if parsed.inquiry_id is None:
        return error_result(
            request.meta, TaskStatus.FAILED, "invalid_input", "삭제를 요청한 문의 번호가 필요하다"
        )
    claimed = await _claimed_ids(request)
    if claimed is None:
        return _claims_unavailable(request)
    plans = retention.plan_customer_request(
        h.STORE, claim_handlers.CLAIMS, parsed.inquiry_id, claimed, False, _stamp()
    )
    if len(plans) == 1 and plans[0].action == "not_found":
        return h._not_found(request, parsed.inquiry_id)
    return h._done(request, _report("customer_request", plans, False), outcome="purge_preview")


@h._caller_meta
async def purge_customer_data(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, PurgeCustomerDataInput)
    if isinstance(parsed, AgentResult):
        return parsed
    claimed = await _claimed_ids(request)
    if claimed is None:
        return _claims_unavailable(request)
    preview = retention.plan_customer_request(
        h.STORE, claim_handlers.CLAIMS, parsed.inquiry_id, claimed, False, _stamp()
    )
    if len(preview) == 1 and preview[0].action == "not_found":
        return h._not_found(request, parsed.inquiry_id)
    to_purge = sum(1 for p in preview if p.action == "purge")
    if to_purge == 0:  # 지울 것이 없으면 사람에게 승인을 부탁하지 않는다
        report = _report("customer_request", preview, False)
        return h._done(request, report, outcome="nothing_to_purge")

    async def apply() -> tuple[dict[str, Any], bool]:
        plans = retention.plan_customer_request(
            h.STORE, claim_handlers.CLAIMS, parsed.inquiry_id, claimed, True, _stamp()
        )
        return _report("customer_request", plans, True), False

    return await h._write(
        request,
        "approve_data_deletion",  # 되돌릴 수 없어 늘 사람이 정한다 (#194)
        f"request:{parsed.inquiry_id}",
        {
            "inquiry_id": parsed.inquiry_id,
            "summary": f"고객 요청 개인정보 삭제 (문의 {to_purge}건)",
        },
        apply,
    )


@h._caller_meta
async def purge_expired_inquiries(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, PurgeExpiredInput)
    if isinstance(parsed, AgentResult):
        return parsed
    now = datetime.now(UTC)
    preview = retention.plan_expired(h.STORE, now, False, _stamp())
    if not preview:
        return h._done(request, _report("expired", [], False), outcome="nothing_expired")

    async def apply() -> tuple[dict[str, Any], bool]:
        plans = retention.plan_expired(h.STORE, datetime.now(UTC), True, _stamp())
        return _report("expired", plans, True), False

    return await h._write(
        request,
        "purge_expired_inquiries",
        f"expired:{now.date().isoformat()}",
        {"count": len(preview)},
        apply,
    )


PURGE_HANDLERS = {
    "preview_data_purge": preview_data_purge,
    "purge_customer_data": purge_customer_data,
    "purge_expired_inquiries": purge_expired_inquiries,
}

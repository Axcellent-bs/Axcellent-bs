"""skill `request_brief_publish`: 준비된 미디어 초안의 "게시 요청"(C등급 승인 대기 건)을 만든다.

화면의 "게시 요청" 버튼이 부른다. 입력은 기획안 번호뿐이다. 게시할 값(초안 id·파일 참조·해시·
캡션·종류)은 호출자가 보내지 않고 **서버가 보관본에서 읽는다**: 넘김 기록의 `prepared`(만든 요청
초안과 일치하는 것만)와 SNS 초안 저장소의 초안. 브라우저는 캡션도 파일도 바꿀 수 없다.

하는 일은 기존 `publish_sns`를 그대로 부르는 것까지다. `publish_sns`는 C등급이라 항상 사람 승인이
필요하고(`execute()` → 보류 + `review_id`, 승인 대기 10분 만료), 사람이 승인 화면에서 "진행"을
눌러야만 이어서 Instagram 게시를 한다. 이 skill은:
- 승인하지 않는다. 승인 이벤트·폴링은 기존 구독자만 처리한다(여기서 건드리지 않는다).
- 안전 점검(AI 표시·금지 문구·숫자 출처·규격)을 다시 돌려 통과한 값만 보낸다(우회·완화 없음).
- 24시간 게시 상한(기본 45)·중단 상태·어댑터 설정 확인은 `publish_sns` 안에서 그대로 일어난다.
- 캐러셀은 이번 범위 밖이다(`media_kind_unsupported`).

멱등: `context_id`를 기획안에서만 정하고(`ctx_brief_<번호>`) 내용 해시가 키에 들어가므로 누가 몇 번
불러도 같은 보관본은 같은 키다. 이미 대기·게시·거절된 건은 `already_requested`로 상태만 돌려주고
`publish_sns`를 다시 부르지 않는다. 거절·만료된 건은 같은 키라 이 skill로 다시 요청하지 않는다.

조회에는 `sns_handoff.publish`(`publish_state`)로 같은 상태를 낸다. 승인 대기 저장소에 이미 있는
값만 읽는다(지어내지 않음).

사람 재요청(`retry=true`): 승인 마감(10분)이 지나 자동 거절된 건(`review_timeout`)만, 사람이 누르는
"다시 요청"만 새 승인 건을 만든다. 새 멱등 키에 시도 번호(`:r<n>`)가 들어가고 `publish_sns`는 그대로
(24시간 상한·AI 표시·점검 재실행·항상 승인 대기)이며 자동 승인은 없다. 사람이 거절한 건·거절/만료를
구분할 수 없는 옛 기록·같은 내용이 이미 게시됐거나 다른 승인 대기가 있는 경우는 코드로 거부한다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent import sns
from marketing_agent.brief_handoff import prepared_of
from marketing_agent.brief_media import context_id_for
from marketing_agent.briefs import BriefStorageError, BriefStore, SnsHandoffRecord
from marketing_agent.sns import SnsStore
from marketing_agent.sns_publish import pending as pend
from marketing_agent.sns_publish import publisher

ACTION = "request_brief_publish"
SUPPORTED_KINDS = ("image", "reel")
KIND_KO = {"image": "이미지", "reel": "릴스(영상)"}
PendingGet = Callable[[str], pend.PendingPost | None]

# 대기 건 상태 → 화면에 보이는 상태
_STATUS = {
    pend.HELD: "held",  # 승인 대기
    pend.APPROVED: "publishing",  # 승인됨, 게시 중
    pend.PUBLISHING: "publishing",
    pend.PUBLISHED: "published",
    pend.REJECTED: "rejected",  # 사람 거절(만료는 failure_code로 가려 "expired"로 낸다)
    pend.FAILED: "failed",
}
MAX_ATTEMPTS = 20  # 같은 내용의 재요청 기록을 따라가는 상한(무한 루프 방지)


class RequestBriefPublishInput(AxModel):
    proposal_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    # 만료로 닫힌 건을 사람이 다시 요청한다. 만료가 아니면 retry_* 코드로 거부한다
    retry: bool = False


def publish_input_for(record: SnsHandoffRecord | None, store: SnsStore) -> tuple[dict | None, str]:
    """보관본에서 `publish_sns` 입력을 만든다. (입력, "") 또는 (None, 막힌 이유 코드)."""
    if record is None or record.status != "handed":
        return None, "not_handed"
    prepared = prepared_of(record)
    if not prepared or prepared.get("stage") != "draft_ready":
        return None, "not_prepared"
    check = prepared.get("check") or {}
    if check.get("verdict") != "approved" or check.get("publishable") is not True:
        return None, "check_not_passed"
    kind = prepared.get("media_kind")
    if kind not in SUPPORTED_KINDS:
        return None, "media_kind_unsupported"
    draft = store.get(str(prepared.get("post_draft_id") or ""))
    if draft is None:
        return None, "draft_missing"
    ref, sha = prepared.get("asset_ref"), prepared.get("asset_sha256")
    caption_ok = hashlib.sha256(draft.caption.encode()).hexdigest() == draft.caption_sha256
    if (
        not (ref and sha)
        or draft.media_kind != kind
        or draft.asset_ref != ref
        or draft.asset_sha256 != sha
        or not caption_ok
    ):
        return None, "draft_changed"
    head = draft.caption.strip().replace("\n", " ")[:80]
    summary = (
        f"기획안 {record.proposal_id} {KIND_KO[kind]} Instagram 게시 요청 "
        f"(AI 표시 포함, 사람 승인 필요): {head}"
    )[:200]
    return {
        "post_draft_id": draft.post_draft_id,
        "asset_ref": ref,
        "asset_sha256": sha,
        "caption": draft.caption,
        "media_kind": kind,
        "share_to_feed": bool(draft.platform_options.get("share_to_feed", True)),
        "summary": summary,
    }, ""


def sha_for(data: dict) -> str:
    return publisher.content_sha(
        data["caption"], data["asset_sha256"], data["media_kind"], data["share_to_feed"], True
    )


def key_for(proposal_id: str, data: dict, attempt: int = 0) -> str:
    """`publish_sns`가 쓰는 멱등 키와 같은 값(내용 해시 포함). `attempt`는 사람 재요청 번호."""
    return publisher.idempotency_key(
        context_id_for(proposal_id), data["post_draft_id"], sha_for(data), attempt
    )


def chain_of(proposal_id: str, data: dict, get: PendingGet | None = None) -> list[pend.PendingPost]:
    """같은 내용의 요청 기록을 시도 순서(0, 1, ...)로. 없으면 빈 목록. 저장소 오류는 올린다."""
    getter = get or publisher.store().get
    rows: list[pend.PendingPost] = []
    for attempt in range(MAX_ATTEMPTS):
        row = getter(key_for(proposal_id, data, attempt))
        if row is None:
            break
        rows.append(row)
    return rows


_IN_FLIGHT = (pend.HELD, pend.APPROVED, pend.PUBLISHING)


def rejected_cause(row: pend.PendingPost) -> str | None:
    """거절 건의 원인: human | unknown(만료와 구분 못 하던 옛 기록). 만료·거절 아님은 None."""
    if row.status != pend.REJECTED or row.failure_code == publisher.REVIEW_TIMEOUT:
        return None
    return "human" if row.result.get("cause") == "human" else "unknown"


def retry_problem(
    chain: list[pend.PendingPost], same_content: list[pend.PendingPost]
) -> str | None:
    """사람 재요청을 거부하는 코드. 허용이면 None(마지막 건이 만료로 닫혔고 다른 건이 없다)."""
    if any(r.status == pend.PUBLISHED for r in same_content):
        return "retry_already_published"
    if any(r.status in _IN_FLIGHT for r in same_content):
        return "retry_other_held"
    latest = chain[-1] if chain else None
    if latest is None or latest.status != pend.REJECTED:
        return "retry_not_expired"
    cause = rejected_cause(latest)
    if cause == "human":
        return "retry_rejected_by_human"
    if cause == "unknown":
        return "retry_cause_unknown"
    return None


def _same_content(
    sha: str, chain: list[pend.PendingPost], injected: bool
) -> list[pend.PendingPost]:
    """같은 내용의 모든 대기 건. 저장소를 주입받은 조회(`get`)에서는 자기 기록만 본다."""
    return list(chain) if injected or not chain else publisher.store().with_content_sha(sha)


def publish_state(
    record: SnsHandoffRecord | None, store: SnsStore, get: PendingGet | None = None
) -> dict[str, Any] | None:
    """조회용 게시 요청 상태. 미디어 초안이 없으면 None.

    `status`: not_requested | held(승인 대기) | publishing(승인됨·게시 중) | published |
    rejected(사람 거절; `rejected_cause`는 human, 옛 기록이라 만료와 구분 못 하면 unknown) |
    expired(마감이 지나 자동 거절, 사람이 다시 요청 가능) | failed | unknown(저장소 읽기 실패).
    `can_request`가 참일 때만 "게시 요청", `can_retry`가 참일 때만 "다시 요청" 버튼을 둔다.
    카드 안 승인 패널용: `expires_at`(안전 관문 마감, 모르면 None)·`preview_url`/`preview_kind`·
    `ai_label`·`proposal_id`. 값은 모두 저장소·보관본에서 읽은 것이다.
    """
    if record is None or record.status != "handed" or prepared_of(record) is None:
        return None
    data, reason = publish_input_for(record, store)
    base: dict[str, Any] = {
        "status": "not_requested",
        "can_request": False,
        "reason_code": reason or None,
        "review_id": None,
        "failure_code": None,
        "permalink": None,
        "caption": None,
        "proposal_id": record.proposal_id,
        "expires_at": None,
        "preview_url": None,
        "preview_kind": None,
        "ai_label": None,
        "rejected_cause": None,
        "attempt": 0,
        "can_retry": False,
        "retry_reason_code": None,
    }
    if data is None:
        return base
    base["caption"] = data["caption"]  # 게시될 글(확인 단계에 보여 준다). 화면은 고칠 수 없다
    prepared = prepared_of(record) or {}
    kind = data["media_kind"]
    base["preview_url"] = f"/api/dashboard/marketing/briefs/{record.proposal_id}/media/preview"
    base["preview_kind"] = "video" if kind == "reel" else "image"
    base["ai_label"] = prepared.get("label_text") or sns.ai_label_text(kind)
    try:
        chain = chain_of(record.proposal_id, data, get)
        same = _same_content(sha_for(data), chain, get is not None)
    except pend.PendingStorageError:
        return base | {"status": "unknown", "reason_code": "storage_error"}
    if not chain:
        return base | {"can_request": True}
    row = chain[-1]
    status = _STATUS.get(row.status, "failed")
    cause = rejected_cause(row)
    if row.status == pend.REJECTED and cause is None:
        status = "expired"
    problem = retry_problem(chain, same)
    return base | {
        "status": status,
        "review_id": row.review_id if row.status == pend.HELD else None,
        "failure_code": row.failure_code,
        "permalink": row.result.get("permalink") if row.status == pend.PUBLISHED else None,
        "expires_at": row.result.get("review_expires_at") if row.status == pend.HELD else None,
        "rejected_cause": cause,
        "attempt": len(chain) - 1,
        "can_retry": problem is None,
        "retry_reason_code": problem if status in ("expired", "rejected") else None,
    }


def _failed(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


async def request_brief_publish(
    request: AgentRequest,
    *,
    briefs: BriefStore,
    store: SnsStore,
    publish: Callable[..., Any] = publisher.publish_sns,
) -> AgentResult:
    try:
        data = RequestBriefPublishInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    pid = data.proposal_id
    try:
        if briefs.existing(pid) is None:
            return _failed(request, "brief_not_found")
        record = briefs.handoff(pid)
    except BriefStorageError:
        return _failed(request, "storage_error")

    publish_input, reason = publish_input_for(record, store)
    if publish_input is None:
        return _failed(request, reason)
    # 요청 직전에 점검을 다시 돌린다. 통과한 값만 승인을 받으러 간다(우회·완화 없음)
    checked = await sns.check_sns_post(
        request.model_copy(
            update={
                "skill": "check_sns_post",
                "input": {
                    "post_draft_id": publish_input["post_draft_id"],
                    "caption": publish_input["caption"],
                    "is_ai_generated": True,
                },
            }
        ),
        store,
    )
    check = checked.output or {}
    if check.get("verdict") != "approved" or check.get("publishable") is not True:
        return _failed(request, "check_not_passed", problems=list(check.get("problems") or [])[:20])

    try:
        chain = chain_of(pid, publish_input)
        same = _same_content(sha_for(publish_input), chain, False)
    except pend.PendingStorageError:
        return _failed(request, "storage_error")
    evidence = Evidence(
        queries=[ACTION],
        data_summary=f"기획안 {pid} 게시 {'재요청' if data.retry else '요청'}",
        collected_at=datetime.now(UTC),
        ref_ids=[pid, publish_input["post_draft_id"]],
    )
    attempt = 0
    if data.retry:  # 사람이 누른 "다시 요청": 만료로 닫힌 건만. 그 밖은 코드로 거부한다
        problem = retry_problem(chain, same)
        if problem is not None:
            return _failed(request, problem, publish=publish_state(record, store))
        attempt = len(chain)
    elif chain:  # 이미 요청한 건. 다시 부르지 않고 상태만 돌려준다
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="already_requested",
            output={"publish": publish_state(record, store)},
            evidence=evidence,
        )

    meta = request.meta.model_copy(update={"context_id": context_id_for(pid)})
    sent = request.model_copy(update={"skill": "publish_sns", "input": publish_input, "meta": meta})
    # 시도 번호는 재요청에서만 넘긴다(첫 요청은 옛 호출 모양 그대로)
    result = await (publish(sent, store, attempt=attempt) if attempt else publish(sent, store))
    output = dict(result.output or {})
    if (
        result.outcome == "held"
    ):  # 화면의 남은 시간용 마감 시각(읽기 전용 조회, 실패해도 요청은 그대로)
        held_row = publisher.store().by_review(str(output.get("review_id") or ""))
        if held_row is not None:
            await publisher.remember_expiry(held_row)
    output["publish"] = publish_state(record, store)
    held = result.outcome == "held"
    return result.model_copy(
        update={
            "meta": request.meta,
            "status": TaskStatus.COMPLETED if held else result.status,
            "reason_code": None if held else result.reason_code,
            "outcome": "requested" if held else result.outcome,
            "output": output,
        }
    )


__all__ = [
    "ACTION",
    "RequestBriefPublishInput",
    "publish_input_for",
    "publish_state",
    "request_brief_publish",
]

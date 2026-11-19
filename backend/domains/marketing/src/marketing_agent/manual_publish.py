"""수동 게시 스킬: `get_manual_publish_package`(읽기)·`complete_manual_publish`(쓰기, 안전 관문).

자동 게시 스위치(`publish_control.py`)가 꺼져 있을 때 사람이 직접 올리고
**게시물 링크를 입력해야만**
게시 완료로 닫히는 흐름이다. 자동도 수동도 쓸 수 있고, 어느 쪽도 무작정 막지 않는다.

흐름
1. 자동 게시가 꺼진 상태에서 `publish_sns` 요청이 사람 승인(C등급)을 받으면 거절하지 않고
   `approved`로 둔다(`result.manual_pending=true`, 응답 `manual_available`·`manual_pending`).
   새 상태 값·새 전이는 없다(`pending.TRANSITIONS` 그대로).
2. `get_manual_publish_package`가 복사용 패키지(승인된 캡션 전문·해시태그·AI 표시 문구·
   미디어 정보)를 준다.
   승인할 때의 값(대기 건의 `params`)을 그대로 읽는다. 화면은 캡션도 파일도 바꿀 수 없다.
3. 사람이 Instagram 앱에서 올리고(AI 표시 켜기) 링크를 입력한다. `complete_manual_publish`가
   `execute()` 관문을 지나 `approved → publishing → published`(CAS)로 닫는다. `result`에
   `manual: true`, `permalink`, `published_at`, `posted_by`, `ai_label_review`가 남는다.

검증(거절하면 `published`로 넘기지 않는다): 링크가 비었거나 형식이 맞지 않음
(`sns_publish/permalink.py`),
AI 표시 확인(`ai_label_confirmed`)이 true가 아님, 대상 건이 `approved`·`manual_pending`이 아님,
같은 링크가 이미 다른 건에 쓰임.
**링크가 실제로 있는 게시물인지·우리 계정의 것인지는 확인하지 못한다**
(Instagram API를 부르지 않고 형식만 본다). 사람이 입력한 값이라는 것을 기록(`posted_by`)으로 남긴다.

`posted_by`는 대시보드 API가 사람 토큰에서 채워 보낸다(요청자가 dashboard-api일 때만
쓰기 가능, Agent Card).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field, StrictBool, ValidationError, field_validator

from ax_agent_template.execute import execute
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, InputReason, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent import sns
from marketing_agent.sns_publish import pending as pend
from marketing_agent.sns_publish import publisher
from marketing_agent.sns_publish.permalink import MAX_RAW, normalize_permalink

ACTION = "complete_manual_publish"
_FUTURE_SKEW = timedelta(minutes=5)
_HASHTAG = re.compile(r"(?<!\S)#[^\s#]+")


class GetManualPublishPackageInput(AxModel):
    post_draft_id: str = Field(min_length=1, max_length=128)


class CompleteManualPublishInput(AxModel):
    post_draft_id: str = Field(min_length=1, max_length=128)
    permalink: str = Field(
        default="", max_length=MAX_RAW
    )  # 필수: 비면 invalid_permalink로 거절한다
    ai_label_confirmed: StrictBool  # 사람이 앱에서 AI 표시를 켰음을 확인했다(true만 받는다)
    posted_by: str = Field(min_length=1, max_length=64)  # 사람 이름: 대시보드 API가 토큰에서 채움
    published_at: datetime | None = None  # 사람이 올린 시각(시각대 포함). 없으면 기록 시각

    @field_validator("published_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("published_at_needs_timezone")
        return value


class ManualPublishPackage(AxModel):
    """`get_manual_publish_package` 결과 `output`(`build_package`가 만든다)."""

    post_draft_id: str
    status: str
    manual_pending: bool
    caption: str
    caption_sha256: str
    hashtags: list[str]
    ai_label_text: str
    ai_label_in_caption: bool
    media_kind: str
    media: list[dict[str, Any]]
    share_to_feed: bool | None = None
    auto_publish_off: bool
    pause_reason: str | None = None
    permalink: str | None = None
    posted_by: str | None = None
    published_at: str | None = None
    permalink_verified: bool = False


def _failed(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    return _failed(
        request,
        "invalid_input",
        errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
    )


def _short_ref(ref: str) -> str:
    """화면에 내는 파일 위치: 마지막 두 구간(`med_.../labeled.jpg`). 서버 절대 경로는 안 낸다."""
    return "/".join(ref.replace("\\", "/").rstrip("/").split("/")[-2:])


def _refs(params: dict[str, Any]) -> list[tuple[str, str]]:
    if params.get("media_kind") == "carousel":
        return list(
            zip(params.get("asset_refs") or [], params.get("asset_sha256s") or [], strict=False)
        )
    ref = str(params.get("asset_ref") or "")
    return [(ref, str(params.get("asset_sha256") or ""))] if ref else []


def _pick(rows: list[pend.PendingPost]) -> pend.PendingPost | None:
    """수동 게시 대기 건(가장 최근) → 없으면 수동으로 게시된 건(가장 최근)."""
    waiting = [r for r in rows if r.status == pend.APPROVED and r.result.get("manual_pending")]
    if waiting:
        return waiting[-1]
    done = [r for r in rows if r.status == pend.PUBLISHED and r.result.get("manual")]
    return done[-1] if done else None


def build_package(row: pend.PendingPost) -> dict[str, Any]:
    """복사용 패키지. 대기 건에 저장된 승인 값만 쓴다. 비밀값·토큰·서버 경로는 들어 있지 않다."""
    params = row.params
    kind = str(params.get("media_kind") or "image")
    caption = str(params.get("caption") or "")
    label = sns.ai_label_text(kind)
    media = [
        {
            "index": index,
            "asset_ref": _short_ref(ref),
            "file_name": _short_ref(ref).rsplit("/", 1)[-1],
            "asset_sha256": sha,
        }
        for index, (ref, sha) in enumerate(_refs(params), start=1)
    ]
    return {
        "post_draft_id": row.post_draft_id,
        "status": "manual_pending" if row.status == pend.APPROVED else row.status,
        "manual_pending": row.status == pend.APPROVED and bool(row.result.get("manual_pending")),
        "caption": caption,
        "caption_sha256": hashlib.sha256(caption.encode()).hexdigest(),
        "hashtags": _HASHTAG.findall(caption),
        "ai_label_text": label,
        "ai_label_in_caption": label in caption,
        "media_kind": kind,
        "media": media,
        "share_to_feed": params.get("share_to_feed"),
        "auto_publish_off": bool(row.result.get("auto_publish_off")),
        "pause_reason": row.result.get("pause_reason"),
        "permalink": row.result.get("permalink") if row.status == pend.PUBLISHED else None,
        "posted_by": row.result.get("posted_by"),
        "published_at": row.result.get("published_at"),
        # 이 시스템이 하지 못하는 일: 링크의 진위·계정 소유는 확인하지 않는다
        "permalink_verified": False,
    }


async def get_manual_publish_package(request: AgentRequest) -> AgentResult:
    try:
        data = GetManualPublishPackageInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    try:
        row = _pick(publisher.store().with_draft(data.post_draft_id))
    except pend.PendingStorageError:
        return _failed(request, "storage_error")
    if row is None:
        return _failed(request, "not_manual_pending")
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="package",
        output=ManualPublishPackage.model_validate(build_package(row)).model_dump(mode="json"),
    )


def permalink_in_use(permalink: str, *, except_key: str | None = None) -> bool:
    """정규화한 같은 링크가 이미 게시됐거나 기록 중인 다른 건에 있는가(자동 게시 건 포함)."""
    store = publisher.store()
    for status in (pend.PUBLISHED, pend.PUBLISHING):
        for row in store.with_status(status):
            if row.idempotency_key != except_key and (
                normalize_permalink(row.result.get("permalink")) == permalink
            ):
                return True
    return False


async def complete_manual_publish(request: AgentRequest, *, from_decision: Any) -> AgentResult:
    """`from_decision`은 `handlers._from_decision`(executed가 아니면 결과를 만든다)."""
    try:
        data = CompleteManualPublishInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    permalink = normalize_permalink(data.permalink)
    if permalink is None:
        return _failed(
            request, "invalid_permalink", reason="empty" if not data.permalink.strip() else "format"
        )
    if data.ai_label_confirmed is not True:
        return _failed(request, "ai_label_not_confirmed")
    now = datetime.now(UTC)
    if data.published_at is not None and data.published_at > now + _FUTURE_SKEW:
        return _failed(request, "published_at_in_future")
    published_at = (data.published_at or now).astimezone(UTC).isoformat()

    try:
        rows = publisher.store().with_draft(data.post_draft_id)
        row = _pick([r for r in rows if r.status == pend.APPROVED])
        if row is None:
            states = {r.status for r in rows}
            if not rows:
                return _failed(request, "not_found")
            if pend.PUBLISHED in states or pend.PUBLISHING in states:
                return _failed(request, "already_published")
            return _failed(request, "not_manual_pending", states=sorted(states))
        if permalink_in_use(permalink, except_key=row.idempotency_key):
            return _failed(request, "permalink_already_used")
    except pend.PendingStorageError:
        return _failed(request, "storage_error")

    evidence = Evidence(
        queries=[ACTION],
        data_summary=f"수동 게시 완료 기록 {data.post_draft_id} ({data.posted_by})",
        collected_at=now,
        ref_ids=[r for r in (data.post_draft_id, row.execution_id) if r],
    )
    # 키는 대기 건마다 하나라 같은 요청을 다시 보내도 같은 결정이다(멱등)
    key = f"{request.meta.context_id}:{ACTION}:{row.idempotency_key}"
    decision = await execute(
        ACTION,
        {
            "post_draft_id": data.post_draft_id,
            "permalink": permalink,
            "posted_by": data.posted_by,
            "ai_label_confirmed": True,
            "summary": f"수동 게시 완료 기록: {data.post_draft_id} → instagram",
        },
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = from_decision(request, decision, evidence)
    if blocked:
        return blocked
    try:
        fresh = publisher.store().get(row.idempotency_key)
        if fresh is None or fresh.status != pend.APPROVED or not fresh.result.get("manual_pending"):
            return _failed(request, "not_manual_pending")
        if permalink_in_use(permalink, except_key=row.idempotency_key):  # 관문 사이에 쓰였다
            return _failed(request, "permalink_already_used")
        won = publisher.finish_manual(
            fresh,
            {"permalink": permalink, "published_at": published_at, "posted_by": data.posted_by},
            data.posted_by,
        )
        if not won:  # 다른 요청이 먼저 닫았다
            return _failed(request, "already_published")
        closed = publisher.store().get(row.idempotency_key) or fresh
    except pend.PendingStorageError:
        return _failed(request, "storage_error")
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="published",
        output=publisher._output(closed)  # noqa: SLF001 - 자동 게시와 같은 응답 모양
        | {"execution_id": decision.execution_id, "post_draft_id": data.post_draft_id},
        evidence=evidence,
    )


__all__ = [
    "ACTION",
    "CompleteManualPublishInput",
    "GetManualPublishPackageInput",
    "ManualPublishPackage",
    "InputReason",
    "build_package",
    "complete_manual_publish",
    "get_manual_publish_package",
]

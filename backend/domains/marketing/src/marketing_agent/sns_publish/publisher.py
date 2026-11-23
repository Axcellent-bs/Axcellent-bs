"""`publish_sns` (C 등급, 항상 사람 승인 후 게시), 승인 구독자, 폴링 대비책, 복구.

흐름 (분석 A-3):
1. `publish_sns` 입력 → `content_sha`로 멱등 키를 만들고 `execute("publish_sns")`를 부른다.
2. `held`면 Task를 `input-required`로 돌려주고 `review_id`를 게시 대기 저장소에 기록한다.
3. 사람이 safety에서 승인하면 `safety.review_decided` 이벤트(구독자) 또는 폴링(대비책)이 온다.
   대기 건의 내용을 다시 계산해 키가 같은지 보고, 같으면 **같은 키로** `execute()`를 다시 불러
   `executed`(멱등 재생)를 받은 뒤에만 Instagram 어댑터를 부른다.
4. 게시는 `approved → publishing → published/failed`. `media_publish`는 성공 1회, 응답을 잃으면
   다시 보내지 않고 컨테이너 `status_code == PUBLISHED`인지 조회해 판정한다.

마케팅이 스스로 승인하는 경로는 없다(결정 API는 사람 토큰만). 로그에는 토큰·서명 URL·고객 원문을
남기지 않는다. 로그 종류 `sns_publish_attempt`·`sns_container_status`는 계약 `LogKind`에 없어 표준
로거 `ax.event`에만 남고 공통 로그 저장소에는 `execute_decided`·`result_sent`만 쌓인다
(계약은 추가만 하는 규칙이라 이 PR에서 건드리지 않았다).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field, StrictBool, ValidationError, model_validator

from ax_agent_template import events
from ax_agent_template.companies import canonical_company_id
from ax_agent_template.config import agent_token, agent_url, get_agent_name
from ax_agent_template.execute import execute
from ax_agent_template.logging import log_event
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    EventEnvelope,
    EventType,
    Evidence,
    InputReason,
    RequestMeta,
    SafetyReviewDecidedData,
    TaskStatus,
)
from ax_contracts.schemas.common import AxModel
from marketing_agent import sns_media
from marketing_agent.sns_publish.config import LimitsConfigError, SnsConfig
from marketing_agent.sns_publish.control import PublishControlError, read_control
from marketing_agent.sns_publish.instagram import (
    HALT_FAILURES,
    AdapterUnavailable,
    CarouselRequest,
    ContainerRequest,
    IgApiError,
    IgResponseLost,
    InstagramAdapter,
    PublishOutcome,
    classify,
    get_adapter,
)
from marketing_agent.sns_publish.pending import (
    APPROVED,
    FAILED,
    HELD,
    PUBLISHED,
    PUBLISHING,
    REJECTED,
    PendingPost,
    PendingStorageError,
    PendingStore,
)

ACTION = "publish_sns"
SOURCE = "marketing"
REVIEW_REJECTED = "review_rejected"  # 사람이 거절했다(옛 기록은 만료와 구분되지 않는다)
REVIEW_TIMEOUT = "review_timeout"  # 마감까지 결정이 없어 자동 거절됐다(사람 재요청 대상)
MANUAL_PENDING = "manual_publish_pending"  # 승인됨, 사람이 직접 올리고 링크를 기록하기를 기다린다
NOT_READY = "ig_not_ready"  # 9007/2207027
NOT_READY_RETRIES = 1
CAPTION_MAX = 2200
_log = logging.getLogger("ax.marketing.sns_publish")

DAILY_WINDOW = timedelta(hours=24)  # 이동 24시간 창
MediaKind = Literal["image", "reel", "carousel"]
_SHA = r"^[0-9a-f]{64}$"


class PublishSnsInput(AxModel):
    post_draft_id: str = Field(min_length=1)
    # 내용 주소 참조(경로·ID). 서명 URL은 어댑터가 만든다. 캐러셀은 아래 asset_refs를 쓴다
    asset_ref: str | None = Field(default=None, min_length=1)
    asset_sha256: str | None = Field(default=None, pattern=_SHA)
    # 캐러셀(이미지 2~10장): 각 장의 참조와 해시. 순서가 게시 순서이고 장마다 라벨을 입힌 파일이다
    asset_refs: list[str] | None = None
    asset_sha256s: list[Annotated[str, Field(pattern=_SHA)]] | None = None
    caption: str = Field(min_length=1, max_length=CAPTION_MAX)
    media_kind: MediaKind = "image"
    share_to_feed: StrictBool = True  # 릴스 전용이지만 항상 명시한다(기본값이 문서에 없음)
    is_ai_generated: Literal[True] = True  # 라벨은 끌 수 없다
    summary: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _media_shape(self) -> PublishSnsInput:
        if self.media_kind == "carousel":
            refs, shas = self.asset_refs or [], self.asset_sha256s or []
            if self.asset_ref is not None or self.asset_sha256 is not None:
                raise ValueError("carousel은 asset_refs·asset_sha256s만 쓴다")
            if not sns_media.CAROUSEL_MIN <= len(refs) <= sns_media.CAROUSEL_MAX:
                raise ValueError("carousel_item_count_out_of_range")
            if len(refs) != len(shas):
                raise ValueError("carousel_refs_shas_mismatch")
            if len(set(refs)) != len(refs) or not all(refs):
                raise ValueError("carousel_duplicate_or_empty_asset")
        else:
            if self.asset_ref is None or self.asset_sha256 is None:
                raise ValueError("asset_ref·asset_sha256 필요")
            if self.asset_refs is not None or self.asset_sha256s is not None:
                raise ValueError("asset_refs는 carousel만 쓴다")
        return self


class PublishSnsOutput(AxModel):
    """결과 `output`. 단계에 따라 일부는 비어 있다."""

    status: str
    execution_id: str | None = None
    review_id: str | None = None
    failure_code: str | None = None
    media_id: str | None = None
    permalink: str | None = None
    is_ai_generated_response: bool | None = None
    ai_label_confirmed: bool | None = None
    ai_label_check: str | None = None  # api_confirmed | api_mismatch | api_unavailable
    ai_label_warning: str | None = None  # ai_label_mismatch | _unverified | _missing
    ai_label_review: dict[str, Any] | None = None  # status, confirmed_by, confirmed_at
    provider: str | None = None
    item_count: int | None = None  # 캐러셀 장수
    asset_refs: list[str] | None = None  # 캐러셀 각 장의 참조(게시 순서)
    # 게시 성공 응답의 config_issue(CAPTION_NOT_ATTACHED 등). 있으면 사람이 앱에서 확인한다
    config_issue: str | None = None
    reconcile_needed: bool | None = None
    limit_check_failed: bool | None = None  # 계정 한도 조회 실패(로컬 기록만으로 판단)
    pause_reason: str | None = None  # `publish_paused`일 때 사람이 적은 사유
    auto_publish_off: bool | None = None  # 자동 게시 스위치가 꺼져 있다(`publish_paused`)
    manual_available: bool | None = None  # 자동 게시가 꺼져 있어 사람이 직접 올릴 수 있다
    manual_pending: bool | None = None  # 승인됨·사람이 올린 뒤 링크를 기록하기를 기다린다
    manual: bool | None = None  # 사람이 직접 올린 게시(링크를 사람이 기록했다)
    posted_by: str | None = None  # 수동 게시를 기록한 사람
    published_at: str | None = None  # 사람이 올린 시각(입력 또는 기록 시각)


# -- 내용 해시·멱등 키
def content_sha(
    caption: str, asset_sha256: str, media_kind: str, share_to_feed: bool, is_ai_generated: bool
) -> str:
    """sha256(caption ‖ asset_sha ‖ media_kind ‖ share_to_feed ‖ is_ai_generated).

    구분자는 U+001F다. 필드 경계가 모호해져 다른 내용이 같은 해시가 되는 일을 막는다.
    """
    parts = (
        caption,
        asset_sha256,
        media_kind,
        str(share_to_feed).lower(),
        str(is_ai_generated).lower(),
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def params_sha(params: dict[str, Any]) -> str:
    return content_sha(
        params["caption"],
        params["asset_sha256"],
        params["media_kind"],
        params["share_to_feed"],
        params["is_ai_generated"],
    )


def idempotency_key(context_id: str, post_draft_id: str, sha: str, attempt: int = 0) -> str:
    """멱등 키. `attempt`는 사람이 만료 건을 다시 요청할 때만 1 이상이다(0이면 옛 키 그대로)."""
    base = f"{context_id}:{ACTION}:{post_draft_id}:{sha[:12]}"
    return f"{base}:r{attempt}" if attempt > 0 else base


# 승인 시점의 "지금 내용". 기본은 저장해 둔 값에서 로컬 자산 파일이 있으면 그 해시를 다시 계산한다.
ContentResolver = Callable[[PendingPost], dict[str, Any]]


def _carousel_resolver(current: dict[str, Any]) -> dict[str, Any]:
    """캐러셀: 각 장의 지금 해시를 다시 계산해 전체 해시를 새로 만든다(읽을 수 없으면 저장값)."""
    shas = list(current.get("asset_sha256s", []))
    for index, ref in enumerate(current.get("asset_refs", [])):
        try:
            shas[index] = sns_media.sha256_file(sns_media.resolve_asset(ref))
        except (sns_media.MediaPathError, OSError):
            continue  # 어댑터 호출 때 실패한다
    current["asset_sha256s"] = shas
    current["asset_sha256"] = sns_media.carousel_sha256(shas)
    return current


def default_resolver(row: PendingPost) -> dict[str, Any]:
    current = dict(row.params)
    if current.get("media_kind") == "carousel":
        return _carousel_resolver(current)
    path = Path(str(current.get("asset_ref", "")))
    try:
        if path.is_file():
            current["asset_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        pass  # 읽을 수 없으면 저장값 그대로. 어댑터 호출 때 실패한다
    return current


# -- 런타임 상태
class Runtime:
    def __init__(self) -> None:
        self.store: PendingStore | None = None
        self.config: SnsConfig | None = None
        self.resolver: ContentResolver = default_resolver
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self.subscriber = "off"  # off | on | disabled_no_redis
        self.poller = "off"  # off | on


RT = Runtime()

# 게시가 끝나면(published) 부르는 함수들: (게시 대기 행, media_id, 결과).
# handlers가 `sns.post_published` 이벤트를 outbox에 쌓으려고 등록한다(2026-11-23).
# 테스트 초기화(reset_runtime)로 지워지지 않는다
PUBLISHED_HOOKS: list[Callable[[PendingPost, str | None, dict[str, Any]], None]] = []


def reset_runtime() -> None:
    """테스트 사이에 대기 저장소·설정·주입 값을 비운다."""
    if RT.store is not None:
        RT.store.close()
    RT.__init__()


def store() -> PendingStore:
    if RT.store is None:
        RT.store = PendingStore()
    return RT.store


def config() -> SnsConfig:
    if RT.config is None:
        RT.config = SnsConfig.from_env()
    return RT.config


def runtime_status() -> dict[str, str]:
    return {"subscriber": RT.subscriber, "poller": RT.poller, "pending_db": store().path}


# -- 도우미
def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(row: PendingPost) -> Evidence:
    note = ""
    if row.result.get("reconcile_needed"):
        note = f" - 게시됐지만 사람 확인 필요(reconcile_needed: {row.result.get('config_issue')})"
    return Evidence(
        queries=["publish_sns"],
        data_summary=f"게시 대기 건 {row.post_draft_id} ({row.status}){note}",
        collected_at=_now(),
        ref_ids=[r for r in (row.post_draft_id, row.execution_id, row.review_id) if r],
    )


def _meta(row: PendingPost) -> RequestMeta:
    return RequestMeta(
        context_id=row.context_id,
        tenant_id=row.tenant_id,
        token="",  # execute()는 토큰을 쓰지 않는다(요청자는 에이전트 이름으로 채운다)
        deadline=_now() + timedelta(seconds=60),
        idempotency_key=row.idempotency_key,
    )


def _short_ref(ref: str) -> str:
    """승인 대기 큐 요약용 짧은 참조: 미디어 폴더 이름(`med_` 접두어 제외)."""
    parts = ref.replace("\\", "/").rstrip("/").split("/")
    name = parts[-2] if len(parts) >= 2 else parts[-1]
    return name.removeprefix("med_")[:16]


def _summary(params: dict[str, Any]) -> str:
    if params["media_kind"] == "carousel":
        refs = [_short_ref(r) for r in params["asset_refs"]]
        return (
            f"SNS 캐러셀 게시 요청({len(refs)}장): {params['post_draft_id']} → instagram "
            f"[{', '.join(refs)}]"
        )
    return f"SNS 게시 요청: {params['post_draft_id']} → instagram/{params['media_kind']}"


def _safety_params(params: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    carousel = (
        {"item_count": len(params["asset_refs"]), "asset_refs": list(params["asset_refs"])}
        if params["media_kind"] == "carousel"
        else {}
    )
    return {
        "platform": "instagram",
        "media_kind": params["media_kind"],
        "post_draft_id": params["post_draft_id"],
        "asset_sha256": params["asset_sha256"],
        "caption_sha256": hashlib.sha256(params["caption"].encode()).hexdigest(),
        "share_to_feed": params["share_to_feed"],
        "is_ai_generated": params["is_ai_generated"],
        "summary": params.get("summary") or _summary(params),
        **carousel,
        **counts,
    }


def _row_log(row: PendingPost, kind: str, payload: dict[str, Any], **kw: Any) -> None:
    log_event(row.context_id, row.tenant_id, kind, payload, **kw)


def _output(row: PendingPost) -> dict[str, Any]:
    info = row.result
    return PublishSnsOutput(
        status=row.status,
        execution_id=row.execution_id,
        review_id=row.review_id,
        failure_code=row.failure_code,
        media_id=row.media_id,
        permalink=info.get("permalink"),
        is_ai_generated_response=info.get("is_ai_generated_response"),
        ai_label_confirmed=info.get("ai_label_confirmed"),
        ai_label_check=info.get("ai_label_check"),
        ai_label_warning=info.get("ai_label_warning"),
        ai_label_review=info.get("ai_label_review"),
        provider=info.get("provider"),
        item_count=len(row.params["asset_refs"]) if row.params.get("asset_refs") else None,
        asset_refs=row.params.get("asset_refs") or None,
        config_issue=info.get("config_issue"),
        reconcile_needed=info.get("reconcile_needed"),
        limit_check_failed=info.get("limit_check_failed"),
        pause_reason=info.get("pause_reason"),
        auto_publish_off=info.get("auto_publish_off"),
        manual_available=info.get("manual_available"),
        manual_pending=info.get("manual_pending"),
        manual=info.get("manual"),
        posted_by=info.get("posted_by"),
        published_at=info.get("published_at"),
    ).model_dump(exclude_none=True)


def _result_for(request: AgentRequest, row: PendingPost) -> AgentResult:
    out = _output(row)
    ev = _evidence(row)
    if row.status == HELD:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome="held",
            output=out | {"grade": "C"},
            evidence=ev,
        )
    if row.status == APPROVED and row.result.get("manual_pending"):
        return AgentResult(  # 자동 게시가 꺼져 있다: 사람이 직접 올리고 링크를 기록해야 닫힌다
            meta=request.meta,
            status=TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome=MANUAL_PENDING,
            output=out,
            evidence=ev,
        )
    if row.status in (APPROVED, PUBLISHING):
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.WORKING,
            outcome="publishing",
            output=out,
            evidence=ev,
        )
    if row.status == PUBLISHED:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="published",
            output=out,
            evidence=ev,
        )
    if row.status == REJECTED:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="rejected_by_review",
            output=out,
            evidence=ev,
        )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=row.failure_code or "failed",
        output=out,
        evidence=ev,
    )


class _Fail(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NewContainer(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# -- 스킬
def _counts(params: dict[str, Any], sns_store: Any | None) -> dict[str, int]:
    """safety 기준표 필드. 초안이 없으면 확인할 수 없으므로 어긋난 것으로 센다(fail-closed)."""
    from marketing_agent.sns import ai_label_text

    draft = sns_store.get(params["post_draft_id"]) if sns_store is not None else None
    # 라벨 문구는 종류별이다(릴스는 "영상"). 점검(`check_sns_post`)과 같은 문구를 본다
    label = ai_label_text(params["media_kind"])
    missing_label = 0 if (params["is_ai_generated"] and label in params["caption"]) else 1
    if draft is None:
        return {
            "ai_label_missing_count": missing_label,
            "text_mismatch_count": 1,
            "constraint_missing_count": 1,
        }
    mismatch = sum(1 for t in draft.required_text if t not in params["caption"])
    constraints = sum(1 for c in draft.constraints if c.text not in params["caption"])
    if params["media_kind"] == "carousel":  # 장마다 라벨·규격·승인한 초안과의 일치를 센다
        no_label, changed, _ = _carousel_check(params, draft)
        missing_label += no_label
        mismatch += changed
    return {
        "ai_label_missing_count": missing_label,
        "text_mismatch_count": mismatch,
        "constraint_missing_count": constraints,
    }


def _carousel_check(params: dict[str, Any], draft: Any) -> tuple[int, int, list[str]]:
    """(라벨 누락 장 수, 초안과 어긋난 항목 수, 문제 코드). 모두 0이어야 게시할 수 있다."""
    from marketing_agent import sns

    pairs = list(zip(params["asset_refs"], params["asset_sha256s"], strict=True))
    if draft.media_kind != "carousel" or pairs != [
        (a.asset_ref, a.asset_sha256) for a in draft.assets
    ]:
        return 0, 1, ["carousel_differs_from_draft"]
    media = sns._check_carousel(draft)  # noqa: SLF001 - 점검 규칙을 한 곳에 둔다
    return media.overlay_missing, media.asset_changed, media.problems


async def publish_sns(
    request: AgentRequest, sns_store: Any | None = None, *, attempt: int = 0
) -> AgentResult:
    """`attempt`는 입력이 아니라 호출 인자다: 만료 건의 사람 재요청에서 마케팅 코드만 넣는다."""
    try:
        data = PublishSnsInput.model_validate(request.input)
    except ValidationError as error:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="invalid_input",
            output={
                "errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            },
        )
    params = data.model_dump()
    if data.media_kind == "carousel":  # 전체 해시가 내용 해시·멱등 키·안전 관문 값에 들어간다
        params["asset_sha256"] = sns_media.carousel_sha256(list(data.asset_sha256s or []))
        params["asset_ref"] = ""
    sha = params_sha(params)
    key = idempotency_key(request.meta.context_id, data.post_draft_id, sha, attempt)

    # 자동 게시가 꺼져 있으면(수동 게시) Instagram 어댑터를 쓰지 않는다. 스위치를 읽을 수 없으면
    # 여기서는 자동 경로로 보고, 아래 `_pre_gate`가 `publish_control_error`로 막는다(fail-closed)
    try:
        manual_mode = _paused() is not None
    except PublishControlError:
        manual_mode = False
    # 자동 게시인데 실제 어댑터가 없으면 승인을 받으러 가기 전에 거부한다(받아도 게시할 수 없다)
    adapter: InstagramAdapter | None = None
    if not manual_mode:
        try:
            adapter = get_adapter()
        except AdapterUnavailable:
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="adapter_unavailable",
                output={
                    "reason": "Instagram 어댑터 설정이 없다 "
                    "(MARKETING_SNS_PROVIDER, MARKETING_IG_*)"
                },
            )
    if data.media_kind == "carousel":
        if adapter is not None and not getattr(adapter, "supports_carousel", False):
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="carousel_unsupported",
                output={
                    "reason": "이 어댑터는 캐러셀을 켜지 않았다 (MARKETING_IG_CAROUSEL_ENABLED)"
                },
            )
        # 승인을 받으러 가기 전에, 모든 장이 라벨·규격을 통과한 초안과 같은지 본다(fail-closed)
        draft = sns_store.get(data.post_draft_id) if sns_store is not None else None
        problems = ["draft_unavailable"] if draft is None else _carousel_check(params, draft)[2]
        if problems:
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="carousel_not_publishable",
                output={"problems": problems[:20]},
            )

    try:
        existing = store().get(key)
        if existing is not None:
            if existing.status == HELD:  # 같은 요청 재전송 = 승인 여부 다시 확인
                await _approve_and_publish(existing, "resubmit")
                existing = store().get(key) or existing
            return _result_for(request, existing)

        refused, gate_flags = await _pre_gate(adapter)
        if refused:  # 중단 중이거나 24시간 상한이다. 승인을 받으러 가지 않는다
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome=refused,
                output={"reason": refused, **gate_flags},
            )
        meta = request.meta.model_copy(update={"idempotency_key": key})
        decision = await execute(
            ACTION,
            _safety_params(params, _counts(params, sns_store)),
            meta,
            Evidence(
                queries=[ACTION],
                data_summary=f"게시 요청 {data.post_draft_id} ({data.media_kind})",
                collected_at=_now(),
                ref_ids=[data.post_draft_id],
            ),
        )
        log_event(
            request.meta.context_id,
            request.meta.tenant_id,
            "execute_decided",
            {
                "action": ACTION,
                "execution_id": decision.execution_id,
                "status": decision.status,
                "grade": decision.grade,
                "review_id": decision.review_id,
            },
            skill=ACTION,
            grade=decision.grade,
        )
        if decision.status not in ("executed", "held"):
            outcome = "execute_failed" if decision.status == "failed" else "execute_rejected"
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome=outcome,
                output={"execution_id": decision.execution_id, "grade": decision.grade},
            )
        row = PendingPost(
            idempotency_key=key,
            tenant_id=request.meta.tenant_id,
            context_id=request.meta.context_id,
            post_draft_id=data.post_draft_id,
            content_sha=sha,
            execution_id=decision.execution_id,
            review_id=decision.review_id,
            status=HELD if decision.status == "held" else APPROVED,
            params=params,
        )
        if not store().create(row):  # 동시에 같은 요청이 먼저 들어갔다
            return _result_for(request, store().get(key) or row)
        if gate_flags.get("auto_publish_off"):  # 승인 대기 중에도 수동 게시 예정임을 보여 준다
            store().update(key, result=dict(gate_flags))
        if row.status == APPROVED:
            await _run_publish(row)
        return _result_for(request, store().get(key) or row)
    except PendingStorageError:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="storage_error",
            output={"reason": "게시 대기 저장소 오류. 게시하지 않았다"},
        )


# -- 승인 처리 (구독자·폴링·재전송 공통)
def _close(row: PendingPost, old: str, new: str, code: str | None, **fields: Any) -> bool:
    won = store().transition(row.idempotency_key, old, new, failure_code=code, **fields)
    if won and new == PUBLISHED:
        for hook in PUBLISHED_HOOKS:
            try:
                hook(
                    row, fields.get("media_id") or row.media_id, fields.get("result") or row.result
                )
            except Exception:  # noqa: BLE001 - 이벤트를 못 쌓아도 게시 기록은 그대로 둔다
                _log.exception("sns_published_hook_failed")
    if won:
        _row_log(
            row,
            "result_sent",
            {
                "skill": ACTION,
                "status": new,
                "failure_code": code,
                "execution_id": row.execution_id,
                "media_id": fields.get("media_id"),
            },
            skill=ACTION,
            task_status=new,
        )
    return won


async def _approve_and_publish(row: PendingPost, source: str) -> None:
    """`held` 건을 승인 뒤 게시로 잇는다. 이벤트가 두 번 와도 게시는 한 번이다(compare-and-swap)."""
    key = row.idempotency_key
    current = RT.resolver(row)
    if params_sha(current) != row.content_sha:
        _close(row, HELD, FAILED, "content_changed")  # 새 승인이 필요하다
        return
    decision = await execute(ACTION, _safety_params(row.params, {}), _meta(row), _evidence(row))
    _row_log(
        row,
        "execute_decided",
        {
            "action": ACTION,
            "execution_id": decision.execution_id,
            "status": decision.status,
            "grade": decision.grade,
            "source": source,
        },
        skill=ACTION,
        grade=decision.grade,
    )
    if decision.status == "held":
        return  # 아직 결정 전
    if decision.status != "executed":
        _close(row, HELD, REJECTED, decision.hold_reason or "execute_rejected")
        return
    if not store().transition(key, HELD, APPROVED):
        return  # 다른 경로가 먼저 승인을 이었다
    await _run_publish(store().get(key) or row)


async def apply_decision(
    row: PendingPost,
    decision: Literal["approved", "rejected"],
    source: str,
    *,
    expired: bool = False,
) -> None:
    """`expired`는 거절이 사람 결정이 아니라 마감 만료(자동 거절)일 때만 참이다."""
    if row.status != HELD:
        _row_log(
            row,
            "task_status",
            {"skill": ACTION, "note": "duplicate_decision", "status": row.status},
        )
        return
    if decision == "rejected":
        if expired:
            _close(row, HELD, REJECTED, REVIEW_TIMEOUT)
        else:  # `cause`로 새 기록과 구분 못 하던 옛 기록(cause 없음)을 가른다
            _close(row, HELD, REJECTED, REVIEW_REJECTED, result={"cause": "human"})
        return
    await _approve_and_publish(row, source)


async def on_review_decided(envelope: EventEnvelope) -> None:
    """`safety.review_decided` 구독자. action=publish_sns, 요청자=marketing만 받는다."""
    try:
        data = SafetyReviewDecidedData.model_validate(envelope.data)
    except ValidationError:
        return
    accepted = {SOURCE}
    with contextlib.suppress(RuntimeError):
        accepted.add(get_agent_name())
    if data.action != ACTION or data.requester not in accepted:
        return
    row = store().by_review(data.review_id)
    if row is None or canonical_company_id(row.tenant_id) != canonical_company_id(
        envelope.tenant_id
    ):
        return
    # 마감 만료는 안전 관문이 `system` 이름으로 자동 거절한다(사람 이름이 아니다)
    await apply_decision(row, data.decision, "event", expired=data.decided_by == "system")


def setup_subscriber() -> bool:
    """구독을 등록한다. `REDIS_URL`이 없으면 꺼져 있음을 기록하고 폴링 대비책만 쓴다."""
    try:
        events.subscribe([EventType.SAFETY_REVIEW_DECIDED], on_review_decided)
    except NotImplementedError:  # EventBusUnavailable
        RT.subscriber = "disabled_no_redis"
        _log.warning(
            "sns_subscriber_disabled: REDIS_URL이 없어 safety.review_decided를 받지 못한다. "
            "승인 확인은 폴링(MARKETING_SNS_REVIEW_POLL_S)으로만 이어진다"
        )
        return False
    RT.subscriber = "on"
    return True


# -- 폴링 대비책
async def _review_info(row: PendingPost, client: httpx.AsyncClient | None) -> dict[str, Any] | None:
    """안전 관문 보류 건 조회(읽기 전용 GET). 못 읽으면 None, 다음 주기에 다시 묻는다."""
    if not row.review_id:
        return None
    http = client or httpx.AsyncClient()
    try:
        response = await http.get(
            f"{agent_url('safety')}/api/safety/reviews/{row.review_id}",
            headers={"Authorization": f"Bearer {agent_token(get_agent_name())}"},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()["data"]
        return data if isinstance(data, dict) and "status" in data else None
    except Exception:  # noqa: BLE001 - 다음 주기에 다시 묻는다
        return None
    finally:
        if client is None:
            await http.aclose()


async def _review_status(row: PendingPost, client: httpx.AsyncClient | None) -> str | None:
    info = await _review_info(row, client)
    return info["status"] if info else None


async def remember_expiry(row: PendingPost, info: dict[str, Any] | None = None) -> None:
    """안전 관문 보류 건의 마감 시각을 대기 건에 적어 둔다(조회 화면의 남은 시간).

    `info`가 없으면 한 번 읽어 온다. 마감을 바꾸지 않고 적기만 한다.
    """
    info = info or await _review_info(row, None)
    raw = (info or {}).get("expires_at")
    if not isinstance(raw, str):
        return
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return
    with contextlib.suppress(PendingStorageError):
        store().set_review_expiry(row.idempotency_key, stamp.astimezone(UTC).isoformat())


async def poll_reviews_once(client: httpx.AsyncClient | None = None) -> int:
    """대기(held) 건마다 `GET /api/safety/reviews/{id}`로 결정을 확인한다. 이은 건수를 돌려준다."""
    handled = 0
    for row in store().with_status(HELD):
        info = await _review_info(row, client)
        status = info["status"] if info else None
        if status == "approved":
            await apply_decision(row, "approved", "poll")
        elif status == "rejected":
            await apply_decision(row, "rejected", "poll")
        elif status == "expired":  # 거절과 만료를 구분해 기록한다
            await apply_decision(row, "rejected", "poll", expired=True)
        else:
            if status == "pending" and info is not None:
                await remember_expiry(row, info)
            continue
        handled += 1
    return handled


async def review_poll_loop() -> None:
    interval = config().review_poll_s
    while True:
        await RT.sleep(interval)
        try:
            await poll_reviews_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.exception("승인 폴링 실패, 다음 주기에 다시 한다")


# -- 게시 실행 (executed 뒤에만 들어온다)
async def _retrying[T](row: PendingPost, op: str, call: Callable[[], Awaitable[T]]) -> T:
    """일시 오류만 백오프로 다시 한다. 상한을 넘거나 재시도 대상이 아니면 실패로 올린다.

    2207027(아직 준비 안 됨)은 한 번만 기다렸다가(`status_poll_s`) 다시 한다.
    """
    cfg = config()
    attempt = 0
    not_ready_used = 0
    while True:
        try:
            return await call()
        except IgResponseLost:
            code, action = "ig_response_lost", "retry"
        except IgApiError as error:
            code, action = classify(error.code, error.subcode)
        _row_log(row, "sns_publish_attempt", {"op": op, "failure": code, "attempt": attempt})
        if action == "retry":
            if code == NOT_READY:
                if not_ready_used < NOT_READY_RETRIES:
                    not_ready_used += 1
                    await RT.sleep(cfg.status_poll_s)
                    continue
            elif attempt < cfg.max_retries:
                await RT.sleep(cfg.retry_base_s * (2**attempt))
                attempt += 1
                continue
        if action == "new_container":
            raise _NewContainer(code)
        raise _Fail(code)


async def _wait_finished(row: PendingPost, adapter: InstagramAdapter, container_id: str) -> None:
    cfg = config()
    for _ in range(cfg.status_max_polls):
        status = await _retrying(
            row, "get_container_status", lambda: adapter.get_container_status(container_id)
        )
        _row_log(
            row,
            "sns_container_status",
            {
                "container_id": container_id,
                "status_code": status.status_code,
                "error_code": status.error_code,
                "error_subcode": status.error_subcode,
            },
        )
        if status.status_code == "FINISHED":
            return
        if status.status_code == "IN_PROGRESS":
            await RT.sleep(cfg.status_poll_s)
            continue
        if status.status_code == "ERROR":
            # ERROR는 게시하지 않고 사유 코드로 닫는다. 다시 하려면 새 승인이 필요하다
            code, _action = classify(status.error_code, status.error_subcode)
            raise _Fail("ig_container_error" if code == "ig_unknown_error" else code)
        if status.status_code == "EXPIRED":
            raise _Fail("ig_container_expired")
        raise _Fail("container_already_published")  # 우리가 게시하기 전에 PUBLISHED: 사람이 확인
    raise _Fail("container_not_ready_timeout")


async def _prepare_carousel(row: PendingPost, adapter: InstagramAdapter) -> str:
    """자식(이미지)마다 컨테이너 → 모두 FINISHED → 부모 컨테이너 → FINISHED. 부모 id를 돌려준다.

    부모 컨테이너 id를 `container_id`에 남기는 것이 게시 중복 방지의 열쇠다: 이후는 단일 게시와
    같아서 `media_publish`는 부모에 1회만 보내고, 재시작 복구는 부모 상태로 판정한다.
    자식 id는 `result.children`에 남긴다(어느 단계까지 갔는지 사람이 볼 수 있다).
    """
    p = row.params
    new_container_budget = 1
    while True:
        try:
            children: list[str] = []
            for ref in p["asset_refs"]:
                child = await _retrying(
                    row, "create_carousel_item", lambda ref=ref: adapter.create_carousel_item(ref)
                )
                children.append(child)
                store().update(row.idempotency_key, result={"children": list(children)})
            for child in children:
                await _wait_finished(row, adapter, child)
            parent_request = CarouselRequest(
                child_ids=tuple(children), caption=p["caption"], is_ai_generated=True
            )
            container_id = await _retrying(
                row,
                "create_carousel_container",
                lambda req=parent_request: adapter.create_carousel_container(req),
            )
            store().update(row.idempotency_key, container_id=container_id)
            _row_log(
                row,
                "sns_publish_attempt",
                {"op": "create_carousel_container", "container_id": container_id,
                 "item_count": len(children)},
            )  # fmt: skip
            await _wait_finished(row, adapter, container_id)
            return container_id
        except _NewContainer as again:
            if new_container_budget <= 0:
                raise _Fail(again.code) from again
            new_container_budget -= 1


async def _prepare_container(row: PendingPost, adapter: InstagramAdapter) -> str:
    p = row.params
    if p["media_kind"] == "carousel":
        return await _prepare_carousel(row, adapter)
    request = ContainerRequest(
        media_ref=p["asset_ref"],
        media_kind=p["media_kind"],
        caption=p["caption"],
        is_ai_generated=True,
        share_to_feed=bool(p["share_to_feed"]),
    )
    new_container_budget = 1
    while True:
        try:
            container_id = await _retrying(
                row, "create_container", lambda: adapter.create_container(request)
            )
            store().update(row.idempotency_key, container_id=container_id)
            _row_log(
                row, "sns_publish_attempt", {"op": "create_container", "container_id": container_id}
            )
            await _wait_finished(row, adapter, container_id)
            return container_id
        except _NewContainer as again:
            if new_container_budget <= 0:
                raise _Fail(again.code) from again
            new_container_budget -= 1


async def _resolve_lost(
    row: PendingPost, adapter: InstagramAdapter, container_id: str
) -> str | None:
    """응답을 잃었다. 다시 게시하지 않고 컨테이너가 `PUBLISHED`인지로 판정한다."""
    status = await _retrying(
        row, "get_container_status", lambda: adapter.get_container_status(container_id)
    )
    _row_log(
        row,
        "sns_container_status",
        {"container_id": container_id, "status_code": status.status_code, "after": "lost_response"},
    )
    if status.status_code == "PUBLISHED":
        return status.media_id
    raise _Fail("publish_unconfirmed")  # 게시 여부를 확인하지 못했다. 사람이 확인한다


async def _publish_once(
    row: PendingPost, adapter: InstagramAdapter, container_id: str
) -> tuple[str | None, str | None]:
    """(media_id, config_issue). 응답 필드만 쓰고 새 외부 호출은 하지 않는다."""
    store().update(row.idempotency_key, publish_sent=True)  # 보내기 전에 남긴다(복구가 본다)
    cfg = config()
    attempt = 0
    not_ready_used = 0
    while True:
        _row_log(
            row,
            "sns_publish_attempt",
            {"op": "media_publish", "container_id": container_id, "attempt": attempt},
        )
        try:
            outcome = await adapter.media_publish(container_id)
            if isinstance(outcome, PublishOutcome):
                return outcome.media_id, outcome.config_issue
            return outcome, None
        except IgResponseLost:
            return await _resolve_lost(row, adapter, container_id), None
        except IgApiError as error:
            code, action = classify(error.code, error.subcode)
            if action != "retry":
                raise _Fail(code) from error
            if code == NOT_READY:  # 한 번만 기다렸다가 다시 보낸다
                if not_ready_used >= NOT_READY_RETRIES:
                    raise _Fail(code) from error
                not_ready_used += 1
                await RT.sleep(cfg.status_poll_s)
            else:
                if attempt >= cfg.max_retries:
                    raise _Fail(code) from error
                # 에러 응답이면 게시되지 않았다. 그래도 컨테이너가 이미 PUBLISHED인지 먼저 본다
                await RT.sleep(cfg.retry_base_s * (2**attempt))
                attempt += 1
            status = await _retrying(
                row, "get_container_status", lambda: adapter.get_container_status(container_id)
            )
            if status.status_code == "PUBLISHED":
                return status.media_id, None


async def _verify(
    row: PendingPost, adapter: InstagramAdapter, media_id: str | None
) -> dict[str, Any]:
    result: dict[str, Any] = {"provider": adapter.name, "container_id": row.container_id}
    if row.result.get("children"):  # 캐러셀: 자식 컨테이너 id를 결과에 남긴다
        result["children"] = row.result["children"]
        result["item_count"] = len(row.result["children"])
    result["published_at"] = _now().isoformat()
    if media_id is None:
        return result | {"media_id_unresolved": True} | label_fields(None)
    try:
        info = await _retrying(row, "get_media", lambda: adapter.get_media(media_id))
    except (_Fail, _NewContainer):
        # 게시는 됐다. 확인 조회만 실패 → 표기는 모름(사람이 확인)
        return result | {"verify_failed": True} | label_fields(None)
    return (
        result
        | {
            "permalink": info.permalink,
            "timestamp": info.timestamp,
            "media_product_type": info.media_product_type,
            "is_ai_generated_response": info.is_ai_generated,
        }
        | label_fields(info.is_ai_generated)
    )


# -- 게시 후 AI 표기 확인
LABEL_PENDING = "pending"
LABEL_CONFIRMED = "confirmed"
LABEL_CONFIRMED_MISSING = "confirmed_missing"
LABEL_NOT_REQUIRED = "not_required"
LABEL_CONFIRM_HOURS_ENV = "MARKETING_SNS_LABEL_CONFIRM_HOURS"
_LABEL_CONFIRM_HOURS_DEFAULT = 24.0


def label_fields(api_value: bool | None) -> dict[str, Any]:
    """게시 직후 조회한 `is_ai_generated`로 표기 상태를 정한다. 모르면 확인됨이 아니다(false).

    True면 API 확인(사람 확인 불필요). False면 불일치(우리는 true를 보냈다), None이면 조회 불가.
    둘 다 `ai_label_review.status=pending`으로 사람 확인을 강제한다.
    """
    if api_value is True:
        return {
            "ai_label_confirmed": True,
            "ai_label_check": "api_confirmed",
            "ai_label_review": {"status": LABEL_NOT_REQUIRED},
        }
    if api_value is False:
        check, warning = "api_mismatch", "ai_label_mismatch"
    else:
        check, warning = "api_unavailable", "ai_label_unverified"
    return {
        "ai_label_confirmed": False,
        "ai_label_check": check,
        "ai_label_warning": warning,
        "ai_label_review": {"status": LABEL_PENDING},
    }


def confirm_ai_label(
    key: str, confirmed_by: str, label_visible: bool, now: datetime | None = None
) -> bool:
    """확인자가 앱에서 AI 표기를 보고 기록한다. 대기(`pending`) 건에만, 한 번만 된다.

    보였으면 `ai_label_confirmed=true`·경고 해제, 안 보였으면 false 유지 + `ai_label_missing` 경고.
    반환값은 기록 여부(이미 확인됐거나 대기 건이 아니면 False).
    """
    who = confirmed_by.strip()
    if not who:
        raise ValueError("확인자(confirmed_by)가 필요하다")
    review = {
        "status": LABEL_CONFIRMED if label_visible else LABEL_CONFIRMED_MISSING,
        "confirmed_by": who,
        "confirmed_at": (now or _now()).isoformat(),
    }
    patch = {
        "ai_label_confirmed": bool(label_visible),
        "ai_label_review": review,
        "ai_label_warning": None if label_visible else "ai_label_missing",
    }
    done = store().patch_result(key, patch, where_review_status=LABEL_PENDING)
    row = store().get(key) if done else None
    if row:
        _row_log(row, "sns_publish_attempt", {"op": "ai_label_review", "visible": label_visible})
    return done


def finish_manual(row: PendingPost, record: dict[str, Any], confirmed_by: str) -> bool:
    """수동 게시 완료를 기록한다: `approved` → `publishing` → `published`(compare-and-swap).

    `record`는 사람이 입력한 값(permalink·published_at·posted_by)이다. 같은 transition 한 번에
    기록 전체를 적어 두므로 도중에 멈춰도 `recover_interrupted`가 이어서 닫는다. 이긴 쪽만 True.
    """
    stamp = _now().isoformat()
    result = {
        **row.result,
        **record,
        "manual": True,
        "manual_pending": False,
        "provider": "manual",
        "manual_recorded_at": stamp,
        "ai_label_confirmed": True,
        "ai_label_check": "human_confirmed",
        "ai_label_warning": None,
        "ai_label_review": {
            "status": LABEL_CONFIRMED,
            "confirmed_by": confirmed_by,
            "confirmed_at": stamp,
        },
    }
    result = {k: v for k, v in result.items() if v is not None}
    key = row.idempotency_key
    if not store().transition(key, APPROVED, PUBLISHING, result=result):
        return False
    _close(row, PUBLISHING, PUBLISHED, None, result=result)
    return True


def _confirm_after_hours() -> float:
    try:
        hours = float(os.getenv(LABEL_CONFIRM_HOURS_ENV, ""))
    except ValueError:
        return _LABEL_CONFIRM_HOURS_DEFAULT
    return hours if hours > 0 else _LABEL_CONFIRM_HOURS_DEFAULT


def ai_label_pending(now: datetime | None = None) -> list[dict[str, Any]]:
    """표기 확인 대기 목록(오래된 순). 기한(기본 24시간)을 넘기거나 게시 시각을 모르면 `overdue`."""
    current = now or _now()
    limit = _confirm_after_hours()
    out: list[dict[str, Any]] = []
    for row in store().with_status(PUBLISHED):
        info = row.result
        if (info.get("ai_label_review") or {}).get("status") != LABEL_PENDING:
            continue
        waited: float | None = None
        try:
            published = datetime.fromisoformat(info["published_at"])
            waited = (current - published).total_seconds() / 3600
        except (KeyError, TypeError, ValueError):
            pass
        out.append(
            {
                "idempotency_key": row.idempotency_key,
                "media_id": row.media_id,
                "permalink": info.get("permalink"),
                "published_at": info.get("published_at"),
                "ai_label_warning": info.get("ai_label_warning"),
                "hours_waiting": round(waited, 1) if waited is not None else None,
                "overdue": waited is None or waited >= limit,
            }
        )
    return sorted(out, key=lambda r: r["published_at"] or "")


def _halted() -> bool:
    """`publish_halted`: 한도 초과·계정 제한·스팸 판정 이후 사람이 풀기 전까지 게시하지 않는다."""
    latest = store().halt_latest()
    if latest is None:
        return False
    cleared = config().halt_cleared_before
    return cleared is None or latest[1] > cleared


def _paused() -> dict[str, Any] | None:
    """자동 게시 스위치가 꺼져 있는지. 꺼졌으면 수동 게시 표시(flags), 아니면 None.

    꺼진 동안 승인된 건은 자동으로 올리지 않고 `approved`로 두어 사람이 직접 올리고 링크를
    기록하게 한다(`manual_pending`). 사유는 표시용.

    매 호출마다 DB를 읽으므로 재시작 없이 반영된다. halt 기록은 남기지 않는다(풀기도 사람이 한다).
    DB를 읽을 수 없으면 `PublishControlError`(fail-closed).
    """
    try:
        control = read_control(store())
    except PendingStorageError:
        raise PublishControlError("publish_control_unreadable") from None
    if not control.paused:
        return None
    flags: dict[str, Any] = {"auto_publish_off": True, "manual_available": True}
    if control.reason:
        flags["pause_reason"] = control.reason
    return flags


async def _limit_check(adapter: InstagramAdapter) -> tuple[str | None, dict[str, Any]]:
    """24시간 게시 상한. (거절 코드 또는 None, 결과에 남길 표시).

    로컬 게시 기록과 계정의 실제 사용량(`content_publishing_limit`, 읽기) 중 큰 쪽으로 본다.
    계정 조회가 실패하면 로컬 기록만으로 판단하고 `limit_check_failed`를 남긴다.
    """
    cfg = config()
    used = store().count_posts_since(_now() - DAILY_WINDOW)
    cap = cfg.max_posts_per_24h
    flags: dict[str, Any] = {}
    reader = getattr(adapter, "get_publishing_limit", None)
    if reader is not None:
        try:
            info = await reader()
        except Exception:  # noqa: BLE001 - 어떤 실패든 로컬 기록으로만 판단한다
            flags["limit_check_failed"] = True
        else:
            used = max(used, info.quota_usage)
            if info.quota_total:
                cap = min(cap, info.quota_total)
    if used >= cap:
        return "daily_limit_reached", flags
    return None, flags


def _local_limit_check() -> str | None:
    """Graph 호출 없이 로컬 기록(수동 게시 포함)만으로 보는 24시간 상한."""
    used = store().count_posts_since(_now() - DAILY_WINDOW)
    return "daily_limit_reached" if used >= config().max_posts_per_24h else None


async def _pre_gate(adapter: InstagramAdapter | None) -> tuple[str | None, dict[str, Any]]:
    """승인 요청 전 점검: 자동 게시 꺼짐(수동 게시), 중단 상태, 24시간 상한.

    꺼져 있으면 Graph를 부르지 않는다. 승인 요청은 그대로 만들고(수동 게시도 사람 승인 C등급),
    로컬 기록만으로 상한을 본다. 이때 두 번째 값(수동 게시 표시)은 대기 건에 남는다.
    """
    try:
        paused = _paused()
        if paused is not None:
            return _local_limit_check(), paused
        if adapter is None:
            return "adapter_unavailable", {}
        if _halted():
            return "publish_halted", {}
        return await _limit_check(adapter)
    except LimitsConfigError:
        return "limits_config_error", {}
    except PublishControlError:
        return "publish_control_error", {}


async def _run_publish(row: PendingPost) -> None:
    """`approved` 건을 게시한다. `execute()`가 `executed`를 돌려준 뒤에만 호출된다.

    자동 게시가 꺼져 있으면(또는 이미 수동 게시 대기면) 올리지 않고 `approved`로 둔다. 사람이 직접
    올리고 링크를 기록하면(`finish_manual`) 그때 닫힌다. 수동 대기 건은 스위치가 다시 켜져도
    자동으로 올리지 않는다(사람이 이미 올렸을 수 있어 두 번 올라가는 것을 막는다).
    """
    key = row.idempotency_key
    fresh = store().get(key) or row
    if fresh.status != APPROVED:
        return
    if fresh.result.get("manual_pending"):
        return
    try:
        paused = _paused()
    except PublishControlError:
        paused = None  # 아래 기존 점검이 `publish_control_error`로 닫는다
    if paused is not None:
        store().update(key, result={**fresh.result, **paused, "manual_pending": True})
        _row_log(fresh, "sns_publish_attempt", {"op": "manual_pending"})
        return
    try:
        adapter = get_adapter()
    except AdapterUnavailable:
        _close(row, APPROVED, FAILED, "adapter_unavailable")
        return
    if not store().transition(key, APPROVED, PUBLISHING):
        return  # 이미 다른 경로가 가져갔다(락)
    store().update(key, attempts=row.attempts + 1)
    flags: dict[str, Any] = {}
    try:
        if (paused := _paused()) is not None:
            flags = paused
            raise _Fail("publish_paused")
        if _halted():
            raise _Fail("publish_halted")
        container_id = await _prepare_container(row, adapter)
        refused, flags = await _limit_check(adapter)  # media_publish 직전 다시 본다
        if refused:
            raise _Fail(refused)
        if _halted():
            raise _Fail("publish_halted")
        if (paused := _paused()) is not None:  # 승인·컨테이너 준비 사이에 멈췄을 수 있다
            flags = paused
            raise _Fail("publish_paused")
        media_id, config_issue = await _publish_once(row, adapter, container_id)
    except (_Fail, _NewContainer) as failure:
        if failure.code in HALT_FAILURES:
            store().halt_set(failure.code, key)
        _close(row, PUBLISHING, FAILED, failure.code, **({"result": flags} if flags else {}))
        return
    except LimitsConfigError:
        _close(row, PUBLISHING, FAILED, "limits_config_error")
        return
    except PublishControlError:
        _close(row, PUBLISHING, FAILED, "publish_control_error")
        return
    except Exception as error:  # noqa: BLE001
        _row_log(row, "sns_publish_attempt", {"op": "unexpected", "error": type(error).__name__})
        _close(row, PUBLISHING, FAILED, "unexpected_error")
        return
    fresh = store().get(key) or row
    result = await _verify(fresh, adapter, media_id) | flags
    if config_issue:  # 게시는 됐지만 캡션 부착 실패 등 → 사람이 확인(reconcile)
        result |= {"config_issue": config_issue, "reconcile_needed": True}
        _row_log(fresh, "sns_publish_attempt", {"op": "config_issue", "issue": config_issue})
    _close(fresh, PUBLISHING, PUBLISHED, None, media_id=media_id, result=result)


# -- 재시작 복구
async def recover_interrupted() -> int:
    """다시 시작했을 때 멈춘 건을 정리한다. 어떤 경우에도 `media_publish`를 다시 보내지 않는다."""
    handled = 0
    for row in store().with_status(APPROVED):  # 승인은 받았지만 게시를 시작하지 못했다
        await _run_publish(row)
        handled += 1
    for row in store().with_status(PUBLISHING):
        if row.result.get("manual") and row.result.get("permalink"):
            # 수동 게시 기록 도중 멈췄다: 링크까지 적은 건이라 닫는다(다시 올릴 것이 없다)
            _close(row, PUBLISHING, PUBLISHED, None, result=row.result)
            handled += 1
            continue
        if not (row.publish_sent and row.container_id):
            _close(row, PUBLISHING, FAILED, "interrupted_before_publish")
        else:
            try:
                adapter = get_adapter()
                status = await adapter.get_container_status(row.container_id)
            except Exception:  # noqa: BLE001
                _close(row, PUBLISHING, FAILED, "publish_unconfirmed")
                handled += 1
                continue
            if status.status_code == "PUBLISHED":
                result = await _verify(row, adapter, status.media_id)
                _close(row, PUBLISHING, PUBLISHED, None, media_id=status.media_id, result=result)
            else:
                _close(row, PUBLISHING, FAILED, "publish_unconfirmed")
        handled += 1
    return handled

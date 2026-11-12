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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field, StrictBool, ValidationError, model_validator

from ax_agent_template import events
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
from marketing_agent.sns_publish.config import SnsConfig
from marketing_agent.sns_publish.instagram import (
    AdapterUnavailable,
    CarouselRequest,
    ContainerRequest,
    IgApiError,
    IgResponseLost,
    InstagramAdapter,
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
CAPTION_MAX = 2200
_log = logging.getLogger("ax.marketing.sns_publish")

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
    provider: str | None = None
    item_count: int | None = None  # 캐러셀 장수
    asset_refs: list[str] | None = None  # 캐러셀 각 장의 참조(게시 순서)


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


def idempotency_key(context_id: str, post_draft_id: str, sha: str) -> str:
    return f"{context_id}:{ACTION}:{post_draft_id}:{sha[:12]}"


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
    return Evidence(
        queries=["publish_sns"],
        data_summary=f"게시 대기 건 {row.post_draft_id} ({row.status})",
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
        provider=info.get("provider"),
        item_count=len(row.params["asset_refs"]) if row.params.get("asset_refs") else None,
        asset_refs=row.params.get("asset_refs") or None,
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
    from marketing_agent.sns import AI_LABEL_TEXT

    draft = sns_store.get(params["post_draft_id"]) if sns_store is not None else None
    missing_label = 0 if (params["is_ai_generated"] and AI_LABEL_TEXT in params["caption"]) else 1
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


async def publish_sns(request: AgentRequest, sns_store: Any | None = None) -> AgentResult:
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
    key = idempotency_key(request.meta.context_id, data.post_draft_id, sha)

    # 실제 어댑터가 없으면 승인을 받으러 가기 전에 거부한다(받아도 게시할 수 없다)
    try:
        adapter = get_adapter()
    except AdapterUnavailable:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="adapter_unavailable",
            output={
                "reason": "Instagram 어댑터 설정이 없다 (MARKETING_SNS_PROVIDER, MARKETING_IG_*)"
            },
        )
    if data.media_kind == "carousel":
        if not getattr(adapter, "supports_carousel", False):
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
    row: PendingPost, decision: Literal["approved", "rejected"], source: str
) -> None:
    if row.status != HELD:
        _row_log(
            row,
            "task_status",
            {"skill": ACTION, "note": "duplicate_decision", "status": row.status},
        )
        return
    if decision == "rejected":
        _close(row, HELD, REJECTED, "review_rejected")
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
    if row is None or row.tenant_id != envelope.tenant_id:
        return
    await apply_decision(row, data.decision, "event")


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
async def _review_status(row: PendingPost, client: httpx.AsyncClient | None) -> str | None:
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
        return response.json()["data"]["status"]
    except Exception:  # noqa: BLE001 - 다음 주기에 다시 묻는다
        return None
    finally:
        if client is None:
            await http.aclose()


async def poll_reviews_once(client: httpx.AsyncClient | None = None) -> int:
    """대기(held) 건마다 `GET /api/safety/reviews/{id}`로 결정을 확인한다. 이은 건수를 돌려준다."""
    handled = 0
    for row in store().with_status(HELD):
        status = await _review_status(row, client)
        if status == "approved":
            await apply_decision(row, "approved", "poll")
        elif status in ("rejected", "expired"):
            await apply_decision(row, "rejected", "poll")
        else:
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
    """일시 오류만 백오프로 다시 한다. 상한을 넘거나 재시도 대상이 아니면 실패로 올린다."""
    cfg = config()
    attempt = 0
    while True:
        try:
            return await call()
        except IgResponseLost:
            code, action = "ig_response_lost", "retry"
        except IgApiError as error:
            code, action = classify(error.code, error.subcode)
        _row_log(row, "sns_publish_attempt", {"op": op, "failure": code, "attempt": attempt})
        if action == "retry" and attempt < cfg.max_retries:
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
            code, action = classify(status.error_code, status.error_subcode)
            if action == "fail":
                raise _Fail(code)
            raise _NewContainer(code)  # 컨테이너가 깨졌으니 새 컨테이너 1회
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
) -> str | None:
    store().update(row.idempotency_key, publish_sent=True)  # 보내기 전에 남긴다(복구가 본다)
    cfg = config()
    attempt = 0
    while True:
        _row_log(
            row,
            "sns_publish_attempt",
            {"op": "media_publish", "container_id": container_id, "attempt": attempt},
        )
        try:
            return await adapter.media_publish(container_id)
        except IgResponseLost:
            return await _resolve_lost(row, adapter, container_id)
        except IgApiError as error:
            code, action = classify(error.code, error.subcode)
            if action != "retry" or attempt >= cfg.max_retries:
                raise _Fail(code) from error
            # 에러 응답이 왔으므로 게시되지 않았다. 그래도 컨테이너가 이미 PUBLISHED인지 먼저 본다
            await RT.sleep(cfg.retry_base_s * (2**attempt))
            attempt += 1
            status = await _retrying(
                row, "get_container_status", lambda: adapter.get_container_status(container_id)
            )
            if status.status_code == "PUBLISHED":
                return status.media_id


async def _verify(
    row: PendingPost, adapter: InstagramAdapter, media_id: str | None
) -> dict[str, Any]:
    result: dict[str, Any] = {"provider": adapter.name, "container_id": row.container_id}
    if row.result.get("children"):  # 캐러셀: 자식 컨테이너 id를 결과에 남긴다
        result["children"] = row.result["children"]
        result["item_count"] = len(row.result["children"])
    if media_id is None:
        return result | {"media_id_unresolved": True}
    try:
        info = await _retrying(row, "get_media", lambda: adapter.get_media(media_id))
    except (_Fail, _NewContainer):
        return result | {"verify_failed": True}  # 게시는 됐다. 확인 조회만 실패
    return result | {
        "permalink": info.permalink,
        "timestamp": info.timestamp,
        "media_product_type": info.media_product_type,
        "is_ai_generated_response": info.is_ai_generated,
        "ai_label_confirmed": info.is_ai_generated is True,
    }


async def _run_publish(row: PendingPost) -> None:
    """`approved` 건을 게시한다. `execute()`가 `executed`를 돌려준 뒤에만 호출된다."""
    key = row.idempotency_key
    try:
        adapter = get_adapter()
    except AdapterUnavailable:
        _close(row, APPROVED, FAILED, "adapter_unavailable")
        return
    if not store().transition(key, APPROVED, PUBLISHING):
        return  # 이미 다른 경로가 가져갔다(락)
    store().update(key, attempts=row.attempts + 1)
    try:
        container_id = await _prepare_container(row, adapter)
        media_id = await _publish_once(row, adapter, container_id)
    except (_Fail, _NewContainer) as failure:
        _close(row, PUBLISHING, FAILED, failure.code)
        return
    except Exception as error:  # noqa: BLE001
        _row_log(row, "sns_publish_attempt", {"op": "unexpected", "error": type(error).__name__})
        _close(row, PUBLISHING, FAILED, "unexpected_error")
        return
    fresh = store().get(key) or row
    result = await _verify(fresh, adapter, media_id)
    _close(fresh, PUBLISHING, PUBLISHED, None, media_id=media_id, result=result)


# -- 재시작 복구
async def recover_interrupted() -> int:
    """다시 시작했을 때 멈춘 건을 정리한다. 어떤 경우에도 `media_publish`를 다시 보내지 않는다."""
    handled = 0
    for row in store().with_status(APPROVED):  # 승인은 받았지만 게시를 시작하지 못했다
        await _run_publish(row)
        handled += 1
    for row in store().with_status(PUBLISHING):
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

"""marketing skill 핸들러 (필수 4개: draft_notice, publish_notice, get_notice, issue_coupon,
권장 1개: list_active_notices).

- 상태를 바꾸는 일(공지 게시, 쿠폰 지급)은 `execute()`가 `executed`를 돌려준 뒤에만 한다.
- 외부 입력(`request.untrusted`)은 읽지 않는다. 문안·금액은 사실 필드와 코드 계산으로만 만든다.
- 저장소는 메모리다(목 모드). 영속 저장은 shop-api 연동 때 붙인다(TODO).
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from ax_agent_template.execute import execute
from ax_agent_template.logging import log_event
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    CouponIssuedData,
    EventEnvelope,
    EventType,
    Evidence,
    ExecuteDecision,
    HoldReason,
    InputReason,
    NoticePublishedData,
    SnsPostPublishedData,
    TaskStatus,
)
from ax_contracts.schemas.ad_metrics import (
    GetAdMetricsInput,
    GetAdMetricsOutput,
    RecordAdMetricsInput,
    RecordAdMetricsOutput,
)
from ax_contracts.schemas.chat import ChatRemoveOutput
from ax_contracts.schemas.finance import ContentFormat
from ax_contracts.schemas.marketing_brand import (
    BrandCharacterMarkInput,
    BrandCharacterMarkOutput,
    BrandCharactersListInput,
    BrandCharactersListOutput,
    BrandCharacterUnmarkInput,
)
from ax_contracts.schemas.marketing_campaigns import (
    ListMarketingCampaignsInput,
    ListMarketingCampaignsOutput,
    MarketingCampaignItem,
)
from ax_contracts.schemas.marketing_creative import (
    CreativeInstagramDraftInput,
    CreativeInstagramOutput,
    CreativeInstagramRequestInput,
    CreativeRecordAttachInput,
    CreativeRecordAttachOutput,
    CreativeRecordGetInput,
    CreativeRecordGetOutput,
    CreativeRecordsListInput,
    CreativeRecordsListOutput,
    CreativeThumbnailSelectInput,
    CreativeThumbnailSelectOutput,
    CreativeThumbnailsOutput,
    CreativeThumbnailsRequestInput,
    CreativeVideoRequestInput,
    CreativeVideoRequestOutput,
    CreativeYoutubeDraftInput,
    CreativeYoutubeOutput,
    CreativeYoutubeRequestInput,
    InstagramStatusOutput,
    RequestVideoCoverOutput,
    RequestVideoCoverSelectInput,
    RequestVideoInstagramDraftInput,
    RequestVideoInstagramOutput,
    RequestVideoInstagramRequestInput,
    RequestVideoYoutubeDraftInput,
    RequestVideoYoutubeOutput,
    RequestVideoYoutubeRequestInput,
    YoutubeStatusOutput,
)
from ax_contracts.schemas.marketing_youtube_stats import (
    YoutubeUploadsListInput,
    YoutubeUploadsListOutput,
    YoutubeVideoStatsInput,
    YoutubeVideoStatsOutput,
)
from ax_contracts.schemas.planning import CampaignBriefInput, CampaignBriefOutput
from marketing_agent import ad_metrics as _ad_metrics
from marketing_agent import (
    brief_media,
    brief_publish,
    cost_limits,
    manual_publish,
    outage_sns_media,
    publish_control,
    request_video,
    secret_requests,
    shop_client,
    state_db,
    video_draft,
    video_providers,
)
from marketing_agent import character_sheet as _character_sheet
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import creative_thumbnails as _thumbs
from marketing_agent import higgsfield_status as _higgsfield_status
from marketing_agent import instagram_post as _instagram
from marketing_agent import request_creative as _request_creative
from marketing_agent import request_video_cover as _request_cover
from marketing_agent import request_video_youtube as _request_youtube
from marketing_agent import storyboard as _storyboard
from marketing_agent import youtube as _youtube
from marketing_agent import youtube_stats as _youtube_stats
from marketing_agent.brief_handoff import (
    AUTO,
    DRAFT_MEDIA_KIND,
    FieldRejected,
    HandoffBriefToSnsInput,
    HandoffBriefToSnsOutput,
    SnsMediaRequestDraft,
    build_media_request,
    media_problem,
    media_request_for,
    prepared_of,
    state_of,
    view_of,
)
from marketing_agent.briefs import (
    BriefStorageError,
    BriefStore,
    CampaignBriefList,
    CampaignBriefView,
    DraftState,
    GetCampaignBriefInput,
    ListCampaignBriefsInput,
    SnsHandoffRecord,
    StoredBrief,
    StoredBriefView,
    campaign_id_for,
    rejection_reason,
)
from marketing_agent.campaign import record_campaign_result, start_campaign
from marketing_agent.campaign_auto import CampaignTracker
from marketing_agent.campaign_cancel import CANCEL_HOOKS, cancel_campaign
from marketing_agent.channel_followers import channel_followers as _channel_followers
from marketing_agent.chat import (
    ChatRefused,
    ChatService,
    ChatThreadRef,
    ListChatThreadsInput,
    SendChatMessageInput,
    short,
)
from marketing_agent.chat_store import ChatStorageError, ChatStore
from marketing_agent.coupons import CouponLedger, SequenceGap, total_value
from marketing_agent.links import BuildCampaignLinkInput, CampaignLink, build_campaign_link
from marketing_agent.media import async_generate
from marketing_agent.media.bgm_flow import generate_media_flow
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.prompts import PromptRejected
from marketing_agent.notice import NoticeStore, make_draft
from marketing_agent.notice_text import TEXT_MAX, write_notice_text
from marketing_agent.outage_sns import (
    draft_outage_sns_post,
    list_outage_sns_drafts,
    merge_outage_sns_drafts,
)
from marketing_agent.schemas import (
    ActiveNotices,
    DraftNoticeInput,
    EndedNotices,
    EndIncidentNoticeInput,
    GetNoticeInput,
    IssueCouponInput,
    IssuedCoupon,
    ListActiveNoticesInput,
    NoticeDraft,
    PublishedNotice,
    PublishNoticeInput,
)
from marketing_agent.sns import SnsStore, check_sns_post, draft_sns_post
from marketing_agent.sns_prepare import prepare_sns_post_from_media
from marketing_agent.sns_publish.publisher import PUBLISHED_HOOKS, publish_sns
from marketing_agent.state_db import StateDb

SOURCE = "marketing"
BANNER_TEXT_MAX = TEXT_MAX["banner"]  # shop-api 배너 문구 상한(200자)
RESOLVED_BANNER_HOURS_ENV = "MARKETING_RESOLVED_BANNER_HOURS"
RESOLVED_BANNER_HOURS_DEFAULT = 24.0  # 정상화 배너는 게시 뒤 이 시간이 지나면 저절로 내려간다


def resolved_banner_hours() -> float:
    """정상화 배너를 게시해 두는 시간. 환경변수가 없거나 0 이하·숫자가 아니면 기본값이다."""
    raw = os.getenv(RESOLVED_BANNER_HOURS_ENV, "").strip()
    try:
        hours = float(raw) if raw else RESOLVED_BANNER_HOURS_DEFAULT
    except ValueError:
        return RESOLVED_BANNER_HOURS_DEFAULT
    return hours if hours > 0 else RESOLVED_BANNER_HOURS_DEFAULT


class State:
    def __init__(self) -> None:
        # 공지·쿠폰 기록과 못 보낸 이벤트(outbox)는 MARKETING_STATE_DB에 남는다(없으면 메모리)
        self.state_db = StateDb()
        self.notices = NoticeStore(db=self.state_db)
        self.coupons = CouponLedger(db=self.state_db)
        self.briefs = BriefStore()
        # 캠페인별 광고 숫자(사람이 적은 값). MARKETING_AD_METRICS_DB, 없으면 메모리
        self.ad_metrics = _ad_metrics.AdMetricsStore()
        self.campaigns = CampaignTracker()  # 시작된 캠페인 대장(자동 결과 전달)
        self.sns = SnsStore()
        self.media_ledger = MediaLedger()  # 열 수 없으면 시작 때 실패한다
        # 열 수 없으면 시작 때 ChatStorageError로 실패한다(briefs와 같다)
        self.chat = ChatService(ChatStore(), self.chat_facts)
        # 대화에서 만든 스토리보드·영상 요청도 Higgsfield 렌더로 잇는다
        self.chat.on_board = lambda meta, rid: _render.maybe_start_board(
            self.media_ledger, meta, rid
        )
        self.chat.on_video = lambda meta, rid, who: _render.request_video(
            self.media_ledger, meta, rid, who
        )

    def chat_facts(self) -> dict[str, Any]:
        """대화에 넣는 사실. 마케팅 저장소를 읽기만 한다(구매자·고객 정보는 넣지 않는다)."""
        try:
            briefs, brief_total = self.briefs.list(10)
            received: Any = [
                {
                    "proposal_id": b.proposal_id,
                    "campaign_id": b.campaign_id,
                    "status": b.status,
                    "received_at": b.received_at.isoformat(),
                    "title": short(b.brief.title),
                    "idea": short(b.brief.idea),
                    "why": short(b.brief.why),
                    "facts": [f.model_dump(mode="json") for f in b.brief.facts][:12],
                    "target": b.brief.target.model_dump(mode="json") if b.brief.target else None,
                    "products": b.brief.products[:10],
                    "approved_by": b.brief.approved_by,
                    "note": short(b.brief.note),
                    "body_md": short(b.brief.body_md),
                }
                for b in briefs
            ]
        except BriefStorageError:
            brief_total, received = None, "받은 기획안을 읽지 못했다"
        notices, notice_total = self.notices.active(limit=10)
        return {
            "today": _now().strftime("%Y-%m-%d"),
            "received_briefs_total": brief_total,
            "received_briefs": received,
            "active_notices_total": notice_total,
            "active_notices": [
                {
                    "notice_id": n.notice_id,
                    "channel_code": n.channel_code,
                    "public_text": n.public_text,
                    "source": n.source,
                    "valid_from": n.valid_from.isoformat() if n.valid_from else None,
                    "valid_to": n.valid_to.isoformat() if n.valid_to else None,
                }
                for n in notices
            ],
            "coupons_issued_total": self.coupons.issued_count(),
            "campaign_status": "마케팅 서비스에는 캠페인 상태 저장소가 없다(예산·성과는 재무 소관)",
        }


STATE = State()
# 재무가 취소를 받으면 자동 결과 전달 대장에서도 뺀다(campaign_cancel.CANCEL_HOOKS)
CANCEL_HOOKS.append(lambda campaign_id: STATE.campaigns.mark_canceled(campaign_id))
# SNS 게시가 끝나면 sns.post_published 이벤트를 outbox에 쌓는다
# (_sns_published는 아래에 정의, 부를 때 찾는다)
PUBLISHED_HOOKS.append(lambda row, media_id, result: _sns_published(row, media_id, result))


def reset_state() -> None:
    """테스트 사이에 저장소를 비운다."""
    global STATE
    STATE = State()
    async_generate.reset()


def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(queries: list[str], summary: str, ref_ids: list[str] | None = None) -> Evidence:
    return Evidence(
        queries=queries, data_summary=summary, collected_at=_now(), ref_ids=ref_ids or []
    )


def _result(request: AgentRequest, status: TaskStatus, **kwargs: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    return _result(
        request,
        TaskStatus.FAILED,
        outcome="invalid_input",
        output={"errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]},
    )


def _parse[T: BaseModel](request: AgentRequest, model: type[T]) -> T | AgentResult:
    try:
        return model.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)


def _from_decision(
    request: AgentRequest, decision: ExecuteDecision, evidence: Evidence
) -> AgentResult | None:
    """`executed`가 아니면 결과 Task를 만들어 돌려준다. `executed`면 None이다."""
    if decision.status == "executed":
        return None
    detail = {"execution_id": decision.execution_id, "grade": decision.grade}
    if decision.status == "verifying":
        return _result(request, TaskStatus.WORKING, outcome="verifying", output=detail)
    if decision.status == "held":
        detail |= {"review_id": decision.review_id, "hold_reason": decision.hold_reason}
        return _result(
            request,
            TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome="held",
            output=detail,
            evidence=evidence,
        )
    if decision.hold_reason == HoldReason.VERIFY_REJECTED:
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome="rejected_by_review",
            output=detail,
            evidence=evidence,
        )
    if decision.hold_reason == HoldReason.VERIFY_TIMEOUT:
        return _result(
            request, TaskStatus.CANCELED, outcome="TIMEOUT", output=detail, evidence=evidence
        )
    outcome = "execute_failed" if decision.status == "failed" else "execute_rejected"
    return _result(request, TaskStatus.FAILED, outcome=outcome, output=detail, evidence=evidence)


def _event_id(key: str) -> str:
    return f"evt_{hashlib.sha256(key.encode()).hexdigest()[:12]}"


async def _emit(request: AgentRequest, event_type: EventType, data: BaseModel, key: str) -> None:
    """같은 키는 같은 `event_id`라서 재전송해도 한 번만 전달된다."""
    envelope = EventEnvelope(
        event_id=_event_id(key),
        type=event_type,
        occurred_at=_now(),
        tenant_id=request.meta.tenant_id,
        context_id=request.meta.context_id,
        source=SOURCE,
        idempotency_key=key,
        data=data.model_dump(mode="json"),
    )
    # 버스(Redis)가 없거나 끊겼으면 outbox에 쌓고 주기적으로 다시 보낸다(최소 한 번 전달)
    if not await state_db.emit(STATE.state_db, envelope):
        log_event(
            request.meta.context_id,
            request.meta.tenant_id,
            "event_queued",
            {"type": event_type.value},
        )


def _sns_published(row: Any, media_id: str | None, result: dict[str, Any]) -> None:
    """SNS 게시가 끝나면 `sns.post_published`를 outbox에 쌓는다(주기적으로 버스에 보낸다)."""
    key = f"sns_published:{row.idempotency_key}"
    data = SnsPostPublishedData(
        post_draft_id=row.post_draft_id,
        manual=bool(result.get("manual")),
        media_id=media_id,
        permalink=result.get("permalink"),
        published_at=result.get("published_at"),
    )
    envelope = EventEnvelope(
        event_id=_event_id(key),
        type=EventType.SNS_POST_PUBLISHED,
        occurred_at=_now(),
        tenant_id=row.tenant_id,
        context_id=row.context_id or None,
        source=SOURCE,
        idempotency_key=key,
        data=data.model_dump(mode="json"),
    )
    STATE.state_db.enqueue(envelope, "queued")


# 공지 문안 LLM. 테스트는 이 값을 가짜 LLM을 돌려주는 함수로 바꾼다
NOTICE_LLM: Any = None


async def _notice_text(draft: NoticeDraft, data: DraftNoticeInput, context_id: str) -> NoticeDraft:
    """초안 문안을 정한다. 같은 초안이 있으면 그대로, 같은 사실의 배너가 떠 있으면 그 문구로."""
    from marketing_agent.chat import default_llm

    saved = STATE.notices.get_draft(draft.draft_id)
    if saved is not None:  # 같은 입력은 같은 초안이다(LLM을 다시 부르지 않는다)
        return saved
    live = STATE.notices.live_draft_for(draft)
    if live is not None:  # 같은 문구의 배너를 같이 쓰는 규칙이 AI 문안에서도 돌게 한다
        return draft.model_copy(
            update={
                "public_text": live.public_text,
                "source": live.source,
                "text_check": live.text_check,
            }
        )
    return await write_notice_text(draft, data, NOTICE_LLM or default_llm, context_id)


async def draft_notice(request: AgentRequest) -> AgentResult:
    data = _parse(request, DraftNoticeInput)
    if isinstance(data, AgentResult):
        return data
    template = make_draft(request.meta.context_id, data)
    draft = await _notice_text(template, data, request.meta.context_id)
    STATE.notices.save_draft(draft)
    if draft.source == "ai":
        what = f"{data.kind} 사실 필드로 AI 문안 생성, 검증 통과"
    else:
        reasons = ",".join(draft.text_check.reasons) if draft.text_check else "-"
        what = f"{data.kind} 사실 필드로 고정 템플릿 문안 생성 (AI 문안 미사용: {reasons})"
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=draft.model_dump(mode="json"),
        evidence=_evidence(["draft_notice"], what),
        fallback=draft.source == "template",  # AI 문안이 없거나 검증에 실패해 템플릿으로 대체
    )


async def publish_notice(request: AgentRequest) -> AgentResult:
    data = _parse(request, PublishNoticeInput)
    if isinstance(data, AgentResult):
        return data
    draft = STATE.notices.get_draft(data.draft_id)
    if draft is None:
        return _result(request, TaskStatus.FAILED, outcome="draft_not_found")
    if draft.channel_code == "sns":
        # SNS 게시는 별도 관문 행동 publish_sns(C등급, 항상 사람 승인)로만 한다.
        # publish_notice(A등급)로는 즉시 나가므로 안전 장치를 부르기 전에 막는다
        return _result(request, TaskStatus.FAILED, outcome="sns_use_publish_sns")

    to_shop = draft.channel_code == "banner" and shop_client.notice_source() == "shop-api"
    if to_shop and len(draft.public_text) > BANNER_TEXT_MAX:
        # 쇼핑몰 배너 문구는 200자까지다. 안전 장치를 부르기 전에 막아 실행 기록을 남기지 않는다
        return _result(request, TaskStatus.FAILED, outcome="notice_text_too_long")

    if (
        draft.kind == "incident_resolved"
        and draft.channel_code == "banner"
        and draft.valid_to is None
    ):
        # 정상화 배너는 끝을 알리는 공지가 따로 없어 기한이 없으면 영영 남는다. 게시 시각부터 센다.
        # 초안 번호는 입력에서 만들어지므로 기한을 채워도 배너 번호는 그대로다(재요청도 같은 번호)
        expiry = STATE.notices.now() + timedelta(hours=resolved_banner_hours())
        draft = draft.model_copy(update={"valid_to": expiry})

    key = f"{request.meta.context_id}:publish_notice:{draft.draft_id}"
    evidence = _evidence(["publish_notice"], f"초안 {draft.draft_id} 게시 요청", [draft.draft_id])
    decision = await execute(
        "publish_notice",
        {"draft_id": draft.draft_id, "channel_code": draft.channel_code},
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = _from_decision(request, decision, evidence)
    if blocked:
        return blocked

    shared = await _same_incident_banner(draft, to_shop)
    if shared is not None:
        # 같은 문구의 장애 배너가 이미 떠 있으면 새로 올리지 않고 그 배너를 같이 쓴다.
        # 같이 쓰는 사건이 모두 끝나야 배너가 내려간다
        notice_id = STATE.notices.join(request.meta.context_id, draft, shared)
        published = PublishedNotice(notice_id=notice_id, channel_code=draft.channel_code)
        return _result(
            request,
            TaskStatus.COMPLETED,
            output=published.model_dump(mode="json"),
            evidence=_evidence(
                ["publish_notice"],
                "같은 문구의 장애 배너가 떠 있어 새로 올리지 않고 같이 씀",
                [notice_id, decision.execution_id],
            ),
        )

    reused = await _same_resolved_banner(request, draft, to_shop)
    if reused is not None:
        # 같은 문구의 정상화 배너가 이미 기한을 두고 떠 있으면 새로 올리지 않고 그대로 둔다
        # (기한을 늘리지 않는다). 같은 사건의 장애 배너는 평소처럼 내린다
        try:
            await _end_incident_banners_after_resolved(request.meta.context_id)
        except shop_client.ShopError as exc:
            return _result(request, TaskStatus.FAILED, outcome=f"shop_banner_failed:{exc.code}")
        notice_id = STATE.notices.join(request.meta.context_id, draft, reused)
        published = PublishedNotice(notice_id=notice_id, channel_code=draft.channel_code)
        return _result(
            request,
            TaskStatus.COMPLETED,
            output=published.model_dump(mode="json"),
            evidence=_evidence(
                ["publish_notice"],
                "같은 문구의 정상화 배너가 떠 있어 새로 올리지 않고 그대로 둠",
                [notice_id, decision.execution_id],
            ),
        )

    notice_id = None
    if to_shop:
        # 쇼핑몰 배너 번호는 초안에서 만든다(같은 초안은 같은 번호라
        # 재요청·재시작에도 한 번만 올라간다).
        # 쇼핑몰에서 실패하면 게시한 것으로 기록하지 않고, 같은 요청을 다시 보내면 이어서 올린다
        notice_id = f"ntc_{draft.draft_id.removeprefix('drf_')}"
        try:
            await shop_client.post_banner(
                notice_id=notice_id,
                text=draft.public_text,
                valid_from=draft.valid_from,
                valid_to=draft.valid_to,
                context_id=request.meta.context_id,
            )
            if (
                draft.kind == "incident_resolved"
            ):  # 복구 공지가 나가면 같은 사건의 장애 배너를 내린다
                await _end_incident_banners_after_resolved(request.meta.context_id)
        except shop_client.ShopError as exc:
            return _result(request, TaskStatus.FAILED, outcome=f"shop_banner_failed:{exc.code}")
    notice_id = STATE.notices.publish(request.meta.context_id, draft, notice_id)
    await _emit(
        request,
        EventType.NOTICE_PUBLISHED,
        NoticePublishedData(
            notice_id=notice_id, channel_code=draft.channel_code, context_id=request.meta.context_id
        ),
        f"notice.published:{notice_id}",
    )
    published = PublishedNotice(notice_id=notice_id, channel_code=draft.channel_code)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=published.model_dump(mode="json"),
        evidence=_evidence(["publish_notice"], "게시 완료", [notice_id, decision.execution_id]),
    )


async def _same_incident_banner(draft: NoticeDraft, to_shop: bool) -> str | None:
    """올리려는 장애 배너와 같은 문구로 이미 쇼핑몰에 떠 있는 배너 번호. 없으면 `None`.

    사건이 둘로 잡혀도(예: 같은 결제 장애가 몇 분 간격으로 두 번) 쇼핑몰에 같은 줄이 두 번
    뜨지 않게 한다. 쇼핑몰로 올리는 배너만 본다. 마케팅이 다시 켜지면 예전 배너를 잊어서
    그 뒤 첫 장애 배너는 막지 못한다. 이미 게시한 초안의 재요청은 원래 길로 보낸다.
    """
    if not to_shop or draft.kind != "incident_opened":
        return None
    own = f"ntc_{draft.draft_id.removeprefix('drf_')}"
    earlier = STATE.notices.published_id(draft.draft_id)
    if earlier is not None:  # 재요청: 같이 쓰던 배너면 그대로, 아니면 원래 길(같은 번호)로
        return earlier if earlier != own else None
    return STATE.notices.open_incident_by_text(draft.public_text)


async def _end_incident_banners_after_resolved(context_id: str) -> None:
    for earlier in STATE.notices.open_incident_notice_ids(context_id):
        if STATE.notices.can_end(context_id, earlier):
            await shop_client.end_banner(earlier)


async def _same_resolved_banner(
    request: AgentRequest, draft: NoticeDraft, to_shop: bool
) -> str | None:
    """올리려는 정상화 배너와 같은 문구로 쇼핑몰에 기한을 두고 떠 있는 배너 번호. 없으면 `None`.

    메모리가 아니라 쇼핑몰 배너 목록으로 찾아서 마케팅이 다시 켜져도 막힌다. 기한이 없는 옛
    배너는 같이 쓰지 않는다(그걸 쓰면 영영 안 내려간다). 목록을 읽지 못하면 게시를 막지 않고
    새로 올린다. 이미 게시한 초안의 재요청은 원래 길(같은 번호)로 보낸다.
    """
    if not to_shop or draft.kind != "incident_resolved":
        return None
    if STATE.notices.published_id(draft.draft_id) is not None:
        return None
    try:
        banners = await shop_client.list_banners()
    except shop_client.ShopError as exc:
        log_event(
            request.meta.context_id,
            request.meta.tenant_id,
            "resolved_banner_lookup_failed",
            {"code": exc.code, "level": "warning"},
        )
        return None
    own = f"ntc_{draft.draft_id.removeprefix('drf_')}"
    for banner in banners:
        notice_id = banner.get("notice_id")
        if (
            isinstance(notice_id, str)
            and notice_id != own
            and banner.get("status") == "active"
            and banner.get("text") == draft.public_text
            and banner.get("valid_to")
        ):
            return notice_id
    return None


async def end_incident_notice(request: AgentRequest) -> AgentResult:
    """정상화 공지 없이 대응이 끝난 이벤트의 장애 배너를 내린다 (총괄이 복구 신호를 받은 뒤 부른다).

    쇼핑몰 배너 목록에서 같은 이벤트(`context_id`)의 게시 중 배너를 찾아 내리고, 메모리에 남은
    장애 공지도 끝낸다. 배너 번호를 메모리에만 두면 재시작 뒤 못 찾기 때문에 목록으로 찾는다.
    내릴 것이 없으면 빈 목록으로 성공한다(다시 불러도 같다). 정상화 공지가 이미 나간 이벤트에는
    부르지 않는다(그 배너까지 내리지 않게 총괄이 막는다).
    """
    data = _parse(request, EndIncidentNoticeInput)
    if isinstance(data, AgentResult):
        return data
    context_id = request.meta.context_id
    key = f"{context_id}:end_incident_notice"
    evidence = _evidence(
        ["end_incident_notice"], "정상화 공지 없이 닫힌 이벤트의 장애 배너 내리기", [context_id]
    )
    decision = await execute(
        "end_incident_notice",
        {"context_id": context_id, "reason": data.reason},
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = _from_decision(request, decision, evidence)
    if blocked:
        return blocked

    remembered = [
        notice_id
        for notice_id in STATE.notices.open_incident_notice_ids(context_id)
        if STATE.notices.can_end(context_id, notice_id)
    ]
    to_end = list(remembered)
    if shop_client.notice_source() == "shop-api":
        try:
            for banner in await shop_client.list_banners():
                notice_id = banner.get("notice_id")
                if (
                    banner.get("context_id") == context_id
                    and banner.get("status") == "active"
                    and isinstance(notice_id, str)
                    and notice_id not in to_end
                ):
                    to_end.append(notice_id)
            to_end = [n for n in to_end if STATE.notices.can_end(context_id, n)]
            for notice_id in to_end:
                await shop_client.end_banner(notice_id)
        except shop_client.ShopError as exc:
            return _result(request, TaskStatus.FAILED, outcome=f"shop_banner_failed:{exc.code}")
    STATE.notices.end_incident_notices(context_id)
    ended = EndedNotices(ended_notice_ids=to_end, status="ended" if to_end else "none_open")
    summary = f"장애 배너 {len(to_end)}개를 내림" if to_end else "내릴 장애 배너가 없음"
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=ended.model_dump(mode="json"),
        evidence=_evidence(["end_incident_notice"], summary, [context_id, *to_end]),
    )


async def get_notice(request: AgentRequest) -> AgentResult:
    data = _parse(request, GetNoticeInput)
    if isinstance(data, AgentResult):
        return data
    view = STATE.notices.view(data.notice_id)
    summary = "게시된 공지 조회" if view.found else "게시된 공지를 찾을 수 없음(확인할 수 없음)"
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=view.model_dump(mode="json"),
        evidence=_evidence(["get_notice"], summary, [data.notice_id]),
    )


async def list_active_notices(request: AgentRequest) -> AgentResult:
    data = _parse(request, ListActiveNoticesInput)
    if isinstance(data, AgentResult):
        return data
    items, total = STATE.notices.active(
        component=data.component,
        channel_code=data.channel_code,
        at=data.at,
        limit=data.limit,
    )
    listing = ActiveNotices(
        items=items,
        total=total,
        unknown_reason=None if items else "조건에 맞는 게시 중인 공지가 없음",
    )
    summary = f"게시 중인 공지 {total}건 조회" if items else "조건에 맞는 게시 중인 공지가 없음"
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=listing.model_dump(mode="json"),
        evidence=_evidence(
            ["list_active_notices"], summary, [item.notice_id for item in items if item.notice_id]
        ),
    )


async def channel_followers(request: AgentRequest) -> AgentResult:
    """공식 SNS 채널 팔로워 수(read). 입력 없음. 모르면 unknown과 이유."""
    out = await _channel_followers()
    summary = (
        "공식 SNS 팔로워 " + ", ".join(f"{c.channel} {c.followers}" for c in out.channels)
        if out.status == "known"
        else f"팔로워 수를 알 수 없음: {out.reason}"
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["channel_followers"], summary, []),
    )


async def higgsfield_status(request: AgentRequest) -> AgentResult:
    """Higgsfield 키가 받아들여지는지 가볍게 확인한다(read). 키 값은 내지 않는다."""
    out = await _higgsfield_status.status()
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["higgsfield_status"], f"Higgsfield 연결 {out.connection}", []),
    )


# 스토리보드 프롬프트(read). 테스트는 이 값을 가짜 LLM을 돌려주는 함수로 바꾼다
STORYBOARD_LLM: Any = None


async def storyboard_prompt(request: AgentRequest) -> AgentResult:
    """줄거리와 이미지 시트 캐릭터 문장으로 장면별 구도를 정한 스토리보드 프롬프트(read)."""
    from marketing_agent.chat import default_llm

    factory = STORYBOARD_LLM or default_llm
    try:
        out = await _storyboard.make_storyboard(
            request.untrusted, factory, context_id=request.meta.context_id
        )
    except _storyboard.StoryboardRefused as refused:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome=refused.code,
            output={"message": refused.message},
        )
    out = out.model_copy(update={"record_id": _record_storyboard(out, request.untrusted)})
    rid = out.record_id
    if rid:
        _safe_render(lambda: _render.maybe_start_board(STATE.media_ledger, request.meta, rid))
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["storyboard_prompt"],
            f"스토리보드 프롬프트 {len(out.shots)}칸 "
            f"(캐릭터 문장 {'사용' if out.character_used else '없음'})",
            [],
        ),
    )


_RECORD_ID = re.compile(r"CR-[0-9a-f]{12}")


def _record_sheet(out: Any, untrusted: dict[str, Any]) -> str | None:
    """시트 결과를 제작 기록에 남긴다. 실패해도 결과는 그대로 돌려준다."""
    try:
        return _creative.store().add_sheet(
            out.model_dump(mode="json", exclude={"record_id"}),
            _character_sheet.clean_note(untrusted.get("note")),
            _creative.clean_thumbnail(untrusted.get("thumbnail")),
        )
    except Exception:  # noqa: BLE001 - 기록은 부가 기능이라 결과를 막지 않는다
        _creative.logger.exception("제작 기록: 이미지 시트를 남기지 못함")
        return None


def _record_storyboard(out: Any, untrusted: dict[str, Any]) -> str | None:
    """`untrusted.record_id`가 있으면 그 기록에 스토리보드를 붙인다."""
    rid = untrusted.get("record_id")
    if not isinstance(rid, str) or not _RECORD_ID.fullmatch(rid):
        return None
    story = " ".join(str(untrusted.get("story") or "").split())
    try:
        done = _creative.store().attach_storyboard(
            rid, story, len(out.shots), out.model_dump(mode="json", exclude={"record_id"})
        )
    except Exception:  # noqa: BLE001 - 기록은 부가 기능이라 결과를 막지 않는다
        _creative.logger.exception("제작 기록: 스토리보드를 붙이지 못함")
        return None
    return rid if done else None


async def creative_records_list(request: AgentRequest) -> AgentResult:
    """이미지 시트·스토리보드 제작 기록 목록(read)."""
    data = _parse(request, CreativeRecordsListInput)
    if isinstance(data, AgentResult):
        return data
    items = _creative.store().list(data.limit)
    out = CreativeRecordsListOutput.model_validate({"items": items})
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["creative_records"], f"제작 기록 {len(items)}건", []),
    )


async def creative_record_get(request: AgentRequest) -> AgentResult:
    """제작 기록 하나(read). 없으면 not_found."""
    data = _parse(request, CreativeRecordGetInput)
    if isinstance(data, AgentResult):
        return data
    if (
        _creative.store().get(data.record_id) is not None
    ):  # 승인된 영상은 이어 만들고 끊긴 단계는 정리
        try:
            _render.expire_stale(data.record_id)
            await _render.check_video(STATE.media_ledger, request.meta, data.record_id)
            _youtube.expire_stale(data.record_id)
            await _youtube.check_upload(request.meta, data.record_id)
            await _instagram.check_record(request.meta, data.record_id)
        except Exception:  # noqa: BLE001 - 진행 확인 실패는 읽기를 막지 않는다
            _render.logger.exception("제작 기록: 영상 승인 확인 실패")
        try:  # 영상이 생겼으면 썸네일 후보 만들기를 사람 결정에 한 번 올리고, 승인됐으면 만든다
            _thumbs.expire_stale(data.record_id)
            await _thumbs.check(STATE.media_ledger, request.meta, data.record_id)
            await _thumbs.request(STATE.media_ledger, request.meta, data.record_id, None, auto=True)
        except Exception:  # noqa: BLE001 - 썸네일 확인 실패는 읽기를 막지 않는다
            _thumbs.logger.exception("제작 기록: 썸네일 확인 실패")
    row = _creative.store().get(data.record_id)
    if row is None:
        return _result(
            request, TaskStatus.FAILED, outcome="not_found", output={"message": "기록이 없다"}
        )
    out = CreativeRecordGetOutput.model_validate(row)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["creative_records"], f"제작 기록 {data.record_id}", []),
    )


def _brand_done(
    request: AgentRequest, record_id: str, marked: bool, dropped: str | None
) -> AgentResult:
    items = _creative.store().brand_characters()
    out = BrandCharacterMarkOutput(record_id=record_id, marked=marked, dropped=dropped, items=items)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            [request.skill], f"대표 캐릭터 {record_id} {'표시' if marked else '표시 뗌'}", []
        ),
    )


async def brand_characters_list(request: AgentRequest) -> AgentResult:
    """요청 회사의 대표 캐릭터(read). 다른 회사 기록은 보이지 않는다."""
    data = _parse(request, BrandCharactersListInput)
    if isinstance(data, AgentResult):
        return data
    items = _creative.store().brand_characters()
    out = BrandCharactersListOutput.model_validate({"items": items})
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["brand_characters"], f"대표 캐릭터 {len(items)}개", []),
    )


async def brand_character_mark(request: AgentRequest) -> AgentResult:
    """제작 기록을 회사 대표 캐릭터로 표시한다. 사람이 권리를 확인해야 한다(누가·언제 남김).

    기록의 표시 칸만 바꾸는 기록 관리라(제작 기록 남기기와 같다) 외부로 나가는 일·비용이 없다.
    """
    data = _parse(request, BrandCharacterMarkInput)
    if isinstance(data, AgentResult):
        return data
    if not data.rights_confirmed or not data.who:
        message = "우리 회사가 쓸 권리가 있는 그림인지 확인해 주세요"
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="rights_not_confirmed",
            output={"message": message},
        )
    dropped = _creative.store().mark_brand(data.record_id, data.who)
    if dropped is False:
        return _result(
            request, TaskStatus.FAILED, outcome="not_found", output={"message": "기록이 없다"}
        )
    return _brand_done(request, data.record_id, True, dropped or None)


async def brand_character_unmark(request: AgentRequest) -> AgentResult:
    """대표 캐릭터 표시만 뗀다(제작 기록은 남는다)."""
    data = _parse(request, BrandCharacterUnmarkInput)
    if isinstance(data, AgentResult):
        return data
    if not _creative.store().unmark_brand(data.record_id):
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="not_found",
            output={"message": "대표 캐릭터가 아니다"},
        )
    return _brand_done(request, data.record_id, False, None)


def _safe_render(fn: Any) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - 그림 생성 시작 실패는 프롬프트 결과를 막지 않는다
        _render.logger.exception("Higgsfield 렌더를 시작하지 못함")


def _start_sheet_render(request: AgentRequest, out: Any) -> None:
    ref: bytes | None = None
    try:
        ref = _character_sheet.decode_image(request.untrusted.get("image")).data
    except Exception:  # noqa: BLE001 - 참조 없이도 만든다
        ref = None
    sheet = out.model_dump(mode="json", exclude={"record_id"})
    rid = out.record_id
    _safe_render(lambda: _render.start_sheet(STATE.media_ledger, request.meta, rid, sheet, ref))


async def creative_video_request(request: AgentRequest) -> AgentResult:
    """스토리보드 그림으로 영상 만들기를 사람 승인에 올린다(C). 승인 뒤에만 Higgsfield를 부른다."""
    data = _parse(request, CreativeVideoRequestInput)
    if isinstance(data, AgentResult):
        return data
    try:
        video = await _render.request_video(
            STATE.media_ledger, request.meta, data.record_id, data.who
        )
    except _render.RenderError as error:
        return _result(
            request, TaskStatus.FAILED, outcome=str(error), output={"message": str(error)}
        )
    out = CreativeVideoRequestOutput(record_id=data.record_id, video=video)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["creative_video_request"], f"영상 요청 {data.record_id}: {video.get('status')}", []
        ),
    )


def _thumb_failed(request: AgentRequest, error: _thumbs.ThumbError) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome=error.code, output={"message": error.code})


async def creative_thumbnails_request(request: AgentRequest) -> AgentResult:
    """제작 기록 영상 썸네일 후보 3장 만들기를 사람 결정(C generate_creative_thumbnails)에."""
    data = _parse(request, CreativeThumbnailsRequestInput)
    if isinstance(data, AgentResult):
        return data
    try:
        thumbs = await _thumbs.request(
            STATE.media_ledger,
            request.meta,
            data.record_id,
            data.who,
            regenerate=data.regenerate,
        )
    except _thumbs.ThumbError as error:
        return _thumb_failed(request, error)
    out = CreativeThumbnailsOutput(record_id=data.record_id, thumbnails=thumbs)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            [request.skill], f"썸네일 후보 요청 {data.record_id}: {thumbs.get('status')}", []
        ),
    )


async def creative_thumbnail_select(request: AgentRequest) -> AgentResult:
    """제작 기록 썸네일 후보 중 하나를 고른다. 승인 대기면 그 승인을 무효로 한다(기록만 바꿈)."""
    data = _parse(request, CreativeThumbnailSelectInput)
    if isinstance(data, AgentResult):
        return data
    try:
        picked = _thumbs.select(data.record_id, data.media_id, data.who)
    except _thumbs.ThumbError as error:
        return _thumb_failed(request, error)
    out = CreativeThumbnailSelectOutput.model_validate(picked)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            [request.skill],
            f"제작 기록 썸네일 {data.record_id}: {data.media_id}"
            + (f" (승인 무효: {', '.join(out.approvals_reset)})" if out.approvals_reset else ""),
            [data.record_id],
        ),
    )


async def creative_record_attach(request: AgentRequest) -> AgentResult:
    """사람이 올린 그림·영상을 제작 기록에 붙인다(A `attach_creative_file`)."""
    data = _parse(request, CreativeRecordAttachInput)
    if isinstance(data, AgentResult):
        return data
    try:
        item = await _render.attach_upload(
            request.meta,
            data.record_id,
            data.part,
            data.asset,
            data.sha256,
            data.size_bytes,
            data.label,
            data.who,
        )
    except _render.RenderError as error:
        return _result(
            request, TaskStatus.FAILED, outcome=str(error), output={"message": str(error)}
        )
    out = CreativeRecordAttachOutput(record_id=data.record_id, part=data.part, item=item)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["creative_record_attach"], f"파일 붙임 {data.record_id} {data.part}", []
        ),
    )


# 유튜브 초안(read: 기록에 초안만 남긴다). 테스트는 이 값을 가짜 LLM 함수로 바꾼다
YOUTUBE_LLM: Any = None


def _youtube_failed(request: AgentRequest, error: Exception) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome=str(error), output={"message": str(error)})


# 유튜브 칸의 "어느 캠페인 영상인지" 말고 초안을 바꾸는 칸
_DRAFT_FIELDS = ("title", "description", "tags", "privacy", "part")


def _known_campaign(campaign_id: str) -> bool:
    """마케팅이 아는 캠페인인가: 시작한 캠페인 대장에 있거나 받아 둔 기획안의 캠페인."""
    return STATE.campaigns.get(campaign_id) is not None or _stored_brief(campaign_id) is not None


def _campaign_refused(request: AgentRequest, campaign_id: str) -> AgentResult | None:
    """고른 캠페인이 마케팅이 모르는 번호면 거절한다(빈 값은 연결 풀기라 통과)."""
    if not campaign_id or _known_campaign(campaign_id):
        return None
    return _result(
        request,
        TaskStatus.FAILED,
        outcome="unknown_campaign",
        output={"message": f"마케팅이 모르는 캠페인이다: {campaign_id}"},
    )


def _only_campaign(data: Any) -> bool:
    """캠페인 연결만 바꾸는 요청인가(올린 뒤에도 바꿀 수 있게 초안 쓰기를 건너뛴다)."""
    if data.campaign_id is None or data.regenerate:
        return False
    return all(getattr(data, name, None) is None for name in _DRAFT_FIELDS)


async def creative_youtube_draft(request: AgentRequest) -> AgentResult:
    """AI가 유튜브 제목·설명·태그 초안을 쓰거나 사람이 고친 값을 저장한다(올리지는 않는다)."""
    from marketing_agent.chat import default_llm

    data = _parse(request, CreativeYoutubeDraftInput)
    if isinstance(data, AgentResult):
        return data
    if data.campaign_id is not None and (refused := _campaign_refused(request, data.campaign_id)):
        return refused
    edits = data.model_dump(include={"title", "description", "tags", "privacy", "part"})
    try:
        if data.campaign_id is not None:
            youtube = _youtube.set_campaign(data.record_id, data.campaign_id)
        if not _only_campaign(data):
            youtube = await _youtube.write_draft(
                data.record_id,
                YOUTUBE_LLM or default_llm,
                request.meta.context_id,
                edits=edits,
                regenerate=data.regenerate,
            )
    except _youtube.YoutubeError as error:
        return _youtube_failed(request, error)
    out = CreativeYoutubeOutput(record_id=data.record_id, youtube=youtube)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["creative_youtube_draft"],
            f"유튜브 초안 {data.record_id} ({youtube.get('source')})",
            [],
        ),
    )


async def creative_youtube_request(request: AgentRequest) -> AgentResult:
    """초안대로 유튜브에 올리기를 사람 승인(C publish_youtube)에 올린다. 승인 뒤에만 올린다."""
    data = _parse(request, CreativeYoutubeRequestInput)
    if isinstance(data, AgentResult):
        return data
    try:
        youtube = await _youtube.request_upload(request.meta, data.record_id, data.who)
    except _youtube.YoutubeError as error:
        return _youtube_failed(request, error)
    out = CreativeYoutubeOutput(record_id=data.record_id, youtube=youtube)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["creative_youtube_request"],
            f"유튜브 올리기 요청 {data.record_id}: {youtube.get('status')}",
            [],
        ),
    )


# 인스타 캡션 초안 LLM. 테스트는 이 값을 가짜 LLM 함수로 바꾼다
INSTAGRAM_LLM: Any = None


async def creative_instagram_draft(request: AgentRequest) -> AgentResult:
    """AI가 인스타 릴스 캡션·해시태그 초안을 쓰거나 사람이 고친 값을 저장한다(올리지 않는다)."""
    from marketing_agent.chat import default_llm

    data = _parse(request, CreativeInstagramDraftInput)
    if isinstance(data, AgentResult):
        return data
    edits = data.model_dump(include={"caption", "hashtags", "part"})
    try:
        instagram = await _instagram.write_record_draft(
            data.record_id,
            INSTAGRAM_LLM or default_llm,
            request.meta.context_id,
            edits=edits,
            regenerate=data.regenerate,
        )
    except _instagram.InstagramError as error:
        return _youtube_failed(request, error)
    out = CreativeInstagramOutput(record_id=data.record_id, instagram=instagram)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["creative_instagram_draft"],
            f"인스타 캡션 초안 {data.record_id} ({instagram.get('source')})",
            [],
        ),
    )


async def creative_instagram_request(request: AgentRequest) -> AgentResult:
    """초안대로 인스타 릴스 올리기를 사람 승인(C publish_instagram_video)에 올린다."""
    data = _parse(request, CreativeInstagramRequestInput)
    if isinstance(data, AgentResult):
        return data
    try:
        instagram = await _instagram.request_record(request.meta, data.record_id, data.who)
    except _instagram.InstagramError as error:
        return _youtube_failed(request, error)
    out = CreativeInstagramOutput(record_id=data.record_id, instagram=instagram)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["creative_instagram_request"],
            f"인스타 올리기 요청 {data.record_id}: {instagram.get('status')}",
            [],
        ),
    )


async def youtube_status(request: AgentRequest) -> AgentResult:
    """AXcellent 유튜브 채널 연결 상태(read). 키 값은 돌려주지 않는다."""
    out = YoutubeStatusOutput(**await _youtube.status())
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["youtube_status"], f"유튜브 연결 {out.connection}", []),
    )


async def instagram_status(request: AgentRequest) -> AgentResult:
    """회사 인스타 연결 상태·프로필 주소(read, 60초 캐시). 토큰 값은 돌려주지 않는다."""
    from marketing_agent import instagram_profile

    out = InstagramStatusOutput(**await instagram_profile.status())
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(["instagram_status"], f"인스타 연결 {out.connection}", []),
    )


async def youtube_video_stats(request: AgentRequest) -> AgentResult:
    """회사 유튜브 영상의 조회수·좋아요·댓글(read, 5분 캐시). 키·토큰 값은 돌려주지 않는다."""
    data = _parse(request, YoutubeVideoStatsInput)
    if isinstance(data, AgentResult):
        return data
    out = YoutubeVideoStatsOutput(
        **await _youtube_stats.video_stats(data.video_ids, data.record_ids)
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome=out.status,
        evidence=_evidence(
            ["youtube_video_stats"],
            f"유튜브 영상 성과 {len(out.items)}개 ({out.reason or out.status})",
            [i.video_id for i in out.items],
        ),
    )


async def youtube_uploads_list(request: AgentRequest) -> AgentResult:
    """회사 유튜브에 올린 영상 목록(read): 기록 id·video_id·주소·올린 시각·제목·연결 번호."""
    data = _parse(request, YoutubeUploadsListInput)
    if isinstance(data, AgentResult):
        return data
    items = _youtube_stats.uploads(data.limit, data.campaign_id)
    requested = _youtube_stats.request_uploads(data.limit, data.campaign_id)
    out = YoutubeUploadsListOutput(items=items, total=len(items), request_items=requested)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["youtube_uploads_list"],
            f"올린 유튜브 영상 {out.total}개 · 의뢰 영상 {len(requested)}개",
            [i.record_id for i in items],
        ),
    )


# 캐릭터 시트 프롬프트(read). 테스트는 이 값을 가짜 LLM을 돌려주는 함수로 바꾼다
CHARACTER_SHEET_LLM: Any = None


async def character_sheet_prompt(request: AgentRequest) -> AgentResult:
    """올린 캐릭터 이미지로 캐릭터 시트 이미지 프롬프트를 만든다(read). 이미지는 저장하지 않는다."""
    from marketing_agent.chat import default_llm

    factory = CHARACTER_SHEET_LLM or default_llm
    try:
        out = await _character_sheet.make_character_sheet(
            request.untrusted, factory, context_id=request.meta.context_id
        )
    except _character_sheet.CharacterSheetRefused as refused:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome=refused.code,
            output={"message": refused.message},
        )
    out = out.model_copy(update={"record_id": _record_sheet(out, request.untrusted)})
    if out.record_id:  # Higgsfield로 이미지 시트 그림(키·모델이 없으면 기록에 이유만)
        _start_sheet_render(request, out)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["character_sheet_prompt"],
            f"캐릭터 시트 프롬프트 {len(out.panels)}칸 "
            f"(본 것 {len(out.seen_ko)}, 추정 {len(out.guessed_ko)})",
            [],
        ),
    )


async def receive_campaign_brief(request: AgentRequest) -> AgentResult:
    """기획안을 받아 보관한다. 캠페인은 시작하지 않는다.

    순서: 검증(`invalid_input`·`rejected`) → 중복(`duplicate`, 관문 안 부름) → 안전 관문
    `execute()` → `executed`일 때만 저장. 저장소는 메모리라 재시작하면 사라진다.
    """
    data = _parse(request, CampaignBriefInput)
    if isinstance(data, AgentResult):
        return data
    reason = rejection_reason(data)
    if reason is not None:
        rejected = CampaignBriefOutput(outcome="rejected", reason_code=reason)
        return _result(
            request,
            TaskStatus.COMPLETED,
            output=rejected.model_dump(mode="json"),
            outcome=rejected.outcome,
            evidence=_evidence(["receive_campaign_brief"], f"기획안 거절 {reason}", []),
        )
    try:
        existing = STATE.briefs.existing(data.proposal_id)
    except BriefStorageError:
        return _brief_storage_failed(request)
    if existing is not None:  # 관문 앞에서 막는다. 보관본은 그대로
        dup = CampaignBriefOutput(outcome="duplicate", campaign_id=existing.campaign_id)
        return _result(
            request,
            TaskStatus.COMPLETED,
            output=dup.model_dump(mode="json"),
            outcome=dup.outcome,
            evidence=_evidence(
                ["receive_campaign_brief"],
                f"기획안 {data.proposal_id} 이미 받음",
                [data.proposal_id, existing.campaign_id],
            ),
        )

    campaign_id = campaign_id_for(data.proposal_id)
    key = f"{request.meta.context_id}:receive_campaign_brief:{data.proposal_id}"
    evidence = _evidence(
        ["receive_campaign_brief"], f"기획안 {data.proposal_id} 받기 요청", [data.proposal_id]
    )
    decision = await execute(
        "receive_campaign_brief",
        {
            "proposal_id": data.proposal_id,
            "campaign_id": campaign_id,
            "insight_id": data.insight_id,
            "title": data.title,
            "approved_by": data.approved_by,
        },
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = _from_decision(request, decision, evidence)
    if blocked:  # 보류·거절·관문 무응답: 저장하지 않는다(같은 요청을 다시 보내면 이어서 처리)
        return blocked
    try:
        out = STATE.briefs.add(data, _now())
    except BriefStorageError:  # 같은 요청을 다시 보내면 관문은 이어서 처리하고 저장을 다시 시도한다
        return _brief_storage_failed(request)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome=out.outcome,
        evidence=evidence.model_copy(
            update={
                "data_summary": f"기획안 {data.proposal_id} {out.outcome}",
                "ref_ids": [data.proposal_id, campaign_id, decision.execution_id],
            }
        ),
    )


def _ad_rejected(request: AgentRequest, reason: str) -> AgentResult:
    out = RecordAdMetricsOutput(outcome="rejected", reason_code=reason)  # type: ignore[arg-type]
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome="rejected",
        evidence=_evidence(["record_campaign_ad_metrics"], f"광고 숫자 거절 {reason}", []),
    )


async def record_campaign_ad_metrics(request: AgentRequest) -> AgentResult:
    """광고 숫자 한 줄을 적거나 고쳐 쓴다(A `record_campaign_ad_metrics`).

    순서: 검증 → 받은 기획안의 캠페인인지 → 안전 관문 `execute()` → `executed`일 때만 저장.
    관문이 보류·거절·무응답이면 저장하지 않는다(fail-closed). 비율은 계산하지 않는다.
    """
    data = _parse(request, RecordAdMetricsInput)
    if isinstance(data, AgentResult):
        return data
    reason = _ad_metrics.rejection(data)
    if reason is not None:
        return _ad_rejected(request, reason)
    try:
        brief = STATE.briefs.get(None, data.campaign_id)
    except BriefStorageError:
        return _ad_rejected(request, "storage_error")
    if brief is None:
        return _ad_rejected(request, "unknown_campaign")
    key = (
        f"{request.meta.context_id}:record_campaign_ad_metrics:{data.campaign_id}:{data.date}"
        f":{data.source}:{data.medium}:{data.impressions}:{data.clicks}:{data.spend_krw}"
    )
    evidence = _evidence(
        ["record_campaign_ad_metrics"],
        f"{data.campaign_id} {data.date} {data.source}/{data.medium} 광고 숫자 적기",
        [data.campaign_id],
    )
    decision = await execute(
        "record_campaign_ad_metrics",
        {
            "campaign_id": data.campaign_id,
            "date": data.date,
            "source": data.source,
            "medium": data.medium,
            "impressions": data.impressions,
            "clicks": data.clicks,
            "spend_krw": data.spend_krw,
            "entered_by": data.entered_by,
        },
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = _from_decision(request, decision, evidence)
    if blocked:
        return blocked
    try:
        outcome, row = STATE.ad_metrics.record(data)
    except _ad_metrics.AdMetricsStorageError:
        return _ad_rejected(request, "storage_error")
    out = RecordAdMetricsOutput(outcome=outcome, row=row)  # type: ignore[arg-type]
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome=outcome,
        evidence=evidence.model_copy(update={"data_summary": f"광고 숫자 {outcome}"}),
    )


async def get_campaign_ad_metrics(request: AgentRequest) -> AgentResult:
    """광고 숫자 원본 줄과 칸별 합계(read). 비율은 주지 않는다. 입력이 없으면 unknown."""
    data = _parse(request, GetAdMetricsInput)
    if isinstance(data, AgentResult):
        return data
    try:
        rows = STATE.ad_metrics.query(data)
    except _ad_metrics.AdMetricsStorageError:
        return _result(request, TaskStatus.FAILED, outcome="storage_error")
    base = {"combos": _ad_metrics.combos(), "default_base_url": _ad_metrics.default_base_url()}
    if not rows:
        out = GetAdMetricsOutput(status="unknown", reason="아직 입력 없음", **base)
    else:
        out = GetAdMetricsOutput(status="ok", rows=rows, totals=_ad_metrics.totals(rows), **base)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome=out.status,
        evidence=_evidence(
            ["get_campaign_ad_metrics"],
            f"광고 숫자 {len(rows)}줄",
            [data.campaign_id] if data.campaign_id else [],
        ),
    )


def _brief_storage_failed(request: AgentRequest) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome="storage_error")


async def list_campaign_briefs(request: AgentRequest) -> AgentResult:
    data = _parse(request, ListCampaignBriefsInput)
    if isinstance(data, AgentResult):
        return data
    try:
        stored, total = STATE.briefs.list(data.limit)
        items = [_with_handoff(i, request) for i in stored]
    except BriefStorageError:
        return _brief_storage_failed(request)
    listing = CampaignBriefList(
        items=items, total=total, unknown_reason=None if items else "받은 기획안이 없음"
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=listing.model_dump(mode="json"),
        evidence=_evidence(
            ["list_campaign_briefs"], f"받은 기획안 {total}건 조회", [i.proposal_id for i in items]
        ),
    )


async def get_campaign_brief(request: AgentRequest) -> AgentResult:
    data = _parse(request, GetCampaignBriefInput)
    if isinstance(data, AgentResult):
        return data
    if data.proposal_id is None and data.campaign_id is None:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": ["proposal_id|campaign_id: missing"]},
        )
    try:
        found = STATE.briefs.get(data.proposal_id, data.campaign_id)
        item = _with_handoff(found, request) if found is not None else None
    except BriefStorageError:
        return _brief_storage_failed(request)
    view = CampaignBriefView(
        found=item is not None,
        item=item,
        unknown_reason=None if item else "받은 기획안을 찾을 수 없음(확인할 수 없음)",
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=view.model_dump(mode="json"),
        evidence=_evidence(
            ["get_campaign_brief"],
            "받은 기획안 조회" if item else "받은 기획안을 찾을 수 없음(확인할 수 없음)",
            [item.proposal_id] if item else [],
        ),
    )


def _draft_state(draft_id: str | None) -> DraftState | None:
    if draft_id is None:
        return None
    draft = STATE.sns.get(draft_id)
    if draft is None:
        return "missing"
    return "media_attached" if (draft.asset_ref or draft.assets) else "caption_only"


def _media_estimate(request: AgentRequest, record: SnsHandoffRecord | None) -> dict | None:
    """넘긴 요청 초안의 예상 사용량·이번 달 예산(장부에서 계산). 참고 정보라 못 구하면 None."""
    if record is None or record.status != "handed" or not record.media_request:
        return None
    try:
        stored = STATE.briefs.existing(record.proposal_id)  # 실제 생성과 같은 프롬프트로 계산
        out = brief_media.estimate(
            request,
            record.proposal_id,
            record.media_request,
            ledger=STATE.media_ledger,
            plan_fields=brief_media.plan_fields_for(stored.brief if stored else None),
        )
        prepared = prepared_of(record)
        audio = (prepared or {}).get("audio")
        if isinstance(audio, dict) and not audio.get("decision"):
            # 영상 소리를 아직 정하지 않았다: "배경음악 따로 만들기"의 추가 비용(BGM 한 곡)
            out["bgm"] = brief_media.bgm_estimate(
                request, record.proposal_id, record.media_request, ledger=STATE.media_ledger
            )
        return out
    except Exception:  # noqa: BLE001 - 조회는 돌려준다. 화면은 비용을 모른다고 보여 준다
        return None


def _publish_state(record: SnsHandoffRecord | None) -> dict | None:
    """준비된 미디어 초안의 게시 요청 상태(승인 대기 저장소에서 읽기만). 못 읽으면 None."""
    try:
        return brief_publish.publish_state(record, STATE.sns)
    except Exception:  # noqa: BLE001 - 조회는 돌려준다. 화면은 게시 요청 버튼을 두지 않는다
        return None


def _with_handoff(item: StoredBrief, request: AgentRequest) -> StoredBriefView:
    record = STATE.briefs.handoff(item.proposal_id)
    return view_of(
        item,
        record,
        _draft_state(record.draft_id if record else None),
        _media_estimate(request, record),
        _publish_state(record),
    )


def _handoff_done(
    request: AgentRequest,
    outcome: str,
    record: SnsHandoffRecord,
    campaign_id: str,
    **detail: str | None,
) -> AgentResult:
    out = HandoffBriefToSnsOutput(
        outcome=outcome,
        campaign_id=campaign_id,
        sns_handoff=state_of(
            record, _draft_state(record.draft_id), _media_estimate(request, record)
        ),
        **detail,
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome=outcome,
        evidence=_evidence(
            ["handoff_brief_to_sns"],
            f"기획안 {record.proposal_id} SNS·미디어 넘김 {outcome}",
            [record.proposal_id, *([record.draft_id] if record.draft_id else [])],
        ),
    )


def _handoff_media(
    item: StoredBrief, data: HandoffBriefToSnsInput
) -> tuple[SnsMediaRequestDraft | None, str | None, dict[str, str | None]]:
    """(미디어 요청 초안, 막힌 이유 코드, 이유 상세). 생성은 하지 않고 만들 수 있는지만 본다.

    옛 호출(종류만)은 필드를 채우지 않는다. `auto`·`autofill`·`fields`가 있으면 추천으로 채우고
    관리자 값으로 덮어쓴다. 자동으로 고른 종류를 서버가 못 만들면 이미지로 물러난다(이유를 남긴다).
    관리자가 직접 고른 종류는 물러나지 않는다(fail-closed).
    """
    settings = MediaSettings.from_env()
    try:
        if not data.fills:
            media = media_request_for(item.proposal_id, data.media_kind)
            return media, media_problem(media, settings), {}
        media = build_media_request(item, data.media_kind, data.fields)
        problem = media_problem(media, settings)
        if problem is not None and data.media_kind == AUTO and media.media_kind != "image":
            fallback = build_media_request(
                item, "image", data.fields, kind_source="auto_fallback", blocked=problem
            )
            if media_problem(fallback, settings) is None:
                return fallback, None, {}
        return media, problem, {}
    except FieldRejected as error:
        return None, "invalid_field", {"field": error.field, "field_code": error.code}
    except (PromptRejected, KeyError):
        return None, "unknown_scene_template", {}


async def handoff_brief_to_sns(request: AgentRequest) -> AgentResult:
    """받은 기획안으로 미디어 요청 초안과 SNS 글 초안을 만들어 보관한다.

    미디어 종류·필드는 기획안에서 추천하고(`media_kind=auto`), 관리자가 준 종류·필드가 우선한다.
    이미 넘긴 기획안은 `revise=true`일 때만 요청 초안을 다시 만든다.
    미디어 생성(`generate_media`)·게시(`publish_sns`)는 부르지 않는다. 사람이 이어서 한다.
    """
    data = _parse(request, HandoffBriefToSnsInput)
    if isinstance(data, AgentResult):
        return data
    if data.proposal_id is None and data.campaign_id is None:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": ["proposal_id|campaign_id: missing"]},
        )

    async def draft(media_kind: str) -> AgentResult:
        draft_input = {"proposal_id": pid, "media_kind": DRAFT_MEDIA_KIND[media_kind]}
        return await draft_sns_post(
            request.model_copy(update={"input": draft_input}),
            STATE.notices,
            STATE.sns,
            STATE.briefs,
        )

    try:
        item = STATE.briefs.get(data.proposal_id, data.campaign_id)
        if item is None:
            return _result(request, TaskStatus.FAILED, outcome="brief_not_found")
        pid = item.proposal_id
        record = STATE.briefs.handoff(pid)
        now = _now()
        handed = record is not None and record.status == "handed"
        stored_kind = str(((record.media_request if record else None) or {}).get("media_kind"))
        if stored_kind not in DRAFT_MEDIA_KIND:
            stored_kind = "image"
        if record is not None and handed and not data.revise:
            # 멱등: 보관본 그대로. 초안만 사라졌다면 같은 내용으로 다시 저장한다(id는 내용 주소)
            if STATE.sns.get(record.draft_id or "") is None:
                await draft(stored_kind)
            return _handoff_done(request, "already_handed", record, item.campaign_id)

        media, problem, detail = _handoff_media(item, data)
        media_dict = media.model_dump(mode="json") if media is not None else None

        if record is not None and handed:  # revise: 막히면 보관본을 바꾸지 않는다
            if problem is not None or media is None:
                return _handoff_done(
                    request,
                    "revise_rejected",
                    record,
                    item.campaign_id,
                    reason_code=problem,
                    **detail,
                )
            draft_id = record.draft_id
            same_draft_kind = DRAFT_MEDIA_KIND[media.media_kind] == DRAFT_MEDIA_KIND[stored_kind]
            if not same_draft_kind or STATE.sns.get(draft_id or "") is None:
                drafted = await draft(media.media_kind)
                if drafted.status != TaskStatus.COMPLETED or not drafted.output:
                    return _handoff_done(
                        request,
                        "revise_rejected",
                        record,
                        item.campaign_id,
                        reason_code=drafted.outcome or "draft_failed",
                    )
                draft_id = drafted.output["post_draft_id"]
            rec = record.model_copy(
                update={"draft_id": draft_id, "media_request": media_dict, "updated_at": now}
            )
            STATE.briefs.save_handoff(rec)
            return _handoff_done(request, "revised", rec, item.campaign_id)

        def failed(code: str, **more: str | None) -> AgentResult:
            rec = SnsHandoffRecord(
                proposal_id=pid,
                status="failed",
                media_request=media_dict,
                reason_code=code,
                updated_at=now,
            )
            STATE.briefs.save_handoff(rec)
            return _handoff_done(request, "failed", rec, item.campaign_id, **more)

        if problem is not None or media is None:  # 가격표 fail-closed, 템플릿·필드 문제
            return failed(problem or "unknown_scene_template", **detail)
        drafted = await draft(media.media_kind)
        if drafted.status != TaskStatus.COMPLETED or not drafted.output:
            return failed(drafted.outcome or "draft_failed")
        rec = SnsHandoffRecord(
            proposal_id=pid,
            status="handed",
            draft_id=drafted.output["post_draft_id"],
            media_request=media_dict,
            handed_at=now,
            updated_at=now,
        )
        STATE.briefs.save_handoff(rec)
    except BriefStorageError:
        return _brief_storage_failed(request)
    return _handoff_done(request, "handed", rec, item.campaign_id)


async def build_campaign_link_skill(request: AgentRequest) -> AgentResult:
    """UTM 꼬리표를 붙인 링크를 만든다. 순수 계산이라 상태를 바꾸지 않는다(`execute()` 없음)."""
    data = _parse(request, BuildCampaignLinkInput)
    if isinstance(data, AgentResult):
        return data
    try:
        url = build_campaign_link(data.base_url, data.campaign_id, data.source, data.medium)
    except ValueError as error:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": [str(error)]},
        )
    link = CampaignLink(
        url=url,
        utm_campaign=data.campaign_id,
        utm_source=data.source,
        utm_medium=data.medium,
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=link.model_dump(mode="json"),
        evidence=_evidence(["build_campaign_link"], "캠페인 링크 꼬리표 생성", [data.campaign_id]),
    )


async def issue_coupon(request: AgentRequest) -> AgentResult:
    data = _parse(request, IssueCouponInput)
    if isinstance(data, AgentResult):
        return data
    if data.purpose_code == "campaign" and not data.campaign_id:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")
    if data.purpose_code == "compensation" and request.sequence is None:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")

    context_id = request.meta.context_id
    key = f"{context_id}:issue_coupon:{data.buyer_id}" + (
        f":{data.campaign_id}" if data.campaign_id else ""
    )
    async with STATE.coupons.lock(context_id):
        if request.sequence is not None:
            replay = STATE.coupons.previous_by_sequence(context_id, request.sequence)
            if replay is not None:  # 같은 번호의 재요청은 이전 결과 그대로
                return replay.model_copy(update={"meta": request.meta})
            try:
                STATE.coupons.check_sequence(context_id, request.sequence)
            except SequenceGap:
                return _result(request, TaskStatus.REJECTED, outcome="SEQUENCE_GAP")
        previous = STATE.coupons.previous_by_key(key)
        if previous is not None:
            if request.sequence is None:  # 같은 키의 재요청은 이전 결과 그대로
                return previous.model_copy(update={"meta": request.meta})
            # 같은 구매자에게 번호만 달리 온 요청: 새로 지급하지 않고 이전 결과에 연결한다
            linked = previous.model_copy(
                update={"meta": request.meta, "outcome": "linked_duplicate"}
            )
            STATE.coupons.record(context_id, key, request.sequence, linked, cache=True)
            return linked

        total = total_value(data.count, data.unit_value)  # 계산은 코드로
        evidence = _evidence(
            ["issue_coupon"], f"{data.count}건 × {data.unit_value}원 = {total}원", [data.buyer_id]
        )
        params = data.model_dump(mode="json", exclude={"claimed_total"}) | {"total_value": total}
        if data.claimed_total is not None:
            params["claimed_total"] = data.claimed_total
        decision = await execute(
            "issue_coupon",
            params,
            request.meta.model_copy(update={"idempotency_key": key}),
            evidence,
        )
        blocked = _from_decision(request, decision, evidence)
        if blocked:
            if blocked.status != TaskStatus.WORKING:  # 회신한 건은 다음 번호로 넘어간다
                # 보류·취소·실패는 다시 시도할 수 있게 결과를 저장하지 않는다(번호만 소비)
                cache = blocked.status == TaskStatus.COMPLETED
                STATE.coupons.record(context_id, key, request.sequence, blocked, cache=cache)
            return blocked

        if shop_client.coupon_source() == "shop-api":
            # 안전 장치를 통과했으면 쇼핑몰에 쿠폰을 만든다. 같은 키의 재요청은 shop-api가
            # 이전 쿠폰을 돌려주므로 실패한 건을 다시 불러도 두 번 만들어지지 않는다.
            # 번호도 shop-api가 정한다
            try:
                coupon = await shop_client.issue_coupon(
                    idempotency_key=key,
                    purpose_code=data.purpose_code,
                    buyer_id=data.buyer_id,
                    count=data.count,
                    unit_value=data.unit_value,
                    campaign_id=data.campaign_id,
                    context_id=context_id,
                )
            except shop_client.ShopError as exc:
                failed = _result(
                    request, TaskStatus.FAILED, outcome=f"shop_coupon_failed:{exc.code}"
                )
                STATE.coupons.record(context_id, key, request.sequence, failed, cache=False)
                return failed
            if coupon.get("applied") == "pending_human":
                # 사이트 변환기가 쿠폰 발급을 할 일함에 넣었다: 아직 발급되지 않았다.
                # 발급 완료로 적지 않고 이벤트도 보내지 않는다(다시 불러도 같은 할 일이다)
                pending = _result(
                    request,
                    TaskStatus.INPUT_REQUIRED,
                    reason_code=InputReason.HUMAN_INPUT,
                    outcome="site_pending_human",
                    output={
                        "applied": "pending_human",
                        "handoff_task_id": coupon.get("handoff_task_id"),
                        "execution_id": decision.execution_id,
                    },
                    evidence=evidence,
                )
                STATE.coupons.record(context_id, key, request.sequence, pending, cache=False)
                return pending
            coupon_id = str(coupon["coupon_id"])
        else:
            coupon_id = STATE.coupons.new_coupon_id()
        issued = IssuedCoupon(
            coupon_id=coupon_id,
            purpose_code=data.purpose_code,
            count=data.count,
            total_value=total,
        )
        result = _result(
            request,
            TaskStatus.COMPLETED,
            output=issued.model_dump(mode="json"),
            evidence=evidence.model_copy(update={"ref_ids": [coupon_id, decision.execution_id]}),
        )
        STATE.coupons.record(context_id, key, request.sequence, result, cache=True)
    await _emit(
        request,
        EventType.COUPON_ISSUED,
        CouponIssuedData(
            coupon_id=coupon_id,
            campaign_id=data.campaign_id if data.purpose_code == "campaign" else None,
            purpose_code=data.purpose_code,
            count=data.count,
            total_value=total,
            buyer_id=data.buyer_id,
            execution_id=decision.execution_id,
        ),
        # 쿠폰 번호는 재시작하면 다시 1부터라 이벤트 키로 쓰면 새 쿠폰이 이미 보낸 것으로 버려진다.
        # 사건·고객으로 만든 멱등 키를 쓴다
        f"coupon.issued:{key}",
    )
    return result


async def draft_sns_post_skill(request: AgentRequest) -> AgentResult:
    return await draft_sns_post(request, STATE.notices, STATE.sns, STATE.briefs)


async def prepare_video_draft_from_plan_skill(request: AgentRequest) -> AgentResult:
    """총괄용: 영상 제작안(video_plan)으로 SNS 초안·장면 템플릿을 정한다(생성·게시 안 함)."""
    return video_draft.prepare_video_draft(request, STATE.sns)


async def list_request_videos_skill(request: AgentRequest) -> AgentResult:
    """의뢰 영상 목록. 유튜브 승인 대기는 관문을 다시 묻고, 항목마다 유튜브 상태를 붙인다."""
    await _request_youtube.refresh(request.meta, STATE.sns)
    await _instagram.refresh_requests(request.meta)
    result = await request_video.list_request_videos(request, STATE.sns)
    if result.status != TaskStatus.COMPLETED or not result.output:
        return result
    items = _request_youtube.attach_states(list(result.output.get("items") or []))
    items = _instagram.attach_states(items)
    for item in items:  # 썸네일을 바꾸면 다시 승인받아야 하는 요청이 있나(화면 확인 상자)
        item["approval_waiting"] = _request_cover.approval_waiting(
            str(item.get("post_draft_id")), item.get("youtube")
        )
    return result.model_copy(update={"output": result.output | {"items": items}})


async def request_video_youtube_draft(request: AgentRequest) -> AgentResult:
    """의뢰로 만든 영상의 유튜브 제목·설명·태그 초안(AI) 또는 사람이 고친 값 저장. 올리지 않는다."""
    from marketing_agent.chat import default_llm

    data = _parse(request, RequestVideoYoutubeDraftInput)
    if isinstance(data, AgentResult):
        return data
    if data.campaign_id is not None and (refused := _campaign_refused(request, data.campaign_id)):
        return refused
    edits = data.model_dump(include={"title", "description", "tags", "privacy"})
    try:
        if data.campaign_id is not None:
            youtube = _request_youtube.set_campaign(STATE.sns, data.post_draft_id, data.campaign_id)
        if not _only_campaign(data):
            youtube = await _request_youtube.write_draft(
                STATE.sns,
                data.post_draft_id,
                YOUTUBE_LLM or default_llm,
                request.meta.context_id,
                edits=edits,
                regenerate=data.regenerate,
            )
    except _request_youtube.YoutubeError as error:
        return _youtube_failed(request, error)
    return _request_youtube_done(request, data.post_draft_id, youtube, "초안")


async def request_video_youtube_request(request: AgentRequest) -> AgentResult:
    """의뢰 영상(AI 라벨 입힌 파일)의 유튜브 올리기를 사람 승인(C publish_youtube)에 올린다."""
    data = _parse(request, RequestVideoYoutubeRequestInput)
    if isinstance(data, AgentResult):
        return data
    try:
        youtube = await _request_youtube.request_upload(
            request.meta, STATE.sns, data.post_draft_id, data.who
        )
    except _request_youtube.YoutubeError as error:
        return _youtube_failed(request, error)
    return _request_youtube_done(request, data.post_draft_id, youtube, "올리기 요청")


async def request_video_instagram_draft(request: AgentRequest) -> AgentResult:
    """의뢰로 만든 영상의 인스타 캡션·해시태그 초안(AI) 또는 사람이 고친 값 저장. 올리지 않는다."""
    from marketing_agent.chat import default_llm

    data = _parse(request, RequestVideoInstagramDraftInput)
    if isinstance(data, AgentResult):
        return data
    try:
        instagram = await _instagram.write_request_draft(
            STATE.sns,
            data.post_draft_id,
            INSTAGRAM_LLM or default_llm,
            request.meta.context_id,
            edits=data.model_dump(include={"caption", "hashtags"}),
            regenerate=data.regenerate,
        )
    except _instagram.InstagramError as error:
        return _youtube_failed(request, error)
    return _request_instagram_done(request, data.post_draft_id, instagram, "초안")


async def request_video_instagram_request(request: AgentRequest) -> AgentResult:
    """의뢰 영상(AI 라벨 파일)의 인스타 릴스 올리기를 사람 승인(C publish_instagram_video)에."""
    data = _parse(request, RequestVideoInstagramRequestInput)
    if isinstance(data, AgentResult):
        return data
    try:
        instagram = await _instagram.request_request(
            request.meta, STATE.sns, data.post_draft_id, data.who
        )
    except _instagram.InstagramError as error:
        return _youtube_failed(request, error)
    return _request_instagram_done(request, data.post_draft_id, instagram, "올리기 요청")


def _request_instagram_done(
    request: AgentRequest, post_draft_id: str, instagram: dict[str, Any], what: str
) -> AgentResult:
    out = RequestVideoInstagramOutput(
        post_draft_id=post_draft_id, request_id=instagram.get("request_id"), instagram=instagram
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            [request.skill],
            f"의뢰 영상 인스타 {what} {post_draft_id}: {instagram.get('status')}",
            [],
        ),
    )


async def request_video_cover_select(request: AgentRequest) -> AgentResult:
    """의뢰 영상 썸네일 후보 중 하나를 고른다. 승인 대기·승인 뒤면 그 승인을 무효로 한다."""
    data = _parse(request, RequestVideoCoverSelectInput)
    if isinstance(data, AgentResult):
        return data
    try:
        picked = _request_cover.select_cover(STATE.sns, data.post_draft_id, data.media_id)
    except _request_cover.CoverError as error:
        return _result(
            request, TaskStatus.FAILED, outcome=error.code, output={"message": error.code}
        )
    out = RequestVideoCoverOutput.model_validate(picked)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            [request.skill],
            f"의뢰 영상 썸네일 {data.post_draft_id}: {data.media_id}"
            + (f" (승인 무효: {', '.join(out.approvals_reset)})" if out.approvals_reset else ""),
            [data.post_draft_id],
        ),
    )


def _request_youtube_done(
    request: AgentRequest, post_draft_id: str, youtube: dict[str, Any], what: str
) -> AgentResult:
    request_id = youtube.get("request_id")
    out = RequestVideoYoutubeOutput(
        post_draft_id=post_draft_id, request_id=request_id, youtube=youtube
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            [request.skill],
            f"의뢰 영상 유튜브 {what} {post_draft_id}: {youtube.get('status')}",
            [],
        ),
    )


async def draft_outage_sns_post_skill(request: AgentRequest) -> AgentResult:
    """장애 사과·보상 SNS 글 초안만 만든다. 게시·미디어 생성은 부르지 않는다."""
    return await draft_outage_sns_post(request, STATE.notices, STATE.sns)


async def list_outage_sns_drafts_skill(request: AgentRequest) -> AgentResult:
    result = await list_outage_sns_drafts(request, STATE.sns)
    # 카드별 진행 상태(이미지 만들기·게시 요청·승인 대기). 참고 정보라 못 구하면 비워 둔다
    for item in (result.output or {}).get("items", []):
        try:
            draft = STATE.sns.get(str(item.get("post_draft_id")))
            if draft is not None:
                item["media_flow"] = outage_sns_media.flow_state(
                    request, draft, STATE.sns, ledger=STATE.media_ledger
                )
        except Exception:  # noqa: BLE001 - 목록은 돌려준다. 화면은 진행 버튼을 두지 않는다
            item["media_flow"] = None
    return result


async def merge_outage_sns_drafts_skill(request: AgentRequest) -> AgentResult:
    """유사 장애 초안 병합. 초안 메타데이터만 바꾼다(캡션·게시·비용 없음, `execute()` 비경유)."""
    return await merge_outage_sns_drafts(request, STATE.sns)


async def check_sns_post_skill(request: AgentRequest) -> AgentResult:
    return await check_sns_post(request, STATE.sns)


async def _generate_media_sync(request: AgentRequest) -> AgentResult:
    if isinstance(request.input, dict) and request.input.get(_request_creative.INPUT_KEY):
        # 11/23 회사 캐릭터를 고른 홍보 영상 의뢰: 시트·스토리보드 그림(B) → 영상(C, 사람 승인)
        result = await _request_creative.make_video(request, STATE.media_ledger)
    else:
        result = await generate_media_flow(
            request, ledger=STATE.media_ledger, from_decision=_from_decision
        )
    # 의뢰 영상 초안이면 완료 직전에 AI 라벨을 입혀 초안에 연결한다(동기·비동기 공통)
    return await request_video.attach_label_after_generate(request, result, store=STATE.sns)


async def generate_media_skill(request: AgentRequest) -> AgentResult:
    """총괄이 부르면 긴 작업(접수 즉시 WORKING + task_id, 끝나면 task.completed). 그 밖은 동기."""
    if async_generate.is_orchestrator(request):
        return await async_generate.generate_media_async(request, _generate_media_sync)
    return await _generate_media_sync(request)


async def prepare_sns_post_from_media_skill(request: AgentRequest) -> AgentResult:
    return await prepare_sns_post_from_media(
        request,
        notices=STATE.notices,
        store=STATE.sns,
        ledger=STATE.media_ledger,
        from_decision=_from_decision,
        briefs=STATE.briefs,
    )


async def prepare_brief_media_skill(request: AgentRequest) -> AgentResult:
    """넘긴 기획안의 요청 초안으로 미디어 초안을 만든다. 게시는 하지 않는다(C등급 사람 승인)."""
    result = await brief_media.prepare_brief_media(
        request,
        briefs=STATE.briefs,
        notices=STATE.notices,
        store=STATE.sns,
        ledger=STATE.media_ledger,
        from_decision=_from_decision,
    )
    state = (result.output or {}).get("sns_handoff")
    if isinstance(state, dict) and state.get("status") == "handed":
        try:
            record = STATE.briefs.handoff(str(request.input.get("proposal_id")))
        except BriefStorageError:
            record = None
        state["media_estimate"] = _media_estimate(request, record)
        state["publish"] = _publish_state(record)
    return result


def _publish_status(record: SnsHandoffRecord) -> str | None:
    state = _publish_state(record)
    return state.get("status") if state else None


async def choose_brief_audio_skill(request: AgentRequest) -> AgentResult:
    """만든 영상의 소리를 한 번 정한다(그대로 쓰기 / 배경음악 따로 만들기). 게시는 하지 않는다."""
    result = await brief_media.choose_brief_audio(
        request,
        briefs=STATE.briefs,
        notices=STATE.notices,
        store=STATE.sns,
        ledger=STATE.media_ledger,
        from_decision=_from_decision,
        publish_status=_publish_status,
    )
    state = (result.output or {}).get("sns_handoff")
    if isinstance(state, dict) and state.get("status") == "handed":
        try:
            record = STATE.briefs.handoff(str(request.input.get("proposal_id")))
        except BriefStorageError:
            record = None
        state["media_estimate"] = _media_estimate(request, record)
        state["publish"] = _publish_state(record)
    return result


async def prepare_outage_sns_media_skill(request: AgentRequest) -> AgentResult:
    """장애 초안에 이미지를 붙인다(B등급 생성). 게시는 하지 않는다(C등급 사람 승인)."""
    return await outage_sns_media.prepare_outage_sns_media(
        request, store=STATE.sns, ledger=STATE.media_ledger, from_decision=_from_decision
    )


async def request_outage_sns_publish_skill(request: AgentRequest) -> AgentResult:
    """이미지를 붙인 장애 초안의 게시 요청(승인 대기 건)만 만든다. 승인은 사람만 한다."""
    return await outage_sns_media.request_outage_sns_publish(
        request, store=STATE.sns, publish=publish_sns
    )


async def request_brief_publish_skill(request: AgentRequest) -> AgentResult:
    """준비된 미디어 초안의 게시 요청(C등급 사람 승인 대기 건)을 만든다. 승인은 사람만 한다."""
    return await brief_publish.request_brief_publish(
        request, briefs=STATE.briefs, store=STATE.sns, publish=publish_sns
    )


async def get_publish_control_skill(request: AgentRequest) -> AgentResult:
    return await publish_control.get_publish_control(request)


async def set_publish_control_skill(request: AgentRequest) -> AgentResult:
    return await publish_control.set_publish_control(request, from_decision=_from_decision)


async def list_video_providers_skill(request: AgentRequest) -> AgentResult:
    return await video_providers.list_providers(request)


async def select_video_provider_skill(request: AgentRequest) -> AgentResult:
    return await video_providers.select_provider(request, from_decision=_from_decision)


async def save_custom_video_provider_skill(request: AgentRequest) -> AgentResult:
    return await video_providers.save_custom(request, from_decision=_from_decision)


async def delete_custom_video_provider_skill(request: AgentRequest) -> AgentResult:
    return await video_providers.delete_custom(request, from_decision=_from_decision)


async def get_media_cost_limits_skill(request: AgentRequest) -> AgentResult:
    return await cost_limits.get_limits(request, ledger=STATE.media_ledger)


async def lower_media_cost_limits_skill(request: AgentRequest) -> AgentResult:
    return await cost_limits.change(
        request, wanted="down", ledger=STATE.media_ledger, from_decision=_from_decision
    )


async def raise_media_cost_limits_skill(request: AgentRequest) -> AgentResult:
    return await cost_limits.change(
        request, wanted="up", ledger=STATE.media_ledger, from_decision=_from_decision
    )


async def set_cost_limit_option_skill(request: AgentRequest) -> AgentResult:
    return await cost_limits.set_option(
        request, ledger=STATE.media_ledger, from_decision=_from_decision
    )


async def get_manual_publish_package_skill(request: AgentRequest) -> AgentResult:
    return await manual_publish.get_manual_publish_package(request)


async def complete_manual_publish_skill(request: AgentRequest) -> AgentResult:
    return await manual_publish.complete_manual_publish(request, from_decision=_from_decision)


async def publish_sns_skill(request: AgentRequest) -> AgentResult:
    return await publish_sns(request, STATE.sns)


def _chat_failed(request: AgentRequest, refused: ChatRefused) -> AgentResult:
    return _result(
        request, TaskStatus.FAILED, outcome=refused.outcome, output={"message": refused.message}
    )


def _chat_storage_failed(request: AgentRequest) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome="storage_error")


def _chat_done(request: AgentRequest, skill: str, output: dict[str, Any]) -> AgentResult:
    # 대화 기록만 읽고 쓴다. 업무 상태(캠페인·공지·쿠폰)는 바꾸지 않아 execute()를 부르지 않는다
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=output,
        evidence=_evidence([skill], "마케팅 AI 대화"),
    )


async def marketing_chat_send(request: AgentRequest) -> AgentResult:
    data = _parse(request, SendChatMessageInput)
    if isinstance(data, AgentResult):
        return data
    try:
        thread = STATE.chat.send(
            data.who, data.thread_id, data.message, data.record_id, meta=request.meta
        )
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "marketing_chat_send", {"thread": thread})


async def marketing_chat_list(request: AgentRequest) -> AgentResult:
    data = _parse(request, ListChatThreadsInput)
    if isinstance(data, AgentResult):
        return data
    try:
        threads = STATE.chat.list(data.who, data.limit)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "marketing_chat_list", {"threads": threads})


async def marketing_chat_get(request: AgentRequest) -> AgentResult:
    data = _parse(request, ChatThreadRef)
    if isinstance(data, AgentResult):
        return data
    try:
        thread = STATE.chat.get(data.who, data.thread_id)
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "marketing_chat_get", {"thread": thread})


async def marketing_chat_close(request: AgentRequest) -> AgentResult:
    data = _parse(request, ChatThreadRef)
    if isinstance(data, AgentResult):
        return data
    try:
        thread = STATE.chat.close(data.who, data.thread_id)
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "marketing_chat_close", {"thread": thread})


async def marketing_chat_remove(request: AgentRequest) -> AgentResult:
    data = _parse(request, ChatThreadRef)
    if isinstance(data, AgentResult):
        return data
    try:
        STATE.chat.remove(data.who, data.thread_id)
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    out = ChatRemoveOutput(thread_id=data.thread_id, removed=True)
    return _chat_done(request, "marketing_chat_remove", out.model_dump(mode="json"))


def _stored_brief(campaign_id: str) -> StoredBrief | None:
    """받아 둔 기획안. 없거나 저장소를 못 읽으면 None (기획안 조회 실패가 캠페인을 막지 않는다)."""
    try:
        return STATE.briefs.get(None, campaign_id)
    except BriefStorageError:
        return None


def _brief_content(campaign_id: str) -> tuple[str | None, ContentFormat | None]:
    """받아 둔 기획안의 (content_category, content_format). 없거나 못 읽으면 (None, None)."""
    stored = _stored_brief(campaign_id)
    if stored is None:
        return None, None
    return stored.brief.content_category, stored.brief.content_format


async def start_campaign_skill(request: AgentRequest) -> AgentResult:
    """`start_campaign`: 요청이 안 준 상품·카테고리·형식은 기획안 값을 재무 예산 의견에 싣는다.

    한 번 부를 때 기획안은 한 번만 읽는다(상품 ID와 카테고리·형식이 같은 기획안에서 나온다).
    """
    looked: dict[str, StoredBrief | None] = {}

    def brief(campaign_id: str) -> StoredBrief | None:
        if campaign_id not in looked:
            looked[campaign_id] = _stored_brief(campaign_id)
        return looked[campaign_id]

    def product_ids(campaign_id: str) -> list[str]:
        stored = brief(campaign_id)
        return list(stored.brief.product_ids) if stored is not None else []

    def content(campaign_id: str) -> tuple[str | None, ContentFormat | None]:
        stored = brief(campaign_id)
        if stored is None:
            return None, None
        return stored.brief.content_category, stored.brief.content_format

    return await start_campaign(
        request,
        brief_product_ids=product_ids,
        brief_content=content,
        on_started=STATE.campaigns.track,
        started_status=STATE.campaigns.status,
    )


async def list_marketing_campaigns(request: AgentRequest) -> AgentResult:
    """시작한 캠페인 대장(read): 유튜브 영상의 캠페인 고르기·결과 저장 칸 미리 채우기에 쓴다."""
    data = _parse(request, ListMarketingCampaignsInput)
    if isinstance(data, AgentResult):
        return data
    items = [
        MarketingCampaignItem(status=status, **started.model_dump())
        for status, started in STATE.campaigns.list(data.limit)
    ]
    out = ListMarketingCampaignsOutput(items=items, total=len(items))
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=_evidence(
            ["list_marketing_campaigns"],
            f"시작한 캠페인 {out.total}개",
            [i.campaign_id for i in items],
        ),
    )


async def record_campaign_result_skill(request: AgentRequest) -> AgentResult:
    """`record_campaign_result`: 요청이 안 준 카테고리·형식은 기획안 값으로 채운다."""
    return await record_campaign_result(
        request,
        brief_content=_brief_content,
        started=STATE.campaigns.get,
        on_recorded=STATE.campaigns.mark_human_recorded,
    )


async def add_secret_skill(request: AgentRequest) -> AgentResult:
    return await secret_requests.apply_request(
        request, wanted=secret_requests.ADD, from_decision=_from_decision
    )


async def change_secret_skill(request: AgentRequest) -> AgentResult:
    return await secret_requests.apply_request(
        request, wanted=secret_requests.CHANGE, from_decision=_from_decision
    )


HANDLERS = {
    "add_secret": add_secret_skill,
    "change_secret": change_secret_skill,
    "get_media_cost_limits": get_media_cost_limits_skill,
    "lower_media_cost_limits": lower_media_cost_limits_skill,
    "raise_media_cost_limits": raise_media_cost_limits_skill,
    "set_cost_limit_option": set_cost_limit_option_skill,
    "list_video_providers": list_video_providers_skill,
    "select_video_provider": select_video_provider_skill,
    "save_custom_video_provider": save_custom_video_provider_skill,
    "delete_custom_video_provider": delete_custom_video_provider_skill,
    "draft_notice": draft_notice,
    "publish_notice": publish_notice,
    "end_incident_notice": end_incident_notice,
    "get_notice": get_notice,
    "list_active_notices": list_active_notices,
    "channel_followers": channel_followers,
    "character_sheet_prompt": character_sheet_prompt,
    "storyboard_prompt": storyboard_prompt,
    "higgsfield_status": higgsfield_status,
    "creative_records_list": creative_records_list,
    "creative_record_get": creative_record_get,
    "creative_video_request": creative_video_request,
    "creative_youtube_draft": creative_youtube_draft,
    "creative_youtube_request": creative_youtube_request,
    "creative_instagram_draft": creative_instagram_draft,
    "creative_instagram_request": creative_instagram_request,
    "youtube_status": youtube_status,
    "instagram_status": instagram_status,
    "youtube_video_stats": youtube_video_stats,
    "youtube_uploads_list": youtube_uploads_list,
    "creative_record_attach": creative_record_attach,
    "creative_thumbnails_request": creative_thumbnails_request,
    "creative_thumbnail_select": creative_thumbnail_select,
    "brand_characters_list": brand_characters_list,
    "brand_character_mark": brand_character_mark,
    "brand_character_unmark": brand_character_unmark,
    "receive_campaign_brief": receive_campaign_brief,
    "record_campaign_ad_metrics": record_campaign_ad_metrics,
    "get_campaign_ad_metrics": get_campaign_ad_metrics,
    "list_campaign_briefs": list_campaign_briefs,
    "get_campaign_brief": get_campaign_brief,
    "handoff_brief_to_sns": handoff_brief_to_sns,
    "build_campaign_link": build_campaign_link_skill,
    "issue_coupon": issue_coupon,
    "start_campaign": start_campaign_skill,
    "record_campaign_result": record_campaign_result_skill,
    "list_marketing_campaigns": list_marketing_campaigns,
    "draft_sns_post": draft_sns_post_skill,
    "prepare_video_draft_from_plan": prepare_video_draft_from_plan_skill,
    "draft_outage_sns_post": draft_outage_sns_post_skill,
    "list_outage_sns_drafts": list_outage_sns_drafts_skill,
    "list_request_videos": list_request_videos_skill,
    "request_video_youtube_draft": request_video_youtube_draft,
    "request_video_youtube_request": request_video_youtube_request,
    "request_video_instagram_draft": request_video_instagram_draft,
    "request_video_instagram_request": request_video_instagram_request,
    "request_video_cover_select": request_video_cover_select,
    "merge_outage_sns_drafts": merge_outage_sns_drafts_skill,
    "check_sns_post": check_sns_post_skill,
    "publish_sns": publish_sns_skill,
    "get_publish_control": get_publish_control_skill,
    "set_publish_control": set_publish_control_skill,
    "get_manual_publish_package": get_manual_publish_package_skill,
    "complete_manual_publish": complete_manual_publish_skill,
    "cancel_campaign": cancel_campaign,
    "generate_media": generate_media_skill,
    "prepare_sns_post_from_media": prepare_sns_post_from_media_skill,
    "prepare_brief_media": prepare_brief_media_skill,
    "choose_brief_audio": choose_brief_audio_skill,
    "request_brief_publish": request_brief_publish_skill,
    "prepare_outage_sns_media": prepare_outage_sns_media_skill,
    "request_outage_sns_publish": request_outage_sns_publish_skill,
    "marketing_chat_send": marketing_chat_send,
    "marketing_chat_list": marketing_chat_list,
    "marketing_chat_get": marketing_chat_get,
    "marketing_chat_close": marketing_chat_close,
    "marketing_chat_remove": marketing_chat_remove,
}

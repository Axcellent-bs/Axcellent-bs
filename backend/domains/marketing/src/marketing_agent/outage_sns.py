"""장애 사과·보상 SNS 글 초안 (skill `draft_outage_sns_post`, `list_outage_sns_drafts`).

장애가 났을 때 (a) 사과 공지가 나갔거나 (b) 보상 쿠폰을 드렸으면, 총괄이 이 skill을 불러
Instagram 글 *초안*을 바로 만들어 둔다. 여기서 끝이다.
- 게시하지 않는다. 게시는 `publish_sns`(C등급, 항상 사람 승인)로만 한다.
- 미디어를 만들지 않는다(`generate_media` 비용 없음). 캡션만 있는 초안이라 그대로는 게시할 수 없다.
- 쓰는 것은 마케팅 전용 초안 저장소뿐이라 `draft_sns_post`처럼 `execute()`를 부르지 않는다.

원인 설명 규칙(일반인이 읽는 글이다):
- 문장은 코드에 있는 고정 문장뿐이다(LLM·자유 글 없음). 총괄은 장애 기록에서 *코드*만 넘긴다
  (`cause_code`·`action_code`). 원문(배포 이름·버전·지표·담당자)은 받지 않는다.
- 원인이 확정된 경우(`recent_update`: 원인으로 본 업데이트를 되돌렸고 복구 확인까지 끝남)에만
  원인을 말한다. 그 밖에는 "원인 확인 중"이라고 쓴다(미확인 원인을 단정하지 않는다).
- 사과 공지에서 만드는 초안은 마케팅이 게시한 장애·정상화 공지(`notice_id`)가 있어야 하고
  그 공지의 공개 문구를 그대로 옮긴다. 영향 숫자(고객 수·금액)는 적지 않는다.
- 금지 항목 필터(`forbidden_terms`): 기술 용어·서버 이름·IP·버전·계정·토큰 모양이 캡션에 있으면
  초안을 만들지 않는다(`forbidden_term`). 고정 문장만 쓰므로 평소에는 걸리지 않는 이중 잠금이다.
- AI 생성 표시·시연용 가상 서비스·약관 문구는 기존 `draft_sns_post`와 같은 조립 함수를 쓴다.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.common import AxModel, UtcDatetime
from marketing_agent import sns
from marketing_agent.notice import _COMPONENT_LABEL as COMPONENT_LABEL
from marketing_agent.notice import NoticeStore

SOURCE_KIND = "outage"
NEXT_STEPS = ["generate_media", "check_sns_post", "publish_sns"]  # 모두 사람이 이어서 한다
INCIDENT_NOTICE_KINDS = ("incident_opened", "incident_resolved")
DEFAULT_LABEL = "일부 서비스"

Trigger = Literal["apology_notice", "compensation"]
CauseCode = Literal["recent_update", "unknown"]
ActionCode = Literal["reverted_update", "restored", "none"]

# 원인: 확정된 경우에만 말한다. 그 밖에는 솔직하게 "확인 중"
CAUSE_TEXT: dict[str, str] = {
    "recent_update": (
        "원인은 장애 직전에 적용한 서비스 업데이트였습니다. "
        "이 업데이트에 문제가 있어 {label} 이용이 원활하지 않았습니다."
    ),
    "unknown": "정확한 원인은 아직 확인 중입니다. 확인되는 대로 다시 안내드리겠습니다.",
}
# 조치: 복구가 확인된 경우에만 말한다
ACTION_TEXT: dict[str, str] = {
    "reverted_update": "문제가 된 업데이트를 이전 상태로 되돌렸고, 정상 이용을 확인했습니다.",
    "restored": "문제 상황을 바로잡았고, 정상 이용을 확인했습니다.",
    "none": "지금은 정상적으로 이용하실 수 있습니다.",
}
NOT_RECOVERED_TEXT = "복구되는 대로 다시 안내드리겠습니다."
COMPENSATION_TEXT = "불편을 겪으신 고객님께는 보상 쿠폰을 드렸습니다."
APOLOGY_TEXT = "{label} 이용이 원활하지 않아 불편을 드려 죄송합니다."

# 금지 항목: 일반인 글에 나오면 안 되는 내부·기술 표현. (이름, 정규식)
_FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ip_address", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("version", re.compile(r"\bv?\d+\.\d+\.\d+\b", re.IGNORECASE)),
    ("url_or_host", re.compile(r"https?://|\b[\w-]+\.(?:com|net|io|kr|internal|local)\b", re.I)),
    ("email_or_account", re.compile(r"[\w.+-]+@[\w-]+")),
    ("token", re.compile(r"\b(?:sk|pk|ghp|xox[bp]|EA[A-Z])[\w-]{6,}|[A-Za-z0-9_-]{32,}")),
    ("internal_id", re.compile(r"\b(?:ctx|ntc|drf|cmp|spd|usr|rev)_[\w-]+", re.IGNORECASE)),
    (
        "tech_term",
        re.compile(
            r"\b(?:api|db|sql|http|5xx|4xx|p95|latency|timeout|rollback|deploy|commit|pr|"
            r"server|nginx|redis|docker|kubernetes|pod|cpu|memory|log|llm|a2a|zabbix|dejafix)\b"
            r"|롤백|배포|서버|데이터베이스|디비|커밋|지연 시간|실패율|트래픽|모니터링|인프라|"
            r"쿼리|캐시|로그(?!인)|토큰|마이그레이션|장애 주입",
            re.IGNORECASE,
        ),
    ),
    ("percent_or_metric", re.compile(r"\d+(?:\.\d+)?\s?(?:%|ms|건|명)")),
)


def forbidden_terms(text: str) -> list[str]:
    """캡션 본문에서 걸린 금지 항목 이름(없으면 빈 목록)."""
    return [name for name, pattern in _FORBIDDEN if pattern.search(text)]


class DraftOutageSnsPostInput(AxModel):
    """장애 기록에서 온 *코드*만 받는다. 자유 글(원인 원문·버전·지표)은 받지 않는다."""

    trigger: Trigger
    # 사과 공지(apology_notice)면 필수: 마케팅이 게시한 장애·정상화 공지
    notice_id: str | None = Field(default=None, min_length=1, max_length=100)
    component: Literal["api", "db", "payment"] | None = None
    cause_code: CauseCode = "unknown"
    action_code: ActionCode = "none"
    recovered: bool = False  # 복구 확인이 끝났는지
    compensation_issued: bool = False  # 보상 쿠폰이 실제로 나갔는지
    media_kind: Literal["image", "reel"] = "image"

    @model_validator(mode="after")
    def _consistent(self) -> DraftOutageSnsPostInput:
        if self.trigger == "apology_notice" and self.notice_id is None:
            raise ValueError("apology_notice는 notice_id가 필요하다")
        if self.trigger == "compensation" and not self.compensation_issued:
            raise ValueError("compensation은 compensation_issued=true여야 한다")
        if self.cause_code == "recent_update" and not (
            self.recovered and self.action_code == "reverted_update"
        ):
            # 되돌려서 복구가 확인된 경우에만 원인을 확정으로 말한다
            raise ValueError("recent_update는 recovered=true, action_code=reverted_update여야 한다")
        if self.action_code != "none" and not self.recovered:
            raise ValueError("조치는 복구가 확인된 뒤에만 적는다")
        return self


class OutageSnsDraftItem(AxModel):
    post_draft_id: str
    trigger: str | None = None
    context_id: str
    source_ref: str
    caption: str
    media_kind: str
    media_attached: bool
    created_at: UtcDatetime


class ListOutageSnsDraftsInput(AxModel):
    limit: int = Field(default=20, ge=1, le=100)


class OutageSnsDraftList(AxModel):
    items: list[OutageSnsDraftItem] = Field(default_factory=list)
    total: int = 0
    unknown_reason: str | None = None


def build_body(data: DraftOutageSnsPostInput, notice_text: str | None, label: str) -> str:
    """고정 문장으로 본문을 만든다. 숫자·고유명사는 들어가지 않는다."""
    if data.trigger == "compensation":
        lines = ["캣북스 보상 안내", "", APOLOGY_TEXT.format(label=label)]
    else:
        lines = ["캣북스 사과 안내", "", notice_text or APOLOGY_TEXT.format(label=label)]
    lines += ["", CAUSE_TEXT[data.cause_code].format(label=label)]
    lines.append(ACTION_TEXT[data.action_code] if data.recovered else NOT_RECOVERED_TEXT)
    if data.compensation_issued:
        lines += ["", COMPENSATION_TEXT]
    return "\n".join(lines)


def _failed(request: AgentRequest, outcome: str, **output: object) -> AgentResult:
    return AgentResult(
        meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=dict(output)
    )


async def draft_outage_sns_post(
    request: AgentRequest, notices: NoticeStore, store: sns.SnsStore
) -> AgentResult:
    try:
        data = DraftOutageSnsPostInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    notice_text: str | None = None
    notice_kind = None
    component = data.component
    source_ref = request.meta.context_id
    if data.notice_id is not None:
        view = notices.view(data.notice_id)
        if not view.found or view.public_text is None:
            return _failed(request, "notice_not_found")
        if view.kind not in INCIDENT_NOTICE_KINDS:
            return _failed(request, "notice_not_incident")
        notice_text, notice_kind, source_ref = view.public_text, view.kind, data.notice_id
        if view.facts is not None and view.facts.component is not None:
            component = view.facts.component  # 공지에 적힌 사실이 우선이다
    label = COMPONENT_LABEL.get(component or "", DEFAULT_LABEL)

    body = build_body(data, notice_text, label)
    hits = forbidden_terms(body)
    if hits or any(phrase in body for phrase in sns.FORBIDDEN_PHRASES):
        return _failed(request, "forbidden_term", terms=hits)
    caption = sns._compose(body, data.media_kind)
    if len(caption) > sns.CAPTION_MAX:
        return _failed(request, "caption_too_long")

    options = {"share_to_feed": True} if data.media_kind == "reel" else {}
    caption_sha = sns._sha(caption)
    draft = sns.SnsPostDraft(
        post_draft_id=sns.content_id(caption_sha, None, data.media_kind, "instagram", options),
        source_kind=SOURCE_KIND,
        source_ref=source_ref,
        notice_kind=notice_kind,
        platform="instagram",
        media_kind=data.media_kind,
        caption=caption,
        hashtags=list(sns.HASHTAGS),
        caption_sha256=caption_sha,
        required_text=[body, sns.DEMO_TEXT, sns.TERMS_TEXT],
        context_id=request.meta.context_id,
        platform_options=options,
        outage_trigger=data.trigger,
    )
    store.save(draft)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=draft.model_dump(mode="json") | {"auto_publish": False, "next_steps": NEXT_STEPS},
        evidence=sns._evidence(
            ["draft_outage_sns_post"],
            f"장애 {data.trigger} 사실 코드로 고정 문장 캡션 초안 생성(게시하지 않음)",
            [draft.post_draft_id, source_ref],
        ),
        fallback=True,  # LLM 문안은 연결하지 않았다. 고정 문장
    )


async def list_outage_sns_drafts(request: AgentRequest, store: sns.SnsStore) -> AgentResult:
    try:
        data = ListOutageSnsDraftsInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    rows, total = store.list_by_source(SOURCE_KIND, data.limit)
    listing = OutageSnsDraftList(
        items=[
            OutageSnsDraftItem(
                post_draft_id=d.post_draft_id,
                trigger=d.outage_trigger,
                context_id=d.context_id,
                source_ref=d.source_ref,
                caption=d.caption,
                media_kind=d.media_kind,
                media_attached=bool(d.asset_ref or d.assets),
                created_at=created_at,
            )
            for d, created_at in rows
        ],
        total=total,
        unknown_reason=None if rows else "장애 사과·보상 SNS 초안이 없음",
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=listing.model_dump(mode="json"),
        evidence=sns._evidence(
            ["list_outage_sns_drafts"],
            f"장애 SNS 초안 {total}건 조회" if rows else "장애 SNS 초안이 없음",
            [d.post_draft_id for d, _ in rows],
        ),
    )

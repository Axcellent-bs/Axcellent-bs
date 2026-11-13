"""장애 사과·보상 SNS 글 초안 (skill `draft_outage_sns_post`, `list_outage_sns_drafts`,
`merge_outage_sns_drafts`).

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

유사 초안 병합(`merge_outage_sns_drafts`, 규칙 기반 1단계. LLM 없음):
- 새 문장을 만들지 않는다. 같은 계기·공지 종류·미디어 종류이고 같은 출처(`source_ref`) 또는 같은
  한국 날짜에 쌓인, 미디어 없는 초안 중 글이 같거나 거의 같은 것을 묶어 대표 1건만 남긴다
  (가장 최근이면서 점검을 통과한 것). 나머지는 지우지 않고 `status="merged"`,
  `merged_into`로 표시한다.
- 캡션은 바꾸지 않으므로 AI 표시·금지어 점검 결과가 그대로 유효하다. 게시(`publish_sns`)는 건드리지
  않는다. 승인 대기·게시 중·게시된 초안은 제외한다. 같은 병합을 다시 해도 결과가 같다.
- 유사 판정은 `similar_captions`(규칙) 한 곳에 모았다. 이후 LLM 다듬기 단계가 생겨도 이 판정 뒤에
  끼우기만 하면 되고, 후보 묶기·표시·저장 구조는 그대로다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
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
    # 유사 초안 병합 표시. 옛 서버·옛 레코드는 draft·빈 값이다
    status: Literal["draft", "merged"] = "draft"
    merged_into: str | None = None
    merged_from: list[str] = Field(default_factory=list)
    # 카드 진행 상태(`outage_sns_media.flow_state`): 이미지 만들기·게시 요청·승인 대기. 목록
    # 조회 때만 채운다
    media_flow: dict | None = None


class ListOutageSnsDraftsInput(AxModel):
    limit: int = Field(default=20, ge=1, le=100)
    post_draft_id: str | None = Field(default=None, min_length=1, max_length=64)  # 한 건만 조회
    include_merged: bool = False  # true면 병합된 원본(감사 기록)도 보여 준다


class OutageSnsDraftList(AxModel):
    items: list[OutageSnsDraftItem] = Field(default_factory=list)
    total: int = 0  # include_merged가 false면 병합된 원본을 뺀 수
    merged_total: int = 0  # 병합된 원본 수(목록에 넣었든 아니든)
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
    all_rows, _ = store.list_by_source(SOURCE_KIND, None, data.include_merged)
    # 이미지를 붙인 사본(`media_from`)은 원본 카드의 진행 상태로 보여 주므로 목록에서 뺀다
    visible = [
        (d, at)
        for d, at in all_rows
        if not d.media_from and (data.post_draft_id in (None, d.post_draft_id))
    ]
    total = len(visible)
    rows = visible[: data.limit]
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
                status=d.status,
                merged_into=d.merged_into,
                merged_from=d.merged_from,
            )
            for d, created_at in rows
        ],
        total=total,
        merged_total=store.count_merged_by_source(SOURCE_KIND),
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


# --- 유사 초안 병합 (skill `merge_outage_sns_drafts`) ------------------------------------------

# 글이 "거의 같다"고 볼 어절 집합 겹침 비율(자카드). 보수적으로 높게 둔다. 테스트가 이 값을 고정한다
SIMILARITY_THRESHOLD = 0.9
GROUP_MIN = 2
GROUP_MAX = 10
KST = timezone(timedelta(hours=9))

SkipReason = Literal[
    "not_found",
    "not_outage_draft",
    "already_merged",
    "media_attached",
    "check_failed",
    "publish_in_progress_or_done",
    "publish_state_unknown",
]

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


def _tokens(text: str) -> list[str]:
    """글자 정규화: 호환 문자 통일·소문자·구두점은 공백으로·공백과 줄바꿈을 통일한 어절."""
    return _PUNCT.sub(" ", unicodedata.normalize("NFKC", text).lower()).split()


def normalize_caption(text: str) -> str:
    return " ".join(_tokens(text))


def _collapse(text: str) -> str:
    return "".join(_tokens(text))


def _pieces(template: str) -> tuple[str, ...]:
    """고정 문장을 `{label}` 자리 기준으로 쪼갠 조각(공백·구두점 없앤 꼴)."""
    return tuple(p for p in (_collapse(x) for x in template.split("{label}")) if p)


_CAUSE_KEYS = [(code, _pieces(text)) for code, text in CAUSE_TEXT.items()]
_ACTION_KEYS = [
    *((code, _pieces(text)) for code, text in ACTION_TEXT.items()),
    ("not_recovered", _pieces(NOT_RECOVERED_TEXT)),
]
_COMPENSATION_KEY = _pieces(COMPENSATION_TEXT)


def key_signature(caption: str) -> tuple[str | None, str | None, bool]:
    """캡션에 든 (원인 문장, 조치 문장, 보상 문장 여부). 서로 다르면 같은 그룹이 될 수 없다."""
    flat = _collapse(caption)

    def find(keys: list[tuple[str, tuple[str, ...]]]) -> str | None:
        for code, parts in keys:
            if all(part in flat for part in parts):
                return code
        return None

    return find(_CAUSE_KEYS), find(_ACTION_KEYS), all(p in flat for p in _COMPENSATION_KEY)


def similar_captions(a: str, b: str) -> bool:
    """두 캡션이 같거나 거의 같은가(규칙). 원인·조치·보상 핵심 문장이 다르면 항상 아니다.

    같음: 공백·줄바꿈·구두점 정규화 뒤 동일. 거의 같음: 핵심 문장이 모두 같고 어절 집합의
    자카드 비율이 `SIMILARITY_THRESHOLD` 이상(핵심 문장을 못 알아보는 글은 이 경로를 안 쓴다).
    """
    if normalize_caption(a) == normalize_caption(b):
        return True
    sig_a, sig_b = key_signature(a), key_signature(b)
    if sig_a != sig_b or sig_a[0] is None or sig_a[1] is None:
        return False
    set_a, set_b = set(_tokens(a)), set(_tokens(b))
    union = set_a | set_b
    return bool(union) and len(set_a & set_b) / len(union) >= SIMILARITY_THRESHOLD


def passes_checks(draft: sns.SnsPostDraft) -> bool:
    """대표가 될 수 있는 초안인가: 저장된 캡션이 AI 표시·필수 문구·금지어·길이 검사를 통과한다."""
    caption = draft.caption
    body = draft.required_text[0] if draft.required_text else caption
    return (
        draft.is_ai_generated
        and sns.ai_label_text(draft.media_kind) in caption
        and all(text in caption for text in draft.required_text)
        and not forbidden_terms(body)
        and not any(phrase in caption for phrase in sns.FORBIDDEN_PHRASES)
        and len(caption) <= sns.CAPTION_MAX
    )


class MergeOutageSnsDraftsInput(AxModel):
    draft_ids: list[str] | None = Field(default=None, max_length=100)
    dry_run: bool = True  # 기본은 저장하지 않는 미리보기


class MergeGroup(AxModel):
    representative: str  # 남는 대표 초안 id
    merged_from: list[str]  # 이번에 병합 표시되는 원본 id(최근 순)
    representative_caption: str
    trigger: str | None = None


class MergeSkip(AxModel):
    id: str
    reason: SkipReason


class MergeOutageSnsDraftsOutput(AxModel):
    dry_run: bool
    groups: list[MergeGroup] = Field(default_factory=list)
    merged_count: int = 0  # 병합 표시된(dry_run이면 될) 원본 수
    remaining_count: int = 0  # 병합 뒤 목록에 남는 초안 수
    skipped: list[MergeSkip] = Field(default_factory=list)


# 초안 id → 게시 요청 기록(승인 대기·게시 중·게시됨 포함)이 있는가. 읽지 못하면 예외
PublishLookup = Callable[[str], bool]
Row = tuple[sns.SnsPostDraft, datetime]


def _same_bucket(a: sns.SnsPostDraft, b: sns.SnsPostDraft) -> bool:
    return (a.outage_trigger, a.notice_kind, a.media_kind) == (
        b.outage_trigger,
        b.notice_kind,
        b.media_kind,
    )


def _related(a: Row, b: Row) -> bool:
    """같은 출처 또는 같은 한국 날짜."""
    return (
        a[0].source_ref == b[0].source_ref
        or a[1].astimezone(KST).date() == b[1].astimezone(KST).date()
    )


def _skip_reason(d: sns.SnsPostDraft, has_publish_record: PublishLookup) -> SkipReason | None:
    if d.source_kind != SOURCE_KIND or d.outage_trigger is None:
        return "not_outage_draft"
    if d.status == "merged":
        return "already_merged"
    if d.asset_ref or d.assets or d.asset_sha256:
        return "media_attached"
    if not passes_checks(d):
        return "check_failed"
    try:
        if has_publish_record(d.post_draft_id):
            return "publish_in_progress_or_done"
    except Exception:  # noqa: BLE001 - 게시 상태를 읽지 못하면 건드리지 않는다
        return "publish_state_unknown"
    return None


def plan_merge(
    rows: list[Row], *, draft_ids: list[str] | None, has_publish_record: PublishLookup
) -> tuple[list[tuple[sns.SnsPostDraft, list[sns.SnsPostDraft]]], list[MergeSkip]]:
    """(대표, 병합될 원본들) 묶음과 건너뛴 초안. 저장하지 않는다. 같은 입력이면 같은 결과다."""
    by_id = {d.post_draft_id: (d, at) for d, at in rows}
    skipped: list[MergeSkip] = []
    # 이미지를 붙인 사본이 있는 원본은 진행 중인 건이라 병합하지 않는다
    derived_origins = {d.media_from for d, _ in rows if d.media_from}
    if draft_ids is None:
        pool = list(by_id.values())
    else:
        pool = []
        for pid in dict.fromkeys(draft_ids):
            if pid in by_id:
                pool.append(by_id[pid])
            else:
                skipped.append(MergeSkip(id=pid, reason="not_found"))

    candidates: list[Row] = []
    for d, at in pool:
        reason = (
            "media_attached"
            if d.post_draft_id in derived_origins
            else _skip_reason(d, has_publish_record)
        )
        if reason is None:
            candidates.append((d, at))
        elif draft_ids is not None or reason in (
            "check_failed",
            "publish_in_progress_or_done",
            "publish_state_unknown",
        ):
            skipped.append(MergeSkip(id=d.post_draft_id, reason=reason))

    # 최근 순(같으면 id 순)으로 시드를 잡고 시드와 비슷한 것만 모은다(전이 묶기 없음).
    # 시드가 가장 최근이므로 대표다
    candidates.sort(key=lambda x: (-x[1].timestamp(), x[0].post_draft_id))
    used: set[str] = set()
    plans: list[tuple[sns.SnsPostDraft, list[sns.SnsPostDraft]]] = []
    for seed in candidates:
        if seed[0].post_draft_id in used:
            continue
        group = [seed]
        for other in candidates:
            if len(group) >= GROUP_MAX:
                break
            if other[0].post_draft_id in used or other is seed:
                continue
            if (
                _same_bucket(seed[0], other[0])
                and _related(seed, other)
                and similar_captions(seed[0].caption, other[0].caption)
            ):
                group.append(other)
        if len(group) < GROUP_MIN:
            continue
        used.update(d.post_draft_id for d, _ in group)
        plans.append((seed[0], [d for d, _ in group[1:]]))
    return plans, skipped


async def merge_outage_sns_drafts(request: AgentRequest, store: sns.SnsStore) -> AgentResult:
    """유사 장애 초안 병합. 초안 저장소의 메타데이터만 바꾸고 캡션·게시는 건드리지 않는다."""
    from marketing_agent.sns_publish import publisher

    try:
        data = MergeOutageSnsDraftsInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )

    def has_publish_record(post_draft_id: str) -> bool:
        return bool(publisher.store().with_draft(post_draft_id))

    rows, _ = store.list_by_source(SOURCE_KIND, None, include_merged=True)
    plans, skipped = plan_merge(
        rows, draft_ids=data.draft_ids, has_publish_record=has_publish_record
    )
    originals = {d.post_draft_id: d for d, _ in rows}

    groups: list[MergeGroup] = []
    updates: dict[str, sns.SnsPostDraft] = {}
    now = datetime.now(UTC)
    for rep, sources in plans:
        absorbed: list[str] = list(rep.merged_from)
        for source in sources:
            absorbed.append(source.post_draft_id)
            # 이미 다른 초안을 흡수한 원본이면 그 원본들도 새 대표로 넘긴다(연쇄 없이 한 단계로)
            for child_id in source.merged_from:
                absorbed.append(child_id)
                child = originals.get(child_id)
                if child is not None and child.status == "merged":
                    updates[child_id] = child.model_copy(update={"merged_into": rep.post_draft_id})
            updates[source.post_draft_id] = source.model_copy(
                update={
                    "status": "merged",
                    "merged_into": rep.post_draft_id,
                    "merged_from": [],
                    "merged_at": now,
                }
            )
        updates[rep.post_draft_id] = rep.model_copy(
            update={"merged_from": sorted(dict.fromkeys(absorbed))}
        )
        groups.append(
            MergeGroup(
                representative=rep.post_draft_id,
                merged_from=[s.post_draft_id for s in sources],
                representative_caption=rep.caption,
                trigger=rep.outage_trigger,
            )
        )

    merged_count = sum(len(g.merged_from) for g in groups)
    visible = sum(1 for d, _ in rows if d.status != "merged")
    output = MergeOutageSnsDraftsOutput(
        dry_run=data.dry_run,
        groups=groups,
        merged_count=merged_count,
        remaining_count=visible - merged_count,
        skipped=skipped,
    )
    if not data.dry_run and updates:
        try:
            store.update_meta(list(updates.values()))
        except Exception:  # noqa: BLE001 - 하나라도 못 쓰면 전부 되돌려진다
            return _failed(request, "storage_error")
    mode = "미리보기" if data.dry_run else "실행"
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=output.model_dump(mode="json"),
        evidence=sns._evidence(
            ["merge_outage_sns_drafts"],
            f"장애 SNS 초안 유사 병합 {mode}: {len(groups)}묶음, 원본 {merged_count}건"
            "(캡션·게시는 바꾸지 않음)",
            [g.representative for g in groups],
        ),
    )

"""고객 아이디어 창구 skill: `submit_idea`(받기), `list_ideas`(읽기),
`send_idea_digest`(기획에 넘기기).

흐름: 고객이 쇼핑몰에서 "이런 걸 해 주면 좋겠다"를 보낸다 → 불만·문의와 따로 쌓는다 → 사람이 쌓인
묶음(종류·영역)을 보고 고른 것을 기획 `create_ticket`으로 넘긴다 → 기획이 이벤트 요청이나 향후 방안
기획서로 이어 간다. 기획이 개발에 넘기는 일은 기획의 기존 흐름이다.

- 기획에 넘기는 근거 요약에는 고객 원문을 싣지 않는다 (건수·고객 수·기간·많이 나온 단어만).
  그래서 기획은 글 속 문장을 지시로 읽을 수 없고, 원문은 이 목록(상담 화면)에서만 본다.
- 받기와 넘기기는 상태를 바꾸므로 둘 다 `execute()`를 거친다.
  넘기기는 사람 토큰이 있는 화면만 부른다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import Field, ValidationError

from ax_agent_template.execute import execute
from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.idea import (
    IdeaPeriod,
    IdeaReportInput,
    IdeaReportOutput,
    IdeaTrendList,
)
from ax_contracts.schemas.planning import CreateTicketOutput
from support_agent import handlers as h
from support_agent import ideas
from support_agent.ideas import AREA_KO, KIND_KO, IdeaArea, IdeaKind, IdeaStatus

PLANNING = "planning"
DATA = "data"
logger = logging.getLogger("ax.support.ideas")
TICKET_EVIDENCE_IDS = 20  # 기획에 근거로 붙이는 아이디어 번호 수 (나머지는 건수로만)
DEFAULT_MIN_IDEAS = 3  # 이만큼 쌓인 묶음부터 "넘길 만하다"로 표시한다
SEND_LOCK = asyncio.Lock()  # 같은 아이디어를 두 번 넘기지 않게 한 번에 한 요청만


class SubmitIdeaInput(AxModel):
    """고객 아이디어. 글은 `untrusted.text`로만 받는다."""

    kind: IdeaKind
    user_id: int | None = None  # 고객 화면이 대신 부를 때 로그인한 고객 번호


class SubmitIdeaOutput(AxModel):
    idea_id: str
    received: bool = True


class ListIdeasInput(AxModel):
    status: IdeaStatus | None = None
    kind: IdeaKind | None = None
    area: IdeaArea | None = None
    min_ideas: int = Field(default=DEFAULT_MIN_IDEAS, ge=1, le=100)
    limit: int = Field(default=50, ge=1, le=200)


class SendIdeaDigestInput(AxModel):
    """새 아이디어를 기획에 넘긴다. 종류·영역을 주면 그 묶음만, 비우면 기준을 넘은 묶음 전부."""

    kind: IdeaKind | None = None
    area: IdeaArea | None = None
    min_ideas: int = Field(default=DEFAULT_MIN_IDEAS, ge=1, le=100)
    author: str = Field(min_length=1, max_length=80)  # 넘긴 사람. 서버가 사람 토큰에서 채운다


class ListIdeasOutput(AxModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    new_count: int = 0  # 아직 기획에 넘기지 않은 아이디어 수 (거르기와 상관없이 전체)
    groups: list[dict[str, Any]] = Field(default_factory=list)


class SendIdeaDigestOutput(AxModel):
    sent: list[dict[str, Any]] = Field(default_factory=list)  # 묶음마다 request_id 또는 error
    failed: int = 0


class SendIdeaReportInput(AxModel):
    """하루치 이벤트·혜택 요청 보고를 데이터에 넘긴다. 날짜를 비우면 어제(회사 시간대)."""

    report_date: date | None = None


class SendIdeaReportOutput(AxModel):
    report_date: date
    total_ideas: int = 0
    clusters: int = 0
    outcome: str  # accepted | duplicate | no_ideas | rejected | data_unavailable
    reason_code: str | None = None


class ListIdeaTrendsInput(AxModel):
    period: IdeaPeriod = "week"
    buckets: int = Field(default=8, ge=1, le=60)
    top: int = Field(default=5, ge=1, le=20)


def _item(idea: ideas.Idea) -> dict[str, Any]:
    """목록의 한 줄. 고객 번호와 중복 확인 지문은 내보내지 않는다."""
    data = idea.model_dump(mode="json", exclude={"user_id", "text_digest", "tenant_id"})
    data["kind_label"] = KIND_KO[idea.kind]
    data["area_label"] = AREA_KO[idea.area]
    return data


def _group(group: ideas.IdeaGroup, min_ideas: int) -> dict[str, Any]:
    data = group.model_dump(mode="json")
    data["top_words"] = [{"word": w, "count": n} for w, n in group.top_words]
    data["kind_label"] = KIND_KO[group.kind]
    data["area_label"] = AREA_KO[group.area]
    data["ready"] = group.idea_count >= min_ideas
    return data


@h._caller_meta
async def submit_idea(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SubmitIdeaInput)
    if isinstance(parsed, AgentResult):
        return parsed
    if parsed.user_id is None:
        return error_result(
            request.meta, TaskStatus.FAILED, "invalid_input", "아이디어를 낸 고객 번호가 필요하다"
        )
    raw = request.untrusted.get("text")
    text = ideas.clean_text(raw if isinstance(raw, str) else "")
    if len(text) < ideas.MIN_TEXT_CHARS:
        return h._done(
            request, {"message": f"{ideas.MIN_TEXT_CHARS}자 이상 적어 주세요"}, outcome="too_short"
        )
    if ideas.looks_like_spam(text):
        return h._done(request, {"message": "광고·링크가 있는 글은 받지 않는다"}, outcome="spam")
    if ideas.looks_like_complaint(text):
        # 지금 풀어야 하는 문제는 문의로 가야 상담원이 본다. 아이디어함에 두면 놓친다
        return h._done(
            request,
            {"message": "환불·취소·불량 같은 문제는 문의로 접수해 주세요"},
            outcome="use_inquiry",
        )
    tenant = request.meta.tenant_id
    if h.IDEAS.recent_count(tenant, parsed.user_id) >= ideas.DAILY_LIMIT:
        return h._done(
            request, {"message": "하루에 낼 수 있는 아이디어 수를 넘었다"}, outcome="rate_limited"
        )

    async def apply() -> tuple[dict[str, Any], bool]:
        assert parsed.user_id is not None
        idea = h.IDEAS.add(
            tenant_id=tenant,
            context_id=request.meta.context_id,
            user_id=parsed.user_id,
            kind=parsed.kind,
            text=text,
        )
        return SubmitIdeaOutput(idea_id=idea.idea_id).model_dump(mode="json"), False

    return await h._write(
        request,
        "submit_idea",
        f"user:{parsed.user_id}:{ideas.digest(parsed.user_id, text)}",
        {"kind": parsed.kind},
        apply,
    )


async def list_ideas(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, ListIdeasInput)
    if isinstance(parsed, AgentResult):
        return parsed
    tenant = request.meta.tenant_id
    rows = [
        i
        for i in h.IDEAS.all(tenant)
        if (parsed.status is None or i.status == parsed.status)
        and (parsed.kind is None or i.kind == parsed.kind)
        and (parsed.area is None or i.area == parsed.area)
    ]
    rows.sort(key=lambda i: (i.created_at, i.idea_id), reverse=True)
    groups = h.IDEAS.groups(tenant, kind=parsed.kind, area=parsed.area)
    return h._done(
        request,
        {
            "items": [_item(i) for i in rows[: parsed.limit]],
            "total": len(rows),
            "new_count": sum(1 for i in h.IDEAS.all(tenant) if i.status == "new"),
            "groups": [_group(g, parsed.min_ideas) for g in groups],
        },
    )


def _ticket_title(group: ideas.IdeaGroup) -> str:
    return f"[고객 아이디어] {KIND_KO[group.kind]} · {AREA_KO[group.area]} {group.idea_count}건"


def _ticket_evidence(group: ideas.IdeaGroup) -> str:
    first, last = group.first_at[:10], group.last_at[:10]
    period = first if first == last else f"{first}~{last}"
    parts = [f"고객 {group.customer_count}명이 {period}에 낸 아이디어 {group.idea_count}건."]
    if group.top_words:
        parts.append("많이 나온 말: " + ", ".join(f"{w} {n}" for w, n in group.top_words) + ".")
    parts.append("고객 원문은 고객지원 아이디어 목록에서 본다.")
    return " ".join(parts)


async def send_idea_digest(request: AgentRequest) -> AgentResult:
    """쌓인 새 아이디어를 (종류, 영역) 묶음마다 기획 `create_ticket`으로 넘긴다 (grade A).

    - 기준(`min_ideas`)에 못 미친 묶음은 넘기지 않고 남겨 둔다.
      종류·영역을 정해 주면 그 묶음만 본다.
    - 넘길 묶음이 없으면 안전 관문도 부르지 않는다 (`nothing_to_send`).
    - 안전 관문을 한 번 거치고, 묶음마다 기획에 요청 한 건을 만든다. 기획에 닿은 묶음의 아이디어만
      `sent`로 바꾸고, 닿지 못한 묶음은 `new`로 남아 다시 누르면 이어서 넘긴다.
    - 기획이 같은 요청을 이미 갖고 있으면 묶어서(`linked_duplicate`) 같은 REQ를 돌려준다.
    """
    parsed = h._parse(request, SendIdeaDigestInput)
    if isinstance(parsed, AgentResult):
        return parsed
    tenant = request.meta.tenant_id
    async with SEND_LOCK:
        groups = [
            g
            for g in h.IDEAS.groups(tenant, kind=parsed.kind, area=parsed.area)
            if g.idea_count >= parsed.min_ideas
        ]
        if not groups:
            return h._done(
                request,
                {"sent": [], "message": "넘길 만큼 쌓인 아이디어가 없다"},
                outcome="nothing_to_send",
            )
        key = request.meta.idempotency_key or (
            f"{request.meta.context_id}:send_idea_digest:{groups[0].idea_ids[0]}:{len(groups)}"
        )
        decision = await execute(
            "send_idea_digest",
            {"groups": len(groups), "ideas": sum(g.idea_count for g in groups)},
            request.meta.model_copy(update={"idempotency_key": key}),
            Evidence(
                ref_ids=[f"idea:{g.idea_ids[0]}" for g in groups],
                data_summary=f"send_idea_digest {len(groups)}묶음",
            ),
        )
        if decision.status != "executed":
            return h._done(
                request,
                {"execution": decision.model_dump(mode="json")},
                outcome=f"not_executed_{decision.status}",
            )
        sent: list[dict[str, Any]] = []
        for group in groups:
            sent.append(await _send_group(request, group, key, parsed.author))
    refs = [r["request_id"] for r in sent if r.get("request_id")]
    return h._done(
        request,
        {"sent": sent, "failed": sum(1 for r in sent if not r.get("request_id"))},
        evidence=Evidence(
            ref_ids=refs,
            data_summary=f"아이디어 {sum(r['idea_count'] for r in sent)}건 → 기획 {len(refs)}묶음",
        ),
    )


async def _send_group(
    request: AgentRequest, group: ideas.IdeaGroup, key: str, author: str
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": group.kind,
        "area": group.area,
        "idea_count": group.idea_count,
        "customer_count": group.customer_count,
    }
    ticket_key = f"{key}:{group.kind}:{group.area}"
    ticket = AgentRequest(
        meta=request.meta.model_copy(update={"idempotency_key": ticket_key}),
        skill="create_ticket",
        input={
            "title": _ticket_title(group),
            "evidence": _ticket_evidence(group),
            "source_ref": f"idea-digest:{group.kind}:{group.area}:{group.idea_ids[0]}",
            "evidence_ids": [f"idea:{i}" for i in group.idea_ids[:TICKET_EVIDENCE_IDS]],
        },
    )
    try:
        reply = await h.SEND(PLANNING, ticket)
    except Exception:
        logger.warning("idea digest %s/%s not sent to planning", group.kind, group.area)
        return {**base, "error": "planning_unavailable"}
    if reply.status != TaskStatus.COMPLETED or reply.outcome not in ("created", "linked_duplicate"):
        return {**base, "error": f"planning_{reply.outcome or reply.status.value}"}
    try:
        out = CreateTicketOutput.model_validate(reply.output)
    except ValidationError:
        return {**base, "error": "invalid_output"}
    h.IDEAS.mark_sent(group.idea_ids, request.meta.tenant_id, out.request_id, author)
    return {
        **base,
        "request_id": out.request_id,
        "outcome": out.outcome,
        "duplicate_of": out.duplicate_of,
    }


async def send_idea_report(request: AgentRequest) -> AgentResult:
    """하루치 이벤트·혜택 요청을 같은 뜻끼리 묶어 데이터 `receive_idea_report`로 넘긴다 (grade A).

    - 그날 받은 해당 종류 아이디어가 없으면 아무것도 보내지 않는다 (`no_ideas`).
    - 데이터가 같은 날 보고를 이미 받았으면 `duplicate`로 끝난다 (여러 번 불러도 한 번만 받는다).
    - 보고에는 고객 원문 없이 대표 말·건수·고객 수만 싣는다. 데이터에 닿지 못하면
      `data_unavailable`이고 다음 주기에 같은 날을 다시 보내면 이어진다.
    - 데이터는 자기 기록만 쌓고 여기서도 상태를 바꾸지 않아 관문을 거치지 않는다.
    """
    parsed = h._parse(request, SendIdeaReportInput)
    if isinstance(parsed, AgentResult):
        return parsed
    tenant = request.meta.tenant_id
    day = parsed.report_date or (datetime.now(ideas.report_tz()).date() - timedelta(days=1))
    total, clusters = ideas.daily_report(h.IDEAS.all(tenant), day)

    def done(outcome: str, reason: str | None = None) -> AgentResult:
        out = SendIdeaReportOutput(
            report_date=day,
            total_ideas=total,
            clusters=len(clusters),
            outcome=outcome,
            reason_code=reason,
        )
        return h._done(
            request,
            out.model_dump(mode="json"),
            evidence=Evidence(data_summary=f"고객 아이디어 보고 {day} {outcome}"),
        )

    if total == 0:
        return done("no_ideas")
    report = IdeaReportInput(
        report_date=day,
        total_ideas=total,
        clusters=[c.model_dump() for c in clusters],  # type: ignore[arg-type]
    )
    send = AgentRequest(
        meta=request.meta.model_copy(
            update={"idempotency_key": f"{request.meta.context_id}:send_idea_report:{tenant}:{day}"}
        ),
        skill="receive_idea_report",
        input=report.model_dump(mode="json"),
    )
    try:
        reply = await h.SEND(DATA, send)
    except Exception:
        logger.warning("idea report %s not sent to data", day)
        return done("data_unavailable")
    if reply.status != TaskStatus.COMPLETED:
        return done("data_unavailable")
    try:
        received = IdeaReportOutput.model_validate(reply.output)
    except ValidationError:
        return done("data_unavailable")
    return done(received.outcome, received.reason_code)


async def list_idea_trends(request: AgentRequest) -> AgentResult:
    """데이터가 받아 둔 일일 보고를 기간(일·주·월·연)별로 더한 요청 순위 (읽기).

    합산은 데이터가 한다. 여기서는 데이터에서 읽어 그대로 돌려줄 뿐 숫자를 만들지 않는다.
    데이터에 닿지 못하면 빈 결과와 이유를 돌려준다.
    """
    parsed = h._parse(request, ListIdeaTrendsInput)
    if isinstance(parsed, AgentResult):
        return parsed
    ask = AgentRequest(
        meta=request.meta.model_copy(update={"idempotency_key": None}),
        skill="list_idea_reports",
        input=parsed.model_dump(mode="json"),
    )
    try:
        reply = await h.SEND(DATA, ask)
        listing = IdeaTrendList.model_validate(reply.output)
        if reply.status != TaskStatus.COMPLETED:
            raise ValueError(reply.status)
    except Exception:
        logger.warning("idea trends not read from data")
        listing = IdeaTrendList(period=parsed.period, unknown_reason="데이터에 닿지 못함")
    return h._done(request, listing.model_dump(mode="json"))


IDEA_HANDLERS = {
    "submit_idea": h.for_customer_gateway(submit_idea, needs_user=True),
    "list_ideas": list_ideas,
    "send_idea_digest": send_idea_digest,
    "send_idea_report": send_idea_report,
    "list_idea_trends": list_idea_trends,
}

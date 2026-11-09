"""에이전트 평가 화면용 집계(읽기 전용). 카드에 저장된 판정과 단계 시간을 에이전트별로 모아 준다.

점수·등급·순위는 만들지 않는다. 사람이 "제때 끝냈다 / 늦었다"를 바로 읽도록 건수, 이벤트마다의 점,
그리고 늦은 이유(다시 시도했는지, 기준 시간을 넘었는지)만 돌려준다. 값은 카드에 저장된 그대로
쓰고 다시 계산하지 않는다(그때의 기준값 버전이 판정에 남아 있다).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentEventDot,
    AgentRequest,
    AgentResult,
    AgentSummary,
    DailyTrend,
    EvaluateAgentsInput,
    EvaluateAgentsOutput,
    EvaluatedEvent,
    EvaluatedStep,
    RepeatEventType,
)
from hr_agent.case_memory.service import KINDS
from hr_agent.dept_eval.criteria import CriteriaError, load_criteria
from hr_agent.dept_eval.stages import LABELS
from hr_agent.results import completed, failed, invalid_input, make_evidence
from hr_agent.services import Services

# 화면에 보이는 순서. 판정이 없는 에이전트도 "참여한 이벤트 없음"으로 보여 준다
AGENT_ORDER = ("infra", "data", "dev", "support", "marketing", "finance", "hr")

# 한 이벤트 안에서 에이전트의 단계가 여럿일 때 점 하나로 줄이는 우선순위(앞쪽이 더 걸리는 판정)
# 건너뛴 단계(기록 없음)는 다른 단계가 제때 끝났다면 점에 올리지 않는다
# (앞 단계가 안 돌아서 건너뛴 것이지 그 에이전트 탓이 아니다)
WORST_FIRST = ("approval_expired", "blocked", "check", "in_progress", "normal", "no_record")
NEEDS_LOOK = ("check", "blocked", "approval_expired")
KST = timedelta(hours=9)


def _seconds(value: float | None) -> str:
    return f"{value:.0f}초" if value is not None else "알 수 없음"


def reason_of(item: dict[str, Any]) -> str | None:
    """판정 이유를 쉬운 말로. 정상이면 None."""
    verdict = item["verdict"]
    if verdict == "check":
        parts = []
        if item["attempts"] > 1:
            parts.append(f"다시 시도 {item['attempts'] - 1}회")
        work = item.get("work_seconds")
        if work is not None and work > item["limit_seconds"]:
            parts.append(f"{_seconds(work)} 걸림(기준 {_seconds(item['limit_seconds'])})")
        return " · ".join(parts) or "기준을 넘음"
    if verdict == "blocked":
        return {
            "transfer_queued": "스스로 끝내지 않고 사람이 보도록 넘김",
            "rejected_by_human": "사람이 거절해서 끝내지 못함",
            "failed": "실패해서 끝내지 못함",
        }.get(str(item.get("status")), "실패하거나 막혀 끝내지 못함")
    return {
        "approval_expired": "승인을 기다리다 시간이 지나 자동 거절됨",
        "no_record": "건너뛰었거나 시간을 알 수 없음",
        "in_progress": "아직 끝나지 않음",
    }.get(verdict)


def _when(card: Any) -> datetime:
    detected = (card.extra.get("timeline") or {}).get("detected_at")
    if isinstance(detected, str):
        try:
            return datetime.fromisoformat(detected.replace("Z", "+00:00"))
        except ValueError:
            pass
    return card.created_at


def _latest_cards(cards: list[Any]) -> list[Any]:
    """이벤트(context_id)마다 판정이 있는 가장 최근 카드 하나. 새로운 이벤트부터."""
    best: dict[str, Any] = {}
    for card in cards:
        if card.kind not in KINDS or not (card.extra.get("step_judgments") or {}).get("items"):
            continue
        old = best.get(card.context_id)
        if old is None or card.created_at > old.created_at:
            best[card.context_id] = card
    return sorted(best.values(), key=_when, reverse=True)


def _event(card: Any) -> EvaluatedEvent:
    saved = card.extra["step_judgments"]
    waits = {t["step_id"]: t for t in card.extra.get("step_timings") or []}
    steps = []
    for item in saved["items"]:
        timing = waits.get(item["step_id"], {})
        steps.append(
            EvaluatedStep(
                step_id=item["step_id"],
                label=LABELS.get(item["step_id"], item["step_id"]),
                dept=item["dept"],
                group=item["group"],
                verdict=item["verdict"],
                work_seconds=item.get("work_seconds"),
                approval_wait_seconds=timing.get("approval_wait_seconds"),
                human_wait_seconds=timing.get("human_wait_seconds"),
                attempts=item["attempts"],
                limit_seconds=item["limit_seconds"],
                reason=reason_of(item),
            )
        )
    event_type = card.extra.get("event_type")
    return EvaluatedEvent(
        context_id=card.context_id,
        title=card.title,
        event_type=str(event_type) if event_type else None,
        at=_when(card),
        criteria_version=int(saved.get("criteria_version") or 1),
        steps=steps,
    )


def _dot(event: EvaluatedEvent, dept: str) -> AgentEventDot:
    mine = [s for s in event.steps if s.dept == dept]
    if not mine:
        return AgentEventDot(
            context_id=event.context_id, title=event.title, at=event.at, verdict="absent"
        )
    verdict = next(v for v in WORST_FIRST if any(s.verdict == v for s in mine))
    reasons = [s.reason for s in mine if s.verdict == verdict and s.reason]
    if verdict == "check":  # 늦은 단계 이름을 앞에 붙여 어디가 늦었는지 바로 보이게 한다
        reasons = [f"{s.label}: {s.reason}" for s in mine if s.verdict == "check" and s.reason]
    return AgentEventDot(
        context_id=event.context_id,
        title=event.title,
        at=event.at,
        verdict=verdict,
        reason=" / ".join(reasons) or None,
        steps=len(mine),
    )


def _summary(dept: str, events: list[EvaluatedEvent]) -> AgentSummary:
    dots = [_dot(e, dept) for e in events]
    joined = [d for d in dots if d.verdict != "absent"]
    counts = Counter(d.verdict for d in joined)
    steps = [s for e in events for s in e.steps if s.dept == dept]
    timed = [s.work_seconds for s in steps if s.work_seconds is not None]
    check_steps = {(e.context_id, s.step_id): s for e in events for s in e.steps if s.dept == dept}
    retried = {k[0] for k, s in check_steps.items() if s.verdict == "check" and s.attempts > 1}
    slow = {
        k[0]
        for k, s in check_steps.items()
        if s.verdict == "check" and (s.work_seconds or 0) > s.limit_seconds
    }
    by_type: defaultdict[str, int] = defaultdict(int)
    latest_title: dict[str, str] = {}
    types = {e.context_id: e.event_type for e in events}
    for dot in joined:  # recent는 새로운 이벤트부터라 처음 만난 제목이 가장 최근 것이다
        kind = types.get(dot.context_id)
        if dot.verdict in NEEDS_LOOK and kind:
            by_type[kind] += 1
            latest_title.setdefault(kind, dot.title)
    repeats = [
        RepeatEventType(event_type=t, title=latest_title[t], count=n)
        for t, n in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        if n >= 2
    ]
    return AgentSummary(
        dept=dept,
        events=len(joined),
        normal=counts["normal"],
        check=counts["check"],
        no_record=counts["no_record"],
        blocked=counts["blocked"],
        approval_expired=counts["approval_expired"],
        in_progress=counts["in_progress"],
        retried=len(retried & {d.context_id for d in joined if d.verdict == "check"}),
        slow=len(slow & {d.context_id for d in joined if d.verdict == "check"}),
        avg_work_seconds=round(sum(timed) / len(timed), 2) if timed else None,
        max_work_seconds=round(max(timed), 2) if timed else None,
        repeats=repeats,
        recent=dots,
    )


def _trend(events: list[EvaluatedEvent]) -> list[DailyTrend]:
    days: dict[str, list[bool]] = defaultdict(list)
    for event in events:
        if event.at is None:
            continue
        day = (event.at.astimezone(UTC) + KST).date().isoformat()
        days[day].append(any(s.verdict in NEEDS_LOOK for s in event.steps))
    return [
        DailyTrend(day=day, events=len(flags), with_check=sum(flags))
        for day, flags in sorted(days.items())
    ]


async def evaluate_agents(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = EvaluateAgentsInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    try:
        criteria = load_criteria()
    except CriteriaError as exc:
        return failed(request, "CRITERIA_UNREADABLE", str(exc)[:200])
    cards = _latest_cards(await svc.memory.list_cards(states=["active"]))[: params.limit]
    events = [_event(c) for c in cards]
    depts = [*AGENT_ORDER, *sorted({s.dept for e in events for s in e.steps} - set(AGENT_ORDER))]
    output = EvaluateAgentsOutput(
        criteria_version=criteria.version,
        criteria_updated=criteria.updated,
        normal_max_seconds=criteria.default.normal_max_seconds,
        normal_max_attempts=criteria.default.normal_max_attempts,
        events=events,
        agents=[_summary(d, events) for d in depts],
        trend=_trend(events),
        collected_at=svc.clock(),
    )
    return completed(
        request,
        output,
        outcome="listed",
        evidence=make_evidence(
            svc.clock, f"에이전트 평가 집계: 이벤트 {len(events)}건", [c.card_id for c in cards]
        ),
    )

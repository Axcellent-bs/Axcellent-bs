"""부서별 AI 업무 숫자 (activity_report). 순수 함수이며 LLM을 쓰지 않는다.

공통 로그 기록만 센다. 사람이 직접 한 일(로그에 없는 일)은 세지 않는다 — AI를 거친 일만 센다.

정의
- 부서: `ax_agent_template.services`의 `department`. 기록의 `source`(응답한 에이전트 = 서비스 id,
  예전 부서 이름도 인정)로 정한다. safety·dashboard-api·memory 같은 공용 서버는 부서가 아니다.
- 요청: 부서 AI가 보낸 응답 `result_sent` 1건 = 요청 1건.
- 응답 시간: 같은 (source, context_id, skill, requester)의 `request_received` 중 가장 먼저 남은 것과
  `result_sent`를 짝짓는다. 받음이 기간 앞에 있으면 짝이 없어 응답 시간에서 빠진다.
- 결과 6가지 (요청 1건마다 하나):
  1. 확인할 수 없음: outcome이 `unavailable`·`service_disabled`
  2. 실패: 상태 `failed`·`canceled`
  3. 거절: 상태 `rejected`
  4. 보류: 상태 `input-required` (사람 판단·입력 대기)
  5. 완료(`completed`)면 그 요청 안에서 이 부서가 낸 안전 관문 판정(`execute_decided`)을 본다.
     판정은 payload `execution_id`가 같거나, 같은 context·requester(=이 부서)이고
     받음~보냄 사이 시각이면 이 요청 것이다. 여러 개면
     실패 > 거절 > 보류 > 확인할 수 없음 > 사람 확인 후 실행 > 자동 완료 순.
     - executed → 자동 완료, rejected → 거절, failed → 실패
     - held → 사람 결정(`review_approved`)이면 사람 확인 후 실행, `review_rejected`면 거절,
       결정 기록이 없으면 보류
     - verifying(B 교차검증) → 확인할 수 없음 (최종 결과가 공통 로그에 없다)
     판정이 없으면 자동 완료.
- 사람에게 넘김: 꺼진 서비스로 간 요청(`task_status` outcome `handed_to_human`)을 받을 서비스
  (`to_service`)의 부서로 센다. 처리·반려는 dashboard-api의 `handoff_done`·`handoff_rejected`.
  평균 처리 시간은 같은 task_id의 넘김 기록이 이 기간 안에 있을 때만 잰다.
- 템플릿 대체 비율: 완료 응답 중 payload `fallback`이 참인 비율.
  분모에는 LLM을 쓰지 않는 skill도 있다.
- 사건 참여: 부서가 응답한 기록 중 `ctx_inc_` context. MTTR은 `metrics.kpi.compute_kpi` 값.
- 협업: `request_received`의 요청자 부서 → 받은 부서. 요청자가 없는 기록(토큰 거부)은 뺀다.
- 비율은 분모가 3 미만이면 None + "표본 부족".
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ax_agent_template.services import BY_ID, department_of
from ax_contracts.data_access.common_log import LogRecord
from ax_contracts.schemas.data import (
    ActivityHandoffs,
    ActivityIncidents,
    ActivityOutcomes,
    ActivityQuality,
    ActivityRate,
    ActivityRequests,
    CollaborationEdge,
    DepartmentActivity,
    SkillCount,
)
from data_agent.labels import department_label, skill_label
from data_agent.metrics.kpi import INCIDENT_PREFIX, UNKNOWN_OUTCOMES

MIN_DENOMINATOR = 3
SMALL_SAMPLE = "표본 부족"
TOP_SKILLS = 5
HANDED_TO_HUMAN = "handed_to_human"  # ax_contracts.schemas.handoff.HANDED_TO_HUMAN
HANDOFF_DONE, HANDOFF_REJECTED = "handoff_done", "handoff_rejected"
REVIEW_APPROVED, REVIEW_REJECTED = "review_approved", "review_rejected"
NO_RECEIVED_WINDOW = timedelta(seconds=60)  # 받음 기록이 없을 때 판정을 찾는 범위 (보냄 앞)

AUTO, HUMAN, HELD, REJECTED, UNKNOWN, FAILED = (
    "auto_completed",
    "human_approved",
    "held",
    "rejected",
    "unknown",
    "failed",
)
# 한 요청에 판정이 여럿이면 앞쪽이 이긴다
_PRIORITY = (FAILED, REJECTED, HELD, UNKNOWN, HUMAN, AUTO)


def rate(count: int, total: int) -> ActivityRate:
    if total < MIN_DENOMINATOR:
        return ActivityRate(count=count, total=total, rate=None, note=SMALL_SAMPLE)
    return ActivityRate(count=count, total=total, rate=round(count / total, 4))


@dataclass
class Scope:
    """세는 범위: 부서 → 서비스 id 목록."""

    departments: dict[str, tuple[str, ...]]

    def department_of_source(self, source: str | None) -> str | None:
        if not source:
            return None
        service = BY_ID.get(source)
        if service is not None:
            return (
                service.department
                if source in self.departments.get(service.department, ())
                else None
            )
        return source if source in self.departments else None  # 예전 부서 이름으로 남은 기록

    def department_of_service(self, service_id: object) -> str | None:
        if not isinstance(service_id, str):
            return None
        service = BY_ID.get(service_id)
        if service is None or service_id not in self.departments.get(service.department, ()):
            return None
        return service.department


@dataclass
class ActivityComputation:
    departments: dict[str, DepartmentActivity]
    collaboration: list[CollaborationEdge]
    notes: list[tuple[str, str]] = field(default_factory=list)  # (칸, 이유)


def _key(r: LogRecord) -> tuple:
    return (r.source, r.context_id, r.skill, r.requester)


def _pair_received(records: list[LogRecord]) -> dict[str, datetime]:
    """result_sent log_id → 짝이 되는 request_received 시각."""
    pending: dict[tuple, list[datetime]] = defaultdict(list)
    pairs: dict[str, datetime] = {}
    for r in records:
        if r.kind == "request_received":
            pending[_key(r)].append(r.occurred_at)
        elif r.kind == "result_sent" and pending.get(_key(r)):
            pairs[r.log_id] = pending[_key(r)].pop(0)
    return pairs


def _reviews(records: Iterable[LogRecord]) -> dict[str, str]:
    """execution_id → review_approved | review_rejected (사람 결정)."""
    found: dict[str, str] = {}
    for r in records:
        outcome = r.payload.get("outcome")
        exe = r.payload.get("execution_id")
        if r.kind == "task_status" and outcome in (REVIEW_APPROVED, REVIEW_REJECTED) and exe:
            found[str(exe)] = str(outcome)
    return found


def _decision_outcome(decision: LogRecord, reviews: Mapping[str, str]) -> str:
    status = decision.task_status or decision.payload.get("status")
    if status == "executed":
        return AUTO
    if status == "rejected":
        return REJECTED
    if status == "failed":
        return FAILED
    if status == "held":
        review = reviews.get(str(decision.payload.get("execution_id")))
        return {REVIEW_APPROVED: HUMAN, REVIEW_REJECTED: REJECTED}.get(review or "", HELD)
    return UNKNOWN  # verifying 등: 최종 결과가 로그에 없다


def _base_outcome(r: LogRecord) -> str | None:
    """판정을 보지 않고 정해지는 결과. 완료면 None(판정을 본다)."""
    if r.payload.get("outcome") in UNKNOWN_OUTCOMES:
        return UNKNOWN
    status = r.task_status or r.payload.get("status")
    if status in ("failed", "canceled"):
        return FAILED
    if status == "rejected":
        return REJECTED
    if status == "input-required":
        return HELD
    if status == "completed":
        return None
    return UNKNOWN  # working 등: 끝나지 않은 응답


def _median_minutes(seconds: list[int]) -> float | None:
    return round(statistics.median(seconds) / 60, 1) if seconds else None


def _skills(counter: Counter[str], limit: int | None = None) -> list[SkillCount]:
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [SkillCount(skill=s, label=skill_label(s), count=n) for s, n in ordered[:limit]]


def compute_activity(
    records: list[LogRecord], scope: Scope, mttr_seconds: Mapping[str, int | None]
) -> ActivityComputation:
    """기간 안 기록(시각 오름차순)으로 부서별 숫자와 협업 목록을 센다."""
    records = sorted(records, key=lambda r: r.occurred_at)
    received_at = _pair_received(records)
    reviews = _reviews(records)
    decisions = [r for r in records if r.kind == "execute_decided"]
    used: set[str] = set()
    notes: list[tuple[str, str]] = []

    results: dict[str, list[LogRecord]] = defaultdict(list)
    for r in records:
        if r.kind == "result_sent" and (dept := scope.department_of_source(r.source)):
            results[dept].append(r)

    def linked(r: LogRecord) -> list[LogRecord]:
        exe = r.payload.get("execution_id")
        start = received_at.get(r.log_id, r.occurred_at - NO_RECEIVED_WINDOW)
        found = []
        for d in decisions:
            if d.log_id in used:
                continue
            same_exe = exe is not None and d.payload.get("execution_id") == exe
            in_window = (
                d.requester == r.source
                and d.context_id == r.context_id
                and start <= d.occurred_at <= r.occurred_at
            )
            if same_exe or in_window:
                used.add(d.log_id)
                found.append(d)
        return found

    handed: dict[str, tuple[str, datetime]] = {}  # task_id → (부서, 넘긴 시각)
    handoffs: dict[str, dict[str, list]] = defaultdict(
        lambda: {"handed": [], "done": [], "rej": []}
    )
    for r in records:
        if r.kind != "task_status":
            continue
        outcome = r.payload.get("outcome")
        dept = scope.department_of_service(r.payload.get("to_service"))
        if dept is None:
            continue
        task_id = r.payload.get("task_id")
        if outcome == HANDED_TO_HUMAN:
            handoffs[dept]["handed"].append(r)
            if task_id:
                handed[str(task_id)] = (dept, r.occurred_at)
        elif outcome in (HANDOFF_DONE, HANDOFF_REJECTED):
            handoffs[dept]["done" if outcome == HANDOFF_DONE else "rej"].append(r)

    departments: dict[str, DepartmentActivity] = {}
    for dept in scope.departments:
        sent = results.get(dept, [])
        outcomes: Counter[str] = Counter()
        verifying = 0
        for r in sent:
            outcome = _base_outcome(r)
            if outcome is None:
                found = [_decision_outcome(d, reviews) for d in linked(r)]
                outcome = next((p for p in _PRIORITY if p in found), AUTO)
                if outcome == UNKNOWN:
                    verifying += 1
            outcomes[outcome] += 1
        if verifying:
            notes.append(
                (
                    f"departments.{dept}.outcomes.unknown",
                    f"B 등급 교차검증 {verifying}건은 최종 결과가 공통 로그에 없어 확인할 수 없음",
                )
            )
        total = len(sent)

        completed = [r for r in sent if (r.task_status or r.payload.get("status")) == "completed"]
        replaced = [r for r in completed if r.payload.get("fallback") is True]
        reasons = Counter(str(r.payload.get("fallback_reason") or "unknown") for r in replaced)
        waits = [
            (r.occurred_at - received_at[r.log_id]).total_seconds() * 1000
            for r in sent
            if r.log_id in received_at
        ]

        incident_skills: Counter[str] = Counter()
        incident_ids: set[str] = set()
        for r in sent:
            if r.context_id and r.context_id.startswith(INCIDENT_PREFIX):
                incident_ids.add(r.context_id)
                if r.skill:
                    incident_skills[r.skill] += 1
        mttrs = [m for c in incident_ids if (m := mttr_seconds.get(c)) is not None]

        h = handoffs.get(dept, {"handed": [], "done": [], "rej": []})
        closes = [
            (r.occurred_at - handed[str(r.payload.get("task_id"))][1]).total_seconds() / 60
            for r in h["done"] + h["rej"]
            if str(r.payload.get("task_id")) in handed
        ]
        departments[dept] = DepartmentActivity(
            requests=ActivityRequests(
                total=total,
                top_skills=_skills(Counter(r.skill for r in sent if r.skill), TOP_SKILLS),
            ),
            outcomes=ActivityOutcomes(**{k: rate(outcomes[k], total) for k in _PRIORITY}),
            handoffs=ActivityHandoffs(
                handed=len(h["handed"]),
                done=len(h["done"]),
                rejected=len(h["rej"]),
                avg_close_minutes=round(sum(closes) / len(closes), 1) if closes else None,
            ),
            quality=ActivityQuality(
                fallback=rate(len(replaced), len(completed)),
                fallback_reasons=dict(sorted(reasons.items())),
                avg_response_ms=round(sum(waits) / len(waits), 1) if waits else None,
                response_samples=len(waits),
            ),
            incidents=ActivityIncidents(
                count=len(incident_ids),
                skills=_skills(incident_skills),
                mttr_median_minutes=_median_minutes(mttrs),
            ),
        )

    return ActivityComputation(departments, collaboration(records, scope), notes)


def collaboration(records: list[LogRecord], scope: Scope) -> list[CollaborationEdge]:
    """A2A 요청(`request_received`) 기준 요청한 부서 → 받은 부서 → 횟수 (많은 순)."""
    edges: Counter[tuple[str, str]] = Counter()
    for r in records:
        if r.kind != "request_received" or not r.requester:
            continue
        to_dept = scope.department_of_source(r.source)
        if to_dept is None:
            continue
        edges[(department_of(r.requester), to_dept)] += 1
    ordered = sorted(edges.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        CollaborationEdge(
            from_department=a,
            from_label=department_label(a),
            to_department=b,
            to_label=department_label(b),
            count=n,
        )
        for (a, b), n in ordered
    ]

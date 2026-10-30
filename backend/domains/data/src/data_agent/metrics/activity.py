"""부서별 AI 업무 숫자 (activity_report). 순수 함수이며 LLM을 쓰지 않는다.

공통 로그 기록만 센다. 사람이 직접 한 일(로그에 없는 일)은 세지 않는다 — AI를 거친 일만 센다.

정의
- 부서: `ax_agent_template.services`의 `department`. 기록의 `source`(응답한 에이전트 = 서비스 id,
  예전 부서 이름도 인정)로 정한다. safety·dashboard-api·memory 같은 공용 서버는 부서가 아니다.
- 요청: 부서 AI가 보낸 응답 `result_sent` 1건 = 요청 1건.
- 응답 시간: `result_sent` payload의 `duration_ms`(agent-template이 받은 직후부터 결과까지 잰 값)를
  쓴다. 없으면(예전 로그) 같은 (source, context_id, skill, requester)의 `request_received` 중 가장
  먼저 남은 것과 짝지어 시각 차이를 쓴다. 받음이 기간 앞에 있으면 짝이 없어 응답 시간에서 빠진다.
- 받음·보냄 짝: payload `request_id`가 있으면 그것으로 정확히 짝짓는다. 없으면 위 순서 짝짓기.
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
from datetime import UTC, datetime, timedelta

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
from data_agent.metrics.kpi import (
    INCIDENT_PREFIX,
    RECOVERY_SKILL,
    RESOLVED_EVENT,
    UNKNOWN_OUTCOMES,
)

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
    incident_count: int = 0  # 기간 안 사건(`ctx_inc_`) 수 (`kpi_summary`와 같은 정의)


# ---------------------------------------------------------------- 더할 수 있는 값
# 일간 저장(`report_store`)과 바로 계산이 같은 값을 쓴다. 비율·평균은 저장하지 않고
# 분자·분모, 합계·개수, 중앙값용 목록만 둔다. 여러 날을 `merge`로 더한 뒤 `finish_activity`가
# 비율·평균·중앙값을 다시 계산한다(평균의 평균이 아니다).


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _earliest(a: datetime | None, b: datetime | None) -> datetime | None:
    present = [x for x in (a, b) if x is not None]
    return min(present) if present else None


@dataclass
class SourceCounts:
    """응답한 에이전트(source = 서비스 id, 예전 부서 이름) 하나의 값."""

    skills: Counter[str] = field(default_factory=Counter)  # 응답한 요청의 skill별 수
    outcomes: Counter[str] = field(default_factory=Counter)  # 결과 6가지별 수
    verifying: int = 0  # 결과를 확인할 수 없는 B 교차검증 수
    completed: int = 0  # 완료 응답 수 (템플릿 대체 분모)
    replaced: int = 0  # 템플릿으로 대체한 완료 응답 수
    reasons: Counter[str] = field(default_factory=Counter)  # 대체 이유별 수
    wait_sum_ms: float = 0.0  # 응답 시간 합계
    wait_count: int = 0  # 응답 시간 개수
    incident_skills: dict[str, Counter[str]] = field(default_factory=dict)  # 사건 → skill별 수

    def add(self, other: SourceCounts) -> None:
        self.skills.update(other.skills)
        self.outcomes.update(other.outcomes)
        self.verifying += other.verifying
        self.completed += other.completed
        self.replaced += other.replaced
        self.reasons.update(other.reasons)
        self.wait_sum_ms += other.wait_sum_ms
        self.wait_count += other.wait_count
        for ctx, skills in other.incident_skills.items():
            self.incident_skills.setdefault(ctx, Counter()).update(skills)

    def to_json(self) -> dict:
        return {
            "skills": dict(self.skills),
            "outcomes": dict(self.outcomes),
            "verifying": self.verifying,
            "completed": self.completed,
            "replaced": self.replaced,
            "reasons": dict(self.reasons),
            "wait_sum_ms": self.wait_sum_ms,
            "wait_count": self.wait_count,
            "incident_skills": {c: dict(s) for c, s in self.incident_skills.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> SourceCounts:
        return cls(
            skills=Counter(data.get("skills") or {}),
            outcomes=Counter(data.get("outcomes") or {}),
            verifying=int(data.get("verifying") or 0),
            completed=int(data.get("completed") or 0),
            replaced=int(data.get("replaced") or 0),
            reasons=Counter(data.get("reasons") or {}),
            wait_sum_ms=float(data.get("wait_sum_ms") or 0.0),
            wait_count=int(data.get("wait_count") or 0),
            incident_skills={c: Counter(s) for c, s in (data.get("incident_skills") or {}).items()},
        )


@dataclass
class ActivityCounts:
    """기간(또는 하루)의 더할 수 있는 값. 부서로 묶는 것은 `finish_activity`가 한다."""

    sources: dict[str, SourceCounts] = field(default_factory=dict)
    # 받을 서비스 → [(task_id, 넘긴 시각)] / [(task_id, handoff_done|handoff_rejected, 끝낸 시각)]
    handed: dict[str, list[tuple[str | None, datetime]]] = field(default_factory=dict)
    closed: dict[str, list[tuple[str | None, str, datetime]]] = field(default_factory=dict)
    # 사건 → [첫 기록, 첫 incident.resolved 발행, 첫 verify_recovery 완료] (MTTR 중앙값용 목록)
    incidents: dict[str, list[datetime | None]] = field(default_factory=dict)
    collab: Counter[tuple[str, str]] = field(default_factory=Counter)  # (요청 부서, 받은 source)
    log_count: int = 0
    truncated: bool = False

    def merge(self, other: ActivityCounts) -> ActivityCounts:
        """두 기간을 더한 새 값 (원래 값은 바꾸지 않는다)."""
        merged = ActivityCounts.from_json(self.to_json())
        for src, counts in other.sources.items():
            merged.sources.setdefault(src, SourceCounts()).add(counts)
        for svc, items in other.handed.items():
            merged.handed.setdefault(svc, []).extend(items)
        for svc, closes in other.closed.items():
            merged.closed.setdefault(svc, []).extend(closes)
        for ctx, (first, resolved, verified) in other.incidents.items():
            have = merged.incidents.setdefault(ctx, [None, None, None])
            have[0] = _earliest(have[0], first)
            have[1] = _earliest(have[1], resolved)
            have[2] = _earliest(have[2], verified)
        merged.collab.update(other.collab)
        merged.log_count += other.log_count
        merged.truncated = merged.truncated or other.truncated
        return merged

    def mttr_seconds(self) -> dict[str, int | None]:
        """사건별 MTTR (`compute_kpi`와 같다: resolved 발행 → 없으면 verify 완료, − 첫 기록)."""
        out: dict[str, int | None] = {}
        for ctx, (first, resolved, verified) in self.incidents.items():
            recovered = resolved or verified
            out[ctx] = int((recovered - first).total_seconds()) if recovered and first else None
        return out

    def to_json(self) -> dict:
        return {
            "sources": {s: c.to_json() for s, c in self.sources.items()},
            "handed": {s: [[t, _iso(a)] for t, a in v] for s, v in self.handed.items()},
            "closed": {s: [[t, o, _iso(a)] for t, o, a in v] for s, v in self.closed.items()},
            "incidents": {
                c: [_iso(x) if x else None for x in v] for c, v in self.incidents.items()
            },
            "collab": [[a, b, n] for (a, b), n in sorted(self.collab.items())],
            "log_count": self.log_count,
            "truncated": self.truncated,
        }

    @classmethod
    def from_json(cls, data: dict) -> ActivityCounts:
        handed: dict[str, list[tuple[str | None, datetime]]] = {}
        for svc, items in (data.get("handed") or {}).items():
            handed[svc] = [(t, a) for t, raw in items if (a := _at(raw)) is not None]
        closed: dict[str, list[tuple[str | None, str, datetime]]] = {}
        for svc, items in (data.get("closed") or {}).items():
            closed[svc] = [(t, o, a) for t, o, raw in items if (a := _at(raw)) is not None]
        return cls(
            sources={s: SourceCounts.from_json(c) for s, c in (data.get("sources") or {}).items()},
            handed=handed,
            closed=closed,
            incidents={c: [_at(x) for x in v] for c, v in (data.get("incidents") or {}).items()},
            collab=Counter({(a, b): n for a, b, n in data.get("collab") or []}),
            log_count=int(data.get("log_count") or 0),
            truncated=bool(data.get("truncated")),
        )


def _key(r: LogRecord) -> tuple:
    return (r.source, r.context_id, r.skill, r.requester)


def _request_id(r: LogRecord) -> str | None:
    value = r.payload.get("request_id")
    return value if isinstance(value, str) and value else None


def _duration_ms(r: LogRecord) -> int | None:
    """agent-template이 잰 처리 시간. 없거나 형식이 이상하면 None (예전 로그)."""
    value = r.payload.get("duration_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _pair_received(records: list[LogRecord]) -> dict[str, datetime]:
    """result_sent log_id → 짝이 되는 request_received 시각.

    `request_id`가 있으면 같은 id끼리, 없으면(예전 로그) 같은 키에서 먼저 받은 것부터 짝짓는다.
    """
    by_id: dict[str, datetime] = {}
    pending: dict[tuple, list[datetime]] = defaultdict(list)
    pairs: dict[str, datetime] = {}
    for r in records:
        request_id = _request_id(r)
        if r.kind == "request_received":
            if request_id:
                by_id[request_id] = r.occurred_at
            else:
                pending[_key(r)].append(r.occurred_at)
        elif r.kind == "result_sent":
            if request_id:
                if request_id in by_id:
                    pairs[r.log_id] = by_id.pop(request_id)
            elif pending.get(_key(r)):
                pairs[r.log_id] = pending[_key(r)].pop(0)
    return pairs


def _response_ms(r: LogRecord, received_at: Mapping[str, datetime]) -> float | None:
    """응답 시간(ms): `duration_ms`가 있으면 그것, 없으면 짝지은 받음과의 시각 차이."""
    if (duration := _duration_ms(r)) is not None:
        return float(duration)
    if r.log_id in received_at:
        return (r.occurred_at - received_at[r.log_id]).total_seconds() * 1000
    return None


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


def count_activity(records: list[LogRecord], truncated: bool = False) -> ActivityCounts:
    """기록(한 기간)에서 더할 수 있는 값을 센다. 부서·범위로 거르지 않는다."""
    records = sorted(records, key=lambda r: r.occurred_at)
    received_at = _pair_received(records)
    reviews = _reviews(records)
    decisions = [r for r in records if r.kind == "execute_decided"]
    used: set[str] = set()
    counts = ActivityCounts(log_count=len(records), truncated=truncated)

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

    for r in records:
        if r.context_id and r.context_id.startswith(INCIDENT_PREFIX):
            times = counts.incidents.setdefault(r.context_id, [None, None, None])
            times[0] = _earliest(times[0], r.occurred_at)
            if r.kind == "event_published" and r.payload.get("event_type") == RESOLVED_EVENT:
                times[1] = _earliest(times[1], r.occurred_at)
            if r.kind == "result_sent" and r.skill == RECOVERY_SKILL:
                if r.task_status == "completed":
                    times[2] = _earliest(times[2], r.occurred_at)
        if r.kind == "request_received" and r.requester:
            counts.collab[(department_of(r.requester), r.source)] += 1
        if r.kind == "task_status":
            outcome = r.payload.get("outcome")
            service = r.payload.get("to_service")
            task_id = r.payload.get("task_id")
            task = str(task_id) if task_id else None
            if isinstance(service, str) and outcome == HANDED_TO_HUMAN:
                counts.handed.setdefault(service, []).append((task, r.occurred_at))
            elif isinstance(service, str) and outcome in (HANDOFF_DONE, HANDOFF_REJECTED):
                counts.closed.setdefault(service, []).append((task, str(outcome), r.occurred_at))
        if r.kind != "result_sent":
            continue
        c = counts.sources.setdefault(r.source, SourceCounts())
        if r.skill:
            c.skills[r.skill] += 1
        outcome = _base_outcome(r)
        if outcome is None:
            found = [_decision_outcome(d, reviews) for d in linked(r)]
            outcome = next((p for p in _PRIORITY if p in found), AUTO)
            if outcome == UNKNOWN:
                c.verifying += 1
        c.outcomes[outcome] += 1
        if (r.task_status or r.payload.get("status")) == "completed":
            c.completed += 1
            if r.payload.get("fallback") is True:
                c.replaced += 1
                c.reasons[str(r.payload.get("fallback_reason") or "unknown")] += 1
        if (wait := _response_ms(r, received_at)) is not None:
            c.wait_sum_ms += wait
            c.wait_count += 1
        if r.context_id and r.context_id.startswith(INCIDENT_PREFIX):
            skills = c.incident_skills.setdefault(r.context_id, Counter())
            if r.skill:
                skills[r.skill] += 1
    return counts


def finish_activity(counts: ActivityCounts, scope: Scope) -> ActivityComputation:
    """더한 값으로 부서별 숫자·협업 목록을 만든다. 비율·평균·중앙값은 여기서 다시 계산한다."""
    notes: list[tuple[str, str]] = []
    mttr = counts.mttr_seconds()
    handed_at = {
        task: at for items in counts.handed.values() for task, at in items if task is not None
    }
    departments: dict[str, DepartmentActivity] = {}
    for dept in scope.departments:
        c = SourceCounts()
        for src, part in counts.sources.items():
            if scope.department_of_source(src) == dept:
                c.add(part)
        handed = [
            item
            for svc, items in counts.handed.items()
            if scope.department_of_service(svc) == dept
            for item in items
        ]
        closed = [
            item
            for svc, items in counts.closed.items()
            if scope.department_of_service(svc) == dept
            for item in items
        ]
        if c.verifying:
            notes.append(
                (
                    f"departments.{dept}.outcomes.unknown",
                    f"B 등급 교차검증 {c.verifying}건은 최종 결과가 "
                    "공통 로그에 없어 확인할 수 없음",
                )
            )
        total = sum(c.outcomes.values())
        closes = [
            (at - handed_at[task]).total_seconds() / 60
            for task, _, at in closed
            if task is not None and task in handed_at
        ]
        incident_skills: Counter[str] = Counter()
        for skills in c.incident_skills.values():
            incident_skills.update(skills)
        mttrs = [m for ctx in c.incident_skills if (m := mttr.get(ctx)) is not None]
        departments[dept] = DepartmentActivity(
            requests=ActivityRequests(total=total, top_skills=_skills(c.skills, TOP_SKILLS)),
            outcomes=ActivityOutcomes(**{k: rate(c.outcomes[k], total) for k in _PRIORITY}),
            handoffs=ActivityHandoffs(
                handed=len(handed),
                done=sum(1 for _, o, _ in closed if o == HANDOFF_DONE),
                rejected=sum(1 for _, o, _ in closed if o == HANDOFF_REJECTED),
                avg_close_minutes=round(sum(closes) / len(closes), 1) if closes else None,
            ),
            quality=ActivityQuality(
                fallback=rate(c.replaced, c.completed),
                fallback_reasons=dict(sorted(c.reasons.items())),
                avg_response_ms=round(c.wait_sum_ms / c.wait_count, 1) if c.wait_count else None,
                response_samples=c.wait_count,
            ),
            incidents=ActivityIncidents(
                count=len(c.incident_skills),
                skills=_skills(incident_skills),
                mttr_median_minutes=_median_minutes(mttrs),
            ),
        )
    return ActivityComputation(
        departments, collaboration(counts, scope), notes, incident_count=len(counts.incidents)
    )


def compute_activity(records: list[LogRecord], scope: Scope) -> ActivityComputation:
    """기간 안 기록으로 부서별 숫자와 협업 목록을 센다 (`count_activity` → `finish_activity`)."""
    return finish_activity(count_activity(records), scope)


def collaboration(counts: ActivityCounts, scope: Scope) -> list[CollaborationEdge]:
    """A2A 요청(`request_received`) 기준 요청한 부서 → 받은 부서 → 횟수 (많은 순)."""
    edges: Counter[tuple[str, str]] = Counter()
    for (from_dept, source), n in counts.collab.items():
        to_dept = scope.department_of_source(source)
        if to_dept is not None:
            edges[(from_dept, to_dept)] += n
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

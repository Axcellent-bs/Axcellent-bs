"""근무 상태·휴가: 휴가 기간 기록(`record_leave`)과 상태 읽기(`get_presence`).

AI 사무실 머리 위 표시(근무 중 · 교대 외 · 휴가 중)의 재료다. 만들지 않는 것: 출퇴근 시각,
접속 중 표시, 휴가 사유·종류.

- `record_leave`: 휴가 기간(서울 날짜, 끝 날짜 포함)만 저장한다. 안전 관문(`execute()`)을 거치고,
  요청자는 dashboard-api(사람 토큰)뿐이다. 같은 사람·같은 기간을 다시 접수하면 처음 것을 돌려준다.
- `get_presence`: 교대 명부와 휴가를 합쳐 상태를 계산한다. 휴가가 먼저다(휴가 기간이면 교대
  안이어도 휴가 중). 교대 안이면 근무 중, 아니면 교대 외. 휴가 기간·입력자는 `include_details`일
  때만 담는다(보여 줄 사람을 정하는 것은 dashboard-api다).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    GetPresenceInput,
    GetPresenceOutput,
    LeaveView,
    PresenceEntry,
    RecordLeaveInput,
    RecordLeaveOutput,
)
from hr_agent.results import (
    completed,
    gate_blocked,
    invalid_input,
    make_evidence,
    refused,
    with_key,
)
from hr_agent.roster import store as roster
from hr_agent.services import Services
from hr_agent.util import stable_id

SEOUL = timezone(timedelta(hours=9))


def seoul_date(moment: datetime) -> str:
    return moment.astimezone(SEOUL).date().isoformat()


def _view(row: dict[str, Any]) -> LeaveView:
    return LeaveView.model_validate({k: row[k] for k in LeaveView.model_fields if k in row})


def leaves_on(svc: Services, day: str) -> list[dict[str, Any]]:
    """그 서울 날짜가 기간 안에 드는 휴가(양 끝 포함)."""
    return svc.store.all(
        "SELECT * FROM leaves WHERE start_date <= ? AND end_date >= ?"
        " ORDER BY start_date, member_id, leave_id",
        (day, day),
    )


def _on_shift(svc: Services, area: str, member_id: str, now: datetime) -> bool:
    return any(
        datetime.fromisoformat(r["start_at"]) <= now < datetime.fromisoformat(r["end_at"])
        for r in roster.shifts(svc.store, area)
        if r["member_id"] == member_id
    )


async def record_leave(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = RecordLeaveInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(
            request, "같은 요청을 두 번 처리하지 않도록 요청 번호(idempotency_key)가 필요해요"
        )
    if not roster.active_member(svc.store, params.member_id):
        return refused(request, "UNKNOWN_MEMBER", "명부에 없거나 퇴직한 사람이에요")

    leave_id = stable_id("lv_", request.meta.idempotency_key)
    existing = svc.store.one("SELECT * FROM leaves WHERE leave_id = ?", (leave_id,)) or (
        svc.store.one(
            "SELECT * FROM leaves WHERE member_id = ? AND start_date = ? AND end_date = ?",
            (params.member_id, params.start_date, params.end_date),
        )
    )
    if existing is not None:  # 같은 요청이거나 같은 기간이 이미 기록돼 있다
        svc.audit.record(request.meta.context_id, "leave.duplicate", {"id": existing["leave_id"]})
        return completed(
            request,
            RecordLeaveOutput(leave=_view(existing)),
            outcome="duplicate",
            evidence=make_evidence(svc.clock, "이미 기록된 휴가", [existing["leave_id"]]),
        )

    summary = f"휴가 기간 기록: {params.start_date} ~ {params.end_date}"
    evidence = make_evidence(svc.clock, summary, [leave_id])
    decision = await svc.gate.execute(
        "record_leave",
        {"leave_id": leave_id, "summary": summary, "target": leave_id},
        with_key(request.meta, f"record_leave:{leave_id}"),
        evidence,
    )
    if decision.status != "executed":
        return gate_blocked(request, decision)

    svc.store.execute(
        "INSERT INTO leaves(leave_id, context_id, member_id, start_date, end_date,"
        " recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            leave_id,
            request.meta.context_id,
            params.member_id,
            params.start_date,
            params.end_date,
            params.recorded_by,
            svc.clock().isoformat(),
        ),
    )
    # 누구의 휴가인지·기간은 감사 로그에 남기지 않는다(휴가 ID만)
    svc.audit.record(request.meta.context_id, "leave.recorded", {"id": leave_id})
    row = svc.store.one("SELECT * FROM leaves WHERE leave_id = ?", (leave_id,))
    assert row is not None
    return completed(
        request, RecordLeaveOutput(leave=_view(row)), outcome="recorded", evidence=evidence
    )


async def get_presence(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = GetPresenceInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if params.area is not None and params.area not in roster.list_areas(svc.store):
        return invalid_input(request, f"알 수 없는 영역: {params.area}")

    now = params.at or svc.clock()
    on_leave_rows = leaves_on(svc, seoul_date(now))
    on_leave = {r["member_id"] for r in on_leave_rows}
    entries: list[PresenceEntry] = []
    for e in roster.entries(svc.store, params.area):
        if e.member_id in on_leave:
            status = "on_leave"
        elif _on_shift(svc, e.area, e.member_id, now):
            status = "working"
        else:
            status = "off_shift"
        entries.append(
            PresenceEntry(area=e.area, role=e.role, member_id=e.member_id, status=status)
        )
    leaves = [_view(r) for r in on_leave_rows] if params.include_details else []
    output = GetPresenceOutput(entries=entries, leaves=leaves, collected_at=svc.clock())
    return completed(
        request,
        output,
        outcome="presence",
        evidence=make_evidence(svc.clock, f"근무 상태 조회: 담당 {len(entries)}건"),
    )

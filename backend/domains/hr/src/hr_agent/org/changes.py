"""조직 변동(팀 이동·입사·퇴사) 기록: 초안 저장 → 인사 담당자 확정·반려 → 확정만 팀 인원에 센다.

- `record_org_change`: 초안을 저장한다. 승인은 필요 없고 인원에는 아직 세지 않는다.
- `decide_org_change`: 인사 담당자(대시보드 사람 토큰)가 확정하거나 반려한다. 확정하기 전에 앞선
  확정 기록과 맞는지 전체를 다시 적용해 보고(`ledger.validate`), 맞지 않으면 확정하지 않는다.
- `list_org_changes`: 조회 전용. 명부 등록 상태(등록된 직원 수, 전 직원 등록 완료 여부)도 돌려준다.
- `set_org_roster_complete`: 인사 담당자가 "전 직원 등록 완료"를 표시한다. 확정된 명부가 전체 직원
  수와 같을 때만 표시할 수 있고, 표시해야 `team_headcount`가 확정 기록으로 센다.
팀 이름은 조직도(`org.yaml`)에 있는 것만 받는다. 같은 발령을 두 번 접수하면 처음 것을 돌려준다.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    DecideOrgChangeInput,
    DecideOrgChangeOutput,
    ListOrgChangesInput,
    ListOrgChangesOutput,
    OrgChangeView,
    RecordOrgChangeInput,
    RecordOrgChangeOutput,
    SetOrgRosterCompleteInput,
    SetOrgRosterCompleteOutput,
)
from hr_agent.clients.memory import card_state
from hr_agent.org import ledger
from hr_agent.org.roster import EXPECTED_MEMBER_COUNT, org_teams
from hr_agent.results import (
    completed,
    gate_blocked,
    invalid_input,
    make_evidence,
    refused,
    with_key,
)
from hr_agent.services import Services
from hr_agent.util import stable_id


def _view(row: dict[str, Any]) -> OrgChangeView:
    return OrgChangeView.model_validate({k: row[k] for k in OrgChangeView.model_fields if k in row})


def _change(row: dict[str, Any]) -> ledger.Change:
    return ledger.Change(
        row["change_id"],
        row["member_id"],
        row["kind"],
        row["from_team"],
        row["to_team"],
        row["counts_from_month"],
    )


def get(svc: Services, change_id: str) -> dict[str, Any] | None:
    return svc.store.one("SELECT * FROM org_changes WHERE change_id = ?", (change_id,))


def confirmed(svc: Services) -> list[ledger.Change]:
    """확정된 기록을 접수 순서대로. 인원 계산의 유일한 재료다."""
    rows = svc.store.all(
        "SELECT * FROM org_changes WHERE status = 'confirmed' ORDER BY created_at, rowid"
    )
    return [_change(r) for r in rows]


_COMPLETE_KEY = "roster_complete"
_LAST_MONTH = "9999-12"


def roster_complete(svc: Services) -> bool:
    row = svc.store.one("SELECT value FROM org_state WHERE key = ?", (_COMPLETE_KEY,))
    return row is not None and row["value"] == "1"


def registered_members(svc: Services) -> int:
    """확정 기록으로 지금(가장 늦은 기록까지 반영) 명부에 있는 직원 수."""
    try:
        return len(ledger.roster_at(confirmed(svc), _LAST_MONTH))
    except ledger.LedgerError:
        return 0


async def record_org_change(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = RecordOrgChangeInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")

    teams = set(org_teams())
    unknown = [t for t in (params.from_team, params.to_team) if t and t not in teams]
    if unknown:
        return refused(request, "UNKNOWN_TEAM", f"조직도에 없는 팀이다: {', '.join(unknown)}")

    basis_title: str | None = None
    if params.basis_card_id:
        card = await svc.memory.get_card(params.basis_card_id)
        if card is None:
            return refused(request, "UNKNOWN_CARD", "없는 근거 카드다")
        if card.kind != "decision" or card_state(card) != "active":
            return refused(request, "NOT_BASIS_CARD", "확정된 업무 결정 카드만 근거로 쓸 수 있다")
        basis_title = card.title

    change_id = stable_id("orgchg_", request.meta.idempotency_key)
    existing = get(svc, change_id) or svc.store.one(
        "SELECT * FROM org_changes WHERE member_id = ? AND kind = ? AND effective_date = ?"
        " AND COALESCE(from_team, '') = ? AND COALESCE(to_team, '') = ? AND status != 'rejected'",
        (
            params.member_id,
            params.kind,
            params.effective_date,
            params.from_team or "",
            params.to_team or "",
        ),
    )
    if existing is not None:  # 같은 요청이거나 같은 발령이 이미 접수돼 있다
        svc.audit.record(
            request.meta.context_id, "org_change.duplicate", {"id": existing["change_id"]}
        )
        return completed(
            request,
            RecordOrgChangeOutput(change=_view(existing)),
            outcome="duplicate",
            evidence=make_evidence(svc.clock, "이미 접수된 발령", [existing["change_id"]]),
        )

    month = ledger.counts_from_month(params.effective_date)
    summary = (
        f"조직 변동 초안: {params.kind} {params.effective_date} (인원에는 {month}부터, 확정 전)"
    )
    evidence = make_evidence(svc.clock, summary, [change_id])
    decision = await svc.gate.execute(
        "record_org_change",
        {"change_id": change_id, "summary": summary, "target": change_id},
        with_key(request.meta, f"record_org_change:{change_id}"),
        evidence,
    )
    if decision.status != "executed":
        return gate_blocked(request, decision)

    svc.store.execute(
        "INSERT INTO org_changes(change_id, context_id, member_id, kind, from_team, to_team,"
        " effective_date, counts_from_month, status, note, created_at, basis_card_id, basis_title)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
        (
            change_id,
            request.meta.context_id,
            params.member_id,
            params.kind,
            params.from_team,
            params.to_team,
            params.effective_date,
            month,
            params.note,
            svc.clock().isoformat(),
            params.basis_card_id,
            basis_title,
        ),
    )
    # 직원 ID는 감사 로그에 남기지 않는다(발령 ID만)
    svc.audit.record(request.meta.context_id, "org_change.drafted", {"id": change_id})
    row = get(svc, change_id)
    assert row is not None
    return completed(
        request, RecordOrgChangeOutput(change=_view(row)), outcome="drafted", evidence=evidence
    )


async def decide_org_change(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = DecideOrgChangeInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")
    row = get(svc, params.change_id)
    if row is None:
        return refused(request, "UNKNOWN_CHANGE", "없는 발령 기록이다")

    target_status = "confirmed" if params.decision == "approve" else "rejected"
    summary = f"조직 변동 {params.decision}: {row['kind']}"
    evidence = make_evidence(svc.clock, summary, [params.change_id])
    if row["status"] == target_status:  # 같은 결정이 이미 반영됐다
        svc.audit.record(
            request.meta.context_id, "org_change.decide_duplicate", {"id": params.change_id}
        )
        return completed(
            request,
            DecideOrgChangeOutput(change=_view(row)),
            outcome="duplicate",
            evidence=evidence,
        )
    if row["status"] != "draft":
        return refused(request, "NOT_DRAFT", f"초안이 아니다: {row['status']}")

    if params.decision == "approve":
        try:
            ledger.validate([*confirmed(svc), _change(row)])
        except ledger.LedgerError as exc:
            return refused(request, "LEDGER_CONFLICT", str(exc))

    target = f"{params.change_id}#{params.decision}"
    decision = await svc.gate.execute(
        "decide_org_change",
        {"change_id": params.change_id, "summary": summary, "target": target},
        with_key(request.meta, f"decide_org_change:{target}"),
        evidence,
    )
    if decision.status != "executed":
        return gate_blocked(request, decision)

    svc.store.execute(
        "UPDATE org_changes SET status = ?, decided_at = ?"
        " WHERE change_id = ? AND status = 'draft'",
        (target_status, svc.clock().isoformat(), params.change_id),
    )
    svc.audit.record(
        request.meta.context_id,
        f"org_change.{target_status}",
        {"id": params.change_id, "note": params.note},
    )
    updated = get(svc, params.change_id)
    assert updated is not None
    return completed(
        request,
        DecideOrgChangeOutput(change=_view(updated)),
        outcome="confirmed" if params.decision == "approve" else "rejected",
        evidence=evidence,
    )


async def list_org_changes(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = ListOrgChangesInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    sql = "SELECT * FROM org_changes"
    args: list[Any] = []
    if params.status:
        sql += " WHERE status = ?"
        args.append(params.status)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(params.limit)
    rows = svc.store.all(sql, args)
    output = ListOrgChangesOutput(
        changes=[_view(r) for r in rows],
        registered_members=registered_members(svc),
        expected_members=EXPECTED_MEMBER_COUNT,
        roster_complete=roster_complete(svc),
        collected_at=svc.clock(),
    )
    return completed(
        request,
        output,
        outcome="org_changes",
        evidence=make_evidence(svc.clock, f"발령 기록 {len(rows)}건"),
    )


async def set_org_roster_complete(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = SetOrgRosterCompleteInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")

    registered = registered_members(svc)
    already = roster_complete(svc)
    if params.complete and registered != EXPECTED_MEMBER_COUNT:
        return refused(
            request,
            "ROSTER_COUNT_MISMATCH",
            f"확정된 명부가 {registered}명이다. 전체 직원 {EXPECTED_MEMBER_COUNT}명을 모두 "
            "등록하고 확정한 뒤에 완료로 표시할 수 있다",
        )
    summary = f"전 직원 등록 완료 {'표시' if params.complete else '해제'} ({registered}명)"
    evidence = make_evidence(svc.clock, summary)
    output = SetOrgRosterCompleteOutput(
        roster_complete=params.complete,
        registered_members=registered,
        expected_members=EXPECTED_MEMBER_COUNT,
    )
    if already == params.complete:
        return completed(request, output, outcome="duplicate", evidence=evidence)

    target = f"roster_complete#{int(params.complete)}"
    decision = await svc.gate.execute(
        "set_org_roster_complete",
        {"summary": summary, "target": target},
        with_key(request.meta, f"set_org_roster_complete:{target}"),
        evidence,
    )
    if decision.status != "executed":
        return gate_blocked(request, decision)
    svc.store.execute(
        "INSERT OR REPLACE INTO org_state(key, value, updated_at) VALUES (?, ?, ?)",
        (_COMPLETE_KEY, "1" if params.complete else "0", svc.clock().isoformat()),
    )
    svc.audit.record(request.meta.context_id, "org_roster.complete", {"complete": params.complete})
    return completed(
        request,
        output,
        outcome="roster_complete" if params.complete else "roster_reopened",
        evidence=evidence,
    )

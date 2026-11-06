"""발령 기록으로 어느 달에 누가 어느 팀인지 계산한다 (저장소를 읽지 않는 순수 함수).

규칙(재무와 합의): 발령일이 1일이면 그 달부터, 아니면 다음 달 1일부터 센다.
기록은 `(세는 첫 달, 접수 순서)`로 차례대로 적용한다. 이동·퇴사는 지금 그 팀에 있는 사람에게만,
입사는 아직 명부에 없는 사람에게만 맞는다. 맞지 않으면 `LedgerError`다.
"""

from __future__ import annotations

from dataclasses import dataclass


class LedgerError(ValueError):
    """기록이 앞선 기록과 맞지 않는다 (없는 사람의 이동, 이미 있는 사람의 입사 등)."""


@dataclass(frozen=True)
class Change:
    change_id: str
    member_id: str
    kind: str  # transfer | join | leave
    from_team: str | None
    to_team: str | None
    counts_from_month: str  # YYYY-MM


def counts_from_month(effective_date: str) -> str:
    """발령일(YYYY-MM-DD)이 인원에 세지는 첫 달(YYYY-MM)."""
    year, month, day = (int(part) for part in effective_date.split("-"))
    if day == 1:
        return f"{year:04d}-{month:02d}"
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}-{month:02d}"


def _apply(roster: dict[str, str], change: Change) -> None:
    current = roster.get(change.member_id)
    if change.kind == "join":
        if current is not None:
            raise LedgerError(
                f"{change.member_id}는 이미 직원 명단에 있어서 입사로 기록할 수 없어요"
            )
        assert change.to_team is not None
        roster[change.member_id] = change.to_team
    elif change.kind == "leave":
        if current is None or current != change.from_team:
            raise LedgerError(
                f"{change.member_id}는 {change.from_team}에 없어서 퇴사로 기록할 수 없어요"
            )
        del roster[change.member_id]
    else:
        if current is None or current != change.from_team:
            raise LedgerError(
                f"{change.member_id}는 {change.from_team}에 없어서 팀 이동으로 기록할 수 없어요"
            )
        assert change.to_team is not None
        roster[change.member_id] = change.to_team


def ordered(changes: list[Change]) -> list[Change]:
    """세는 첫 달 순, 같은 달이면 주어진(접수) 순서를 지킨다."""
    return sorted(changes, key=lambda c: c.counts_from_month)


def roster_at(changes: list[Change], month: str) -> dict[str, str]:
    """`month`의 1일 기준 직원 → 팀. 그 달까지 세는 기록만 적용한다."""
    roster: dict[str, str] = {}
    for change in ordered(changes):
        if change.counts_from_month <= month:
            _apply(roster, change)
    return roster


def validate(changes: list[Change]) -> None:
    """전체 기록을 차례로 적용해 본다. 맞지 않으면 `LedgerError`."""
    roster: dict[str, str] = {}
    for change in ordered(changes):
        _apply(roster, change)


def earliest_month(changes: list[Change]) -> str | None:
    return min((c.counts_from_month for c in changes), default=None)

"""재무 이상 건 목록: 사후 감사가 찾은 의심 건을 사람이 처리할 때까지 남긴다.

사후 감사는 최근 N일(기본 7일)만 계산하지만, 한 번 찾은 의심 건은 기간이 지나도 사라지지 않는다.
사람이 재무 화면에서 "처리 완료"를 누르면(메모 필수) 열린 목록에서 빠진다.

- 같은 의심 건(종류 + 관련 번호)은 하나로 본다. 다시 찾으면 `last_seen`만 바꾼다
- 처리한 건은 같은 의심 건이 다시 나와도 다시 열지 않는다 (처리 기록이 남아 있으므로)
- 총괄에 알리는 이벤트(`finance.anomaly_detected`)는 건마다 한 번만 보낸다(`announced`)
- 돈을 돌려받거나 고객을 막는 일은 하지 않는다. 기록과 처리 메모뿐이다
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from finance_agent import finance_config as fc

OPEN, RESOLVED = "open", "resolved"
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
RESOLVED_SHOWN = 20


def signature(finding: dict[str, Any]) -> str:
    """의심 건 번호. 종류와 관련 번호가 같으면 같은 건이다."""
    raw = "|".join([str(finding.get("code")), *sorted(str(r) for r in finding.get("refs") or [])])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def note_findings(
    store: dict[str, dict[str, Any]], findings: list[dict[str, Any]], now: datetime | None = None
) -> list[dict[str, Any]]:
    """찾은 의심 건을 목록에 넣는다. 아직 총괄에 알리지 않은 열린 건(finding + `finding_ref`)."""
    at = fc.iso(now or fc.now())
    pending = []
    for finding in findings:
        sig = signature(finding)
        entry = store.get(sig)
        if entry is None:
            entry = store[sig] = {
                "finding_ref": sig,
                "code": finding.get("code"),
                "severity": finding.get("severity"),
                "title": finding.get("title"),
                "detail": finding.get("detail"),
                "refs": list(finding.get("refs") or []),
                "status": OPEN,
                "first_seen": at,
                "last_seen": at,
                "announced": False,
                "resolved_by": None,
                "resolved_at": None,
                "note": None,
            }
        else:
            entry["last_seen"] = at
        if entry["status"] == OPEN and not entry["announced"]:
            pending.append(finding | {"finding_ref": sig})
    return pending


def mark_announced(store: dict[str, dict[str, Any]], refs: list[str]) -> None:
    for ref in refs:
        if ref in store:
            store[ref]["announced"] = True


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k != "announced"}


def listing(store: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """열린 건 전부(기간 제한 없음, 심각한 것·오래된 것 먼저)와 최근 처리한 건."""
    opened = sorted(
        (e for e in store.values() if e["status"] == OPEN),
        key=lambda e: (SEVERITY_ORDER.get(str(e["severity"]), 3), e["first_seen"]),
    )
    resolved = sorted(
        (e for e in store.values() if e["status"] == RESOLVED),
        key=lambda e: e["resolved_at"] or "",
        reverse=True,
    )[:RESOLVED_SHOWN]
    return {
        "open": [_public(e) for e in opened],
        "open_count": len(opened),
        "resolved": [_public(e) for e in resolved],
    }


def resolve(
    store: dict[str, dict[str, Any]], ref: str, person: str, note: str
) -> dict[str, Any] | None:
    """사람이 처리 완료로 닫는다. 없는 건이면 None. 이미 닫힌 건은 처음 기록을 그대로 둔다."""
    entry = store.get(ref)
    if entry is None:
        return None
    if entry["status"] == OPEN:
        entry.update(status=RESOLVED, resolved_by=person, resolved_at=fc.iso(fc.now()), note=note)
    return _public(entry)


def finding_view(entry: dict[str, Any]) -> dict[str, Any]:
    """목록의 한 건을 사후 감사 결과(`AuditFinding`) 모양으로."""
    return {
        "code": entry["code"],
        "severity": entry["severity"],
        "title": entry["title"],
        "detail": entry["detail"],
        "refs": entry["refs"],
    }

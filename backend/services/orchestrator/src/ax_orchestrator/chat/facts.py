"""총괄 대화에 넣는 사실. 총괄의 이벤트 기록(단계·시간·승인 대기)을 읽기만 한다.

숫자·상태는 여기서 코드가 모은다. LLM은 이것을 풀어 쓸 뿐이다.
총괄이 아직 준비되지 않았으면(`manager`가 없음) 모른다고 남긴다. 0으로 채우지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

RECENT = 8  # 최근 이벤트 수
TEXT_CHARS = 120


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:TEXT_CHARS]


def _secs(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return round(
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1
        )
    except ValueError:
        return None


def _step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": _short(step.get("label")),
        "dept": step.get("dept"),
        "status": step.get("status"),
        "started_at": step.get("started_at"),
        "finished_at": step.get("finished_at"),
        "seconds": _secs(step.get("started_at"), step.get("finished_at")),
        "grade": step.get("grade"),
        "note": _short(step.get("note")),
    }


def _incident(view: dict[str, Any]) -> dict[str, Any]:
    steps = [_step(s) for s in view.get("steps", [])]
    return {
        "id": view.get("context_id"),
        "title": _short(view.get("title")),
        "status": view.get("status"),
        "opened_at": view.get("opened_at"),
        "closed_at": view.get("closed_at"),
        "recovered_at": view.get("recovered_at"),
        "steps": steps,
        "slowest_step": max(
            (s for s in steps if s["seconds"] is not None),
            key=lambda s: s["seconds"],
            default=None,
        ),
        "waiting_approval": [
            {
                "label": _short(h.get("label")),
                "since": h.get("since"),
                "expires_at": h.get("expires_at"),
            }
            for h in view.get("pending_holds", [])
        ],
        "waiting_human_task": [
            {"label": _short(h.get("label")), "unit": h.get("assignee_unit")}
            for h in view.get("pending_handoffs", [])
        ],
        "summary": [_short(x) for x in view.get("summary", [])][:5],
    }


def collect(manager: Any) -> dict[str, Any]:
    """총괄 이벤트 기록을 사실로. 총괄이 준비되지 않았으면 `unavailable`만 담는다."""
    if manager is None:
        return {"unavailable": ["총괄이 아직 준비되지 않아 이벤트 기록을 읽지 못했다"]}
    views = manager.listing()
    recent = views[-RECENT:][::-1]  # 최근 순
    # 대응 중(open) 또는 총괄 대응은 끝났지만 쇼핑몰이 아직 안 풀린(degraded, 복구 신호 없음) 이벤트
    active = [
        v
        for v in views
        if v.get("status") == "open"
        or (v.get("status") == "degraded" and not v.get("recovered_at"))
    ]
    return {
        "event_count": len(views),
        "active_events": [_incident(v) for v in active][:RECENT],
        "recent_events": [_incident(v) for v in recent],
        "waiting_approval_total": sum(len(v.get("pending_holds", [])) for v in views),
        "waiting_human_task_total": sum(len(v.get("pending_handoffs", [])) for v in views),
    }

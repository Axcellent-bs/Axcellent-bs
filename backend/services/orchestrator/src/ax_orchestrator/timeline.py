"""이벤트 시각: 총괄이 이미 아는 사실에서 감지·원인 후보 도착·사람 작업·정상 복귀 시각을 뽑는다.

사람이 입력하는 값은 없다. 시각은 단계가 끝난 때(`finished_at`), 사람 할 일함이 끝난 때, 쇼핑몰이
정상 복귀를 알린 때(`shop.recovered`)다. 모르는 시각은 만들지 않고 비워 둔다.
- 감지: 이벤트가 열린 시각
- 원인 후보 도착: AI가 원인 후보나 같은 유형의 과거 근거를 처음 내놓은 시각
- 사람 작업 시작: 사람에게 원인 확인·조치를 넘긴 시각(이관 큐·원인 확인 요청 중 가장 이른 것)
- 사람 작업 완료: 사람 할 일함에서 끝난 시각(알 수 있을 때만)
- 정상 복귀: 쇼핑몰이 알린 시각
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ax_orchestrator.recommend import recommend

if TYPE_CHECKING:
    from ax_orchestrator.models import Incident


def times_of(inc: Incident) -> dict[str, datetime]:
    out: dict[str, datetime] = {"detected_at": inc.opened_at}
    candidates: list[datetime] = []
    root = inc.steps.get("root_cause")
    if (
        root is not None
        and root.status == "completed"
        and root.finished_at
        and (inc.outputs.get("root_cause") or {}).get("hypotheses")
    ):
        candidates.append(root.finished_at)
    similar = inc.steps.get("similar_cases")
    found = (recommend(inc.outputs.get("similar_cases")) or {}).get("status") == "found"
    if similar is not None and similar.status == "completed" and similar.finished_at and found:
        candidates.append(similar.finished_at)
    if candidates:
        out["cause_candidate_at"] = min(candidates)

    started = [
        run.finished_at
        for run in inc.steps.values()
        if run.status == "transfer_queued" and run.finished_at
    ]
    ticket = inc.steps.get("cause_check")
    if ticket is not None and ticket.status == "completed" and ticket.finished_at:
        started.append(ticket.finished_at)
    started += [w.started_at for w in inc.handoffs.values()]
    if started:
        out["human_work_started_at"] = min(started)
    elif inc.status == "degraded" and inc.closed_at:
        out["human_work_started_at"] = inc.closed_at

    waits = list(inc.handoffs.values())
    if waits and not any(w.pending for w in waits) and all(w.finished_at for w in waits):
        out["human_work_done_at"] = max(w.finished_at for w in waits if w.finished_at)
    if inc.recovered_at:
        out["recovered_at"] = inc.recovered_at
    return out


def prior_case_of(inc: Incident) -> str | None:
    """시작할 때 근거로 본 같은 유형의 과거 이벤트 카드(반복 장애). 없으면 None."""
    rec = recommend(inc.outputs.get("similar_cases")) or {}
    return rec.get("card_id") if rec.get("status") == "found" else None


def past_hint(inc: Incident) -> str | None:
    """사람에게 넘기는 글에 붙일 한 줄: 같은 유형의 과거 이벤트가 남긴 원인·조치(참고용)."""
    rec: dict[str, Any] = recommend(inc.outputs.get("similar_cases")) or {}
    if rec.get("status") != "found":
        return None
    pct = round((rec.get("similarity") or 0) * 100)
    return (
        f"같은 유형의 과거 이벤트(유사도 {pct}%)에서는 "
        f"원인 {rec.get('root_cause') or '확인할 수 없음'}, "
        f"조치 {rec.get('action') or '확인할 수 없음'} (참고용, 이번 원인으로 확정된 것 아님)"
    )


def timeline_view(inc: Incident) -> dict[str, Any]:
    """화면용: 시각은 ISO 문자열, 모르면 None."""
    times = times_of(inc)
    names = (
        "detected_at",
        "cause_candidate_at",
        "human_work_started_at",
        "human_work_done_at",
        "recovered_at",
    )
    return {
        **{n: times[n].isoformat() if n in times else None for n in names},
        "prior_case_id": prior_case_of(inc),
    }

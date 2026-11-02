"""해결 내용 초안: 총괄이 이미 아는 것만 옮겨 사람이 확인할 초안을 만든다.

- 같은 유형의 해결된 과거 이벤트가 있으면(반복 장애) 그 이벤트의 원인과 조치를 초안으로 쓴다.
  이번 이벤트에서 AI가 찾은 후보(데이터분석 AI의 원인 후보)는 칸에 넣지 않고 따로 참고로 둔다.
- 처음 겪는 장애는 이번 이벤트의 원인 후보 중 확신이 가장 높은 것과 끝난 롤백을 쓴다.
- 이번 이벤트에서 끝난 롤백은 사실이라 반복 이벤트에서도 조치 칸에 우선한다.
아는 게 없으면 초안을 만들지 않는다(없는 값을 지어내지 않는다). 초안은 저장되지 않고, 사람이 확인해
눌러야 남는다. 과거 이벤트의 원인이 이번 원인과 다를 수 있어 "같은지 확인해 달라"고 표시한다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ax_orchestrator.recommend import recommend

if TYPE_CHECKING:
    from ax_orchestrator.models import Incident

_RANK = {"high": 0, "mid": 1, "low": 2}
_KST = timezone(timedelta(hours=9))
_ISO_UTC = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z")


def kst_text(text: str) -> str:
    """원인 후보 문장 속 UTC 시각(2026-10-30T06:17:54Z)을 한국 시간으로 바꿔 읽기 쉽게 한다."""

    def one(m: re.Match[str]) -> str:
        at = datetime(*(int(g) for g in m.groups()), tzinfo=UTC).astimezone(_KST)
        return f"{at.month}월 {at.day}일 {at:%H:%M:%S}(한국 시간)"

    return _ISO_UTC.sub(one, text)


_CONFIDENCE_KO = {"high": "높음", "mid": "보통", "low": "낮음"}


def _this_event(inc: Incident) -> tuple[str | None, str | None, list[str]]:
    """이번 이벤트에서 알게 된 원인 후보와 끝난 롤백."""
    root_cause, basis = None, []
    found = inc.outputs.get("root_cause") or {}
    hypotheses = [h for h in found.get("hypotheses") or [] if h.get("explanation")]
    if hypotheses:
        top = min(hypotheses, key=lambda h: _RANK.get(str(h.get("confidence")), 3))
        root_cause = kst_text(str(top["explanation"]))
        level = _CONFIDENCE_KO.get(str(top.get("confidence")), "알 수 없음")
        basis.append(f"원인은 데이터분석 AI의 원인 후보 중 확신이 가장 높은 것(확신도 {level})")
    action = None
    rollback = inc.steps.get("rollback")
    if rollback is not None and rollback.status == "completed":
        out = inc.outputs.get("rollback") or {}
        service = out.get("service") or "배포"
        before, after = out.get("from_version"), out.get("to_version")
        action = f"{service} 롤백" + (f" ({before} → {after})" if before and after else "")
        basis.append("조치는 총괄이 끝낸 롤백")
    return root_cause, action, basis


def resolution_draft(inc: Incident) -> dict[str, Any] | None:
    this_cause, this_action, this_basis = _this_event(inc)
    recovery = inc.steps.get("recovery")
    outcome = "resolved" if recovery is not None and recovery.status == "completed" else None
    past = recommend(inc.outputs.get("similar_cases")) or {}
    if past.get("status") == "found":
        pct = round((past.get("similarity") or 0) * 100)
        basis = [
            f"같은 유형의 과거 이벤트(유사도 {pct}%)에서 가져온 원인과 조치예요. "
            "이번 이벤트의 원인과 같은지 확인해 주세요"
        ]
        if this_action:
            basis.append("조치는 이번 이벤트에서 총괄이 끝낸 롤백이라 과거 조치보다 우선했어요")
        return {
            "outcome": outcome,
            "root_cause": past.get("root_cause"),
            "action": this_action or past.get("action"),
            "basis": basis,
            "source": "past_case",
            "past_card_id": past.get("card_id"),
            "similarity": past.get("similarity"),
            "this_event_cause": this_cause,
        }
    if this_cause is None and this_action is None:
        return None
    return {
        "outcome": outcome,
        "root_cause": this_cause,
        "action": this_action,
        "basis": this_basis,
        "source": "this_event",
        "past_card_id": None,
        "similarity": None,
        "this_event_cause": None,
    }

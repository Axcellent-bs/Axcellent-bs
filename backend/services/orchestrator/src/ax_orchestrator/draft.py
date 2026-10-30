"""해결 내용 초안: 총괄이 이미 아는 사실만 옮겨 사람이 확인할 초안을 만든다.

원인은 데이터분석 AI의 원인 후보 중 확신이 가장 높은 것, 조치는 끝난 롤백뿐이다. 아는 게 없으면
초안을 만들지 않는다(없는 값을 지어내지 않는다). 초안은 저장되지 않고, 사람이 확인해 눌러야 남는다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ax_orchestrator.models import Incident

_RANK = {"high": 0, "mid": 1, "low": 2}
_KST = timezone(timedelta(hours=9))
_ISO_UTC = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z")


def _kst(text: str) -> str:
    """원인 후보 문장 속 UTC 시각(2026-10-30T06:17:54Z)을 한국 시간으로 바꿔 읽기 쉽게 한다."""

    def one(m: re.Match[str]) -> str:
        at = datetime(*(int(g) for g in m.groups()), tzinfo=UTC).astimezone(_KST)
        return f"{at.month}월 {at.day}일 {at:%H:%M:%S}(한국 시간)"

    return _ISO_UTC.sub(one, text)


_CONFIDENCE_KO = {"high": "높음", "mid": "보통", "low": "낮음"}


def resolution_draft(inc: Incident) -> dict[str, Any] | None:
    root_cause, basis = None, []
    found = inc.outputs.get("root_cause") or {}
    hypotheses = [h for h in found.get("hypotheses") or [] if h.get("explanation")]
    if hypotheses:
        top = min(hypotheses, key=lambda h: _RANK.get(str(h.get("confidence")), 3))
        root_cause = _kst(str(top["explanation"]))
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
    if root_cause is None and action is None:
        return None
    recovery = inc.steps.get("recovery")
    outcome = "resolved" if recovery is not None and recovery.status == "completed" else None
    return {"outcome": outcome, "root_cause": root_cause, "action": action, "basis": basis}

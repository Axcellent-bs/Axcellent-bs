"""재무 이상 사건 plan: 재무 AI 사후 감사가 새 의심 건(같은 고객 반복 환불 등)을 찾았을 때.

`finance.anomaly_detected` 이벤트로 열린다. 재무 AI가 주인공이다.
1. 재무 AI가 사후 감사를 다시 돌려 의심 건을 사건 기록으로 남긴다 (read)
2. 고객지원 AI가 의심 고객의 이상 신호(문의·클레임·환불 이력)를 본다 (read, 고객이 없으면 건너뜀)
3. 인사 AI가 사건을 회사 기억에 기록한다

돈을 돌려받거나 고객을 막는 일은 하지 않는다. 사람이 재무 화면(/finance)에서 보고 판단한다.
"""

from __future__ import annotations

from typing import Any

from ax_orchestrator.models import Incident, Plan, Step


def _buyers(inc: Incident) -> list[str]:
    return [b for b in inc.signal.get("buyer_ids") or [] if isinstance(b, str)]


def _audit(inc: Incident) -> dict[str, Any]:
    return {"days": 7}


def _signals(inc: Incident) -> dict[str, Any]:
    buyers = _buyers(inc)
    return {"buyer_id": buyers[0]} if buyers else {}


def _signals_gate(inc: Incident) -> tuple[str, str, str] | None:
    if _buyers(inc):
        return None
    return ("skipped", "no_buyer", "의심 고객이 특정되지 않아 고객 신호 확인 없음")


def finance_anomaly() -> Plan:
    return Plan(
        name="finance_anomaly",
        steps=[
            Step("audit", "audit_report", "사후 감사 다시 확인 (finance)", build=_audit),
            Step(
                "signals",
                "customer_signals",
                "의심 고객 이상 신호 확인 (support)",
                after=("audit",),
                build=_signals,
                gate=_signals_gate,
                required=False,
            ),
            Step(
                "record",
                "record_case",
                "사건 기록 (hr)",
                also_after=("audit", "signals"),
                kind="record",
            ),
        ],
    )

"""판매자 입점 심사 plan: 캣북스에 새 판매자가 입점했을 때 (`seller.registered`).

판매자 한 명마다 이벤트 하나가 열린다. 재무 AI가 주인공이다.
1. 재무 AI가 서류·위험 점수(데이터분석 `fraud_score`)로 입점을 심사한다 (B, safety 관문).
   위험 점수가 보류 기준 이상이면 safety가 보류하고 사람이 확인한다.
2. 심사가 거절되면(서류 미확인·사람 거절) 재무 AI가 그 판매자의 정산을 멈춘다 (B).
   이미 올린 상품이 팔려도 돈이 나가지 않게 한다. 승인되면 건너뛴다.
3. 인사 AI가 심사 결과를 회사 기억에 기록한다.

판매자 소개 문장은 이벤트에 없다. 심사는 서류 확인 여부와 위험 점수로만 한다.
"""

from __future__ import annotations

from typing import Any

from ax_orchestrator.models import Incident, Plan, Step


def _review(inc: Incident) -> dict[str, Any]:
    return {
        "seller_id": inc.signal.get("seller_id"),
        "category_codes": list(inc.signal.get("category_codes") or []),
        "documents_verified": inc.signal.get("documents_verified") is True,
    }


def _rejected(inc: Incident) -> bool:
    run = inc.steps.get("review")
    if run is None:
        return False
    return run.status == "rejected_by_human" or inc.shared.get("review_decision") == "rejected"


def _hold(inc: Incident) -> dict[str, Any]:
    reason = inc.shared.get("review_reason")
    code = "documents_missing" if reason == "documents_missing" else "fraud_risk"
    return {"seller_id": inc.signal.get("seller_id"), "reason_code": code}


def _hold_gate(inc: Incident) -> tuple[str, str, str] | None:
    if _rejected(inc):
        return None
    return ("skipped", "not_rejected", "입점이 거절되지 않아 정산을 멈추지 않음")


def seller_onboarding() -> Plan:
    return Plan(
        name="seller_onboarding",
        steps=[
            Step(
                "review",
                "seller_review",
                "판매자 입점 심사 (finance)",
                build=_review,
                keep={
                    "decision": "review_decision",
                    "reason_code": "review_reason",
                    "risk_score": "risk_score",
                },
            ),
            Step(
                "hold",
                "settlement_hold",
                "거절된 판매자 정산 멈춤 (finance)",
                also_after=("review",),
                build=_hold,
                gate=_hold_gate,
                required=False,
            ),
            Step(
                "record",
                "record_case",
                "심사 결과 기록 (hr)",
                also_after=("review", "hold"),
                kind="record",
            ),
        ],
    )

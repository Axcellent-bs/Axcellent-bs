"""고객지원 처리 결과 plan: 고객지원이 문의를 닫았을 때 (`support.inquiry_resolved`).

문의 한 건마다 사건 하나가 열리고, 총괄이 처리 결과를 기록으로 남겨 다른 부서에 넘긴다.
1. 재무 AI가 처리 결과를 받아 주문 배송비·비용 기록을 확인하고 장부에 남긴다 (read).
   경비를 쓰지 않은 문의(답변만 한 문의 등)도 처리 결과는 기록한다.
2. 인사 AI가 재무의 기록까지 붙여 처리 결과를 회사 기억에 기록한다.
   (운영 기록: 이후 업무에서 다시 찾아 쓴다)

고객에게 돈을 돌려주거나 쿠폰을 내는 일은 하지 않는다. 그건 고객지원이 이미 처리한 일이고,
이 plan은 그 결과를 기록하고 넘기기만 한다.
"""

from __future__ import annotations

from typing import Any

from ax_orchestrator.models import Incident, Plan, Step

# 이벤트가 알려 주는 처리 결과 필드 (`SupportInquiryResolvedData`). 있는 것만 재무에 넘긴다
SIGNAL_FIELDS = (
    "inquiry_id",
    "order_id",
    "resolution",
    "claim_id",
    "claim_decision",
    "coupon_id",
    "refund_amount",
)


def _cost(inc: Incident) -> dict[str, Any]:
    return {k: inc.signal[k] for k in SIGNAL_FIELDS if inc.signal.get(k) is not None}


def support_resolved() -> Plan:
    return Plan(
        name="support_resolved",
        steps=[
            Step(
                "cost",
                "record_support_cost",
                "배송비·비용 기록 확인 (finance)",
                build=_cost,
                # 재무가 남긴 기록을 사건에 보관한다: 총괄 화면에서 보고, 인사 기록에 붙인다
                keep={
                    "note": "record_note",
                    "expense_used": "expense_used",
                    "shipping_fee": "shipping_fee",
                    "refund_amount": "refund_amount",
                },
            ),
            Step(
                "record",
                "record_case",
                "처리 결과 기록 (hr)",
                # 재무 기록이 실패해도 상담 처리 결과는 기록으로 남긴다
                also_after=("cost",),
                kind="record",
            ),
        ],
    )

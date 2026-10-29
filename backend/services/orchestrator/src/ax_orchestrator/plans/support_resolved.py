"""고객지원 처리 결과 plan: 고객지원이 문의를 닫았을 때 (`support.inquiry_resolved`).

문의 한 건마다 사건 하나가 열리고, 총괄이 처리 결과를 기록으로 남겨 다른 부서에 넘긴다.
1. 재무 AI가 처리 결과를 받아 주문 배송비·비용 기록을 확인하고 장부에 남긴다 (read).
   경비를 쓰지 않은 문의(답변만 한 문의 등)도 처리 결과는 기록한다.
2. 인사 AI가 재무의 기록까지 붙여 처리 결과를 회사 기억에 기록한다.
   (운영 기록: 이후 업무에서 다시 찾아 쓴다)

고객에게 돈을 돌려주거나 쿠폰을 내는 일은 하지 않는다. 그건 고객지원이 이미 처리한 일이고,
이 plan은 그 결과를 기록하고 넘기기만 한다.

3. 인사 AI의 감사 기록을 읽어, 이 사건의 기록이 실제로 남았는지 확인한다 (read).
   남지 않았으면 이관 큐로 돌려 사람이 보게 한다 (기록이 남았다고 믿고 넘어가지 않는다).
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


# 인사가 `record_case`로 남기는 감사 기록: `record_case.<결과>`(카드·후보·중복)와 `record_only`
def _recorded(entry: dict[str, Any]) -> bool:
    kind = str(entry.get("kind") or "")
    return kind.startswith("record_case.") or kind == "record_only"


def _verify_record(output: dict[str, Any]) -> tuple[str, str] | None:
    """인사 감사 기록에 처리 결과 기록이 없으면 사람이 보게 한다 (있으면 None)."""
    entries = output.get("entries") or []
    if any(_recorded(e) for e in entries if isinstance(e, dict)):
        return None
    return "record_not_found", "인사 기록에서 이 사건의 처리 결과 기록을 찾지 못함"


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
            Step(
                "verify",
                "get_audit_timeline",
                "인사 기록 읽어 확인 (hr)",
                after=("record",),
                build=lambda inc: {"context_id": inc.context_id},
                verdict=_verify_record,
            ),
        ],
    )

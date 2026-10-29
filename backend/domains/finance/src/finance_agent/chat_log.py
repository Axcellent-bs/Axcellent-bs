"""재무 AI가 한 일을 공통 로그에 남겨 AI 대화방에 보이게 한다.

재무는 사건 흐름의 A2A 단계가 아니라서 그동안 대화방에 나오지 않았다. 하지만 재무 AI가 하는 일
(장부 기록, 사후 감사)은 모두 다른 AI가 한 일에서 시작하므로 대화로 보여 준다.

- 장부 기록: 지급·환불 이벤트를 받아 장부에 적으면 "재무 AI → 보낸 쪽: 장부에 적었어요"
  (`result_sent`, completed) 한 줄을 남긴다. skill 이름은 `ledger_record`.
  요청 줄은 남기지 않는다(대화가 두 배로 길어진다).
- 사후 감사: 새로 찾은 의심 건만 `finance.anomaly_detected` 이벤트로 알린다. 총괄이 받아 사건을 열고
  (사건 방이 생긴다) 재무·고객지원·인사 AI에게 일을 나눈다. 같은 의심 건은 한 번만 알린다
  (화면이 감사를 다시 불러도 사건이 늘지 않게).

payload에는 코드가 만든 사실(종류·금액·규칙 코드)만 담는다. 고객 문장은 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from ax_agent_template import events
from ax_agent_template.logging import log_event
from ax_contracts.schemas import EventEnvelope, EventType

AGENT = "finance"
LEDGER_SKILL = "ledger_record"
_seen_findings: set[str] = set()


def ledger_recorded(
    context_id: str | None, tenant_id: str, sender: str, kind: str, amount: int, count: int = 1
) -> None:
    """지급·환불 한 건을 장부에 적은 일을 대화 한 줄로 남긴다.

    AI 대화방은 사건마다 방이 있다. 사건 번호가 없는 기록(쇼핑몰 환불 이벤트 등)은
    대화로 남기지 않는다 (장부에는 적힌다).
    """
    if not context_id:
        return
    payload: dict[str, Any] = {"ledger_kind": kind, "amount": amount, "count": count}
    log_event(
        context_id, tenant_id, "result_sent", payload,
        source=AGENT, skill=LEDGER_SKILL, task_status="completed", requester=sender,
    )


def _signature(finding: dict[str, Any]) -> str:
    raw = "|".join([str(finding.get("code")), *sorted(str(r) for r in finding.get("refs") or [])])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


BUYER_ID = re.compile(r"buy_\d+")


async def audit_findings(findings: list[dict[str, Any]], tenant_id: str) -> None:
    """새로 찾은 사후 감사 의심 건마다 `finance.anomaly_detected`를 발행한다.

    총괄이 사건을 연다.
    """
    for finding in findings:
        sig = _signature(finding)
        if sig in _seen_findings:
            continue
        _seen_findings.add(sig)
        buyers = sorted(set(BUYER_ID.findall(str(finding.get("detail") or ""))))
        envelope = EventEnvelope(
            event_id=f"evt_fin_{sig}",
            type=EventType.FINANCE_ANOMALY_DETECTED,
            occurred_at=datetime.now(UTC),
            context_id=None,
            tenant_id=tenant_id,
            source=AGENT,
            idempotency_key=f"finance.anomaly:{sig}",
            data={
                "component": "finance",
                "metric_code": finding.get("code"),
                "severity": finding.get("severity"),
                "buyer_ids": buyers,
                "finding_ref": sig,
                "event_ref": uuid.uuid4().hex[:8],
            },
        )
        try:
            await events.publish(envelope)
        except Exception:  # noqa: BLE001 - 알리지 못해도 감사 결과는 화면에 그대로 보인다
            _seen_findings.discard(sig)

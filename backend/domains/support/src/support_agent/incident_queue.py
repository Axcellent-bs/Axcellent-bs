"""사건(장애) 보상의 "대기 → 사람 승인 → 발송" 큐 (준하님 결정 2026-11-04: 사람 확인 후).

총괄이 장애 복구 뒤 영향 고객 목록을 넘기면 쿠폰을 바로 요청하지 않고 고객마다 한 줄씩 대기로
쌓는다(`propose_incident_compensation`). 사람이 화면에서 고객별로 승인하거나 거절하고
(`decide_incident_compensation`), 승인된 고객만 기존 `request_incident_compensation` 경로로
마케팅 `issue_coupon`에 요청한다. 지급과 한도 검사는 그대로 마케팅·안전 관문이 한다.

상태: `pending`(대기) → `approved`(승인, 아직 못 보냄) → `sent`(마케팅에 요청해 지급·이음) 또는
`held`(마케팅 한도 초과로 사람 승인 대기, C 보류), 승인 뒤 마케팅에 닿지 않으면 `approved`로 남아
다시 승인하면 이어서 보낸다. 거절(`rejected`)은 끝이다.

서버 메모리가 기본이고 `SUPPORT_PG_DSN`이 있으면 `persistence`가 DB에도 쓴다.
"""

from __future__ import annotations

from typing import Literal, get_args

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel
from support_agent.store import now_iso

Status = Literal["pending", "approved", "sent", "held", "rejected"]
OPEN_STATUSES: tuple[str, ...] = ("pending", "approved", "held")  # 아직 끝나지 않은 줄


class QueueItem(AxModel):
    """보상 대기 한 줄: 사건 하나·구매자 한 명·사유 하나."""

    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str  # 사건 번호 `ctx_inc_…`
    buyer_id: str
    reason_code: str
    count: int
    unit_value: int
    total_value: int
    impact_ref: str | None = None  # 영향 분석 결과 참조 (근거)
    status: Status = "pending"
    created_at: str
    created_by: str  # 대기로 올린 요청자 (총괄)
    decided_by: str | None = None  # 사람 토큰의 이름 (본문 값이 아니다)
    decided_at: str | None = None
    coupon_id: str | None = None  # 마케팅이 지급한 쿠폰
    sent_at: str | None = None
    seq: int


Key = tuple[str, str, str]  # (사건, 구매자, 사유)


class IncidentQueueStore:
    def __init__(self) -> None:
        self._items: dict[Key, QueueItem] = {}

    def _put(self, item: QueueItem) -> QueueItem:
        """저장 지점. 영속 저장소가 여기서 DB에도 쓴다."""
        self._items[(item.context_id, item.buyer_id, item.reason_code)] = item
        return item

    def get(self, context_id: str, buyer_id: str, reason_code: str) -> QueueItem | None:
        return self._items.get((context_id, buyer_id, reason_code))

    def find_buyer(self, context_id: str, buyer_id: str) -> QueueItem | None:
        """사건의 구매자 한 명의 줄 (사유가 여럿이면 먼저 올린 것)."""
        rows = [
            i for i in self._items.values() if i.context_id == context_id and i.buyer_id == buyer_id
        ]
        return min(rows, key=lambda i: i.seq) if rows else None

    def list(
        self, context_id: str | None = None, statuses: tuple[str, ...] | None = None
    ) -> list[QueueItem]:
        rows = [
            i
            for i in self._items.values()
            if (context_id is None or i.context_id == context_id)
            and (statuses is None or i.status in statuses)
        ]
        return sorted(rows, key=lambda i: i.seq)

    def add(
        self,
        context_id: str,
        buyer_id: str,
        reason_code: str,
        *,
        count: int,
        unit_value: int,
        impact_ref: str | None,
        created_by: str,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> tuple[QueueItem, bool]:
        """대기 한 줄을 만든다. 같은 사건·구매자·사유가 이미 있으면 그것을 돌려준다 (멱등).

        `(줄, 새로 만들었는지)`를 돌려준다.
        """
        existing = self.get(context_id, buyer_id, reason_code)
        if existing is not None:
            return existing, False
        item = QueueItem(
            tenant_id=tenant_id,
            context_id=context_id,
            buyer_id=buyer_id,
            reason_code=reason_code,
            count=count,
            unit_value=unit_value,
            total_value=count * unit_value,
            impact_ref=impact_ref,
            created_at=now_iso(),
            created_by=created_by,
            seq=max((i.seq for i in self._items.values()), default=0) + 1,
        )
        return self._put(item), True

    def update(self, item: QueueItem, **changes: object) -> QueueItem:
        return self._put(item.model_copy(update=changes))

    def decide(self, item: QueueItem, status: Status, decided_by: str) -> QueueItem:
        return self.update(item, status=status, decided_by=decided_by, decided_at=now_iso())

    def summary(self, context_id: str) -> dict[str, int]:
        """사건 큐의 지금 상태를 상태별 고객 수와 나간 쿠폰으로 센다 (고객별 값은 없다)."""
        rows = self.list(context_id)
        count = {s: sum(1 for i in rows if i.status == s) for s in get_args(Status)}
        sent = [i for i in rows if i.status == "sent"]
        return {
            **count,
            "remaining": count["pending"] + count["approved"] + count["held"],
            "coupons_sent": sum(i.count for i in sent),
            "sent_value": sum(i.total_value for i in sent),
        }

    def totals(self, context_id: str, statuses: tuple[str, ...]) -> tuple[int, int]:
        """사건의 (줄 수, 합계 원). 고른 상태만 센다."""
        rows = self.list(context_id, statuses)
        return len(rows), sum(i.total_value for i in rows)

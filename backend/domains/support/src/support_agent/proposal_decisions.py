"""보내지 않기로 한 변경 요청 제안의 기록 (#281 2단계).

제안(`propose_change_requests`)은 급증 상태에서 매번 계산되고 저장하지 않는다. 상담원이 "보내지
않기"를 고르면 이유와 결정한 사람만 여기에 남긴다. 키는 (분류, 대표 문의)다. 같은 급증이 이어지는
동안은 같은 키라 제안 목록에서 "보내지 않음"으로 표시되고, 급증이 새로 시작해 대표 문의가 바뀌면
새 제안으로 다시 나온다.

실제로 기획에 보낸 요청은 `ChangeRequestStore`가 따로 갖는다. 이 저장소에는 보내지 않은 것만 있다.
서버 메모리가 기본이고 `SUPPORT_PG_DSN`이 있으면 `persistence`가 DB에도 쓴다.
"""

from __future__ import annotations

from typing import Literal

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel
from support_agent.store import now_iso

# 고르는 이유. 자유 글은 note에 짧게만 받는다 (개인정보가 있으면 거부)
DeclineReason = Literal["already_known", "not_a_real_problem", "other"]
DECLINE_REASONS: tuple[str, ...] = ("already_known", "not_a_real_problem", "other")
NOTE_LIMIT = 200


class ProposalDecision(AxModel):
    """보내지 않기로 한 제안 하나."""

    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str  # 이 결정을 한 요청의 사건 ID (화면 호출은 `ctx_ui_…`)
    topic_code: str
    inquiry_id: int  # 제안의 대표 문의
    reason: DeclineReason
    note: str | None = None
    decided_by: str  # 사람 토큰의 이름 (본문 값이 아니다)
    decided_at: str
    seq: int


class ProposalDecisionStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, int], ProposalDecision] = {}

    def _put(self, decision: ProposalDecision) -> ProposalDecision:
        """저장 지점. 영속 저장소가 여기서 DB에도 쓴다."""
        self._items[(decision.topic_code, decision.inquiry_id)] = decision
        return decision

    def get(self, topic_code: str, inquiry_id: int) -> ProposalDecision | None:
        return self._items.get((topic_code, inquiry_id))

    def all(self) -> list[ProposalDecision]:
        return sorted(self._items.values(), key=lambda d: d.seq)

    def decline(
        self,
        topic_code: str,
        inquiry_id: int,
        reason: DeclineReason,
        *,
        context_id: str,
        decided_by: str,
        note: str | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> tuple[ProposalDecision, bool]:
        """보내지 않기를 기록한다. 같은 제안에 이미 기록이 있으면 그것을 돌려준다 (멱등).

        `(기록, 새로 만들었는지)`를 돌려준다.
        """
        existing = self.get(topic_code, inquiry_id)
        if existing is not None:
            return existing, False
        decision = ProposalDecision(
            tenant_id=tenant_id,
            context_id=context_id,
            topic_code=topic_code,
            inquiry_id=inquiry_id,
            reason=reason,
            note=note,
            decided_by=decided_by,
            decided_at=now_iso(),
            seq=max((d.seq for d in self._items.values()), default=0) + 1,
        )
        return self._put(decision), True

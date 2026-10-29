"""hr `find_similar_fraud_cards` 조회 (사기 점수 FRAUD_SIMILAR 규칙, A2A).

다른 domains 코드는 import하지 않고 `call_agent`로만 부른다. 보조 조회라 오래 기다리지 않는다
(`TIMEOUT_SECONDS`). 꺼짐·무응답·시간 초과·형식 오류는 모두 이유와 함께 "읽지 못함"으로 돌려준다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.hr import FindSimilarFraudCardsOutput

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]

TARGET = "hr"  # 부서 이름으로 부르면 skill을 맡은 서비스(decisions)로 간다 (services.py)
SKILL = "find_similar_fraud_cards"
SOURCE = f"hr.{SKILL}"
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class SimilarCards:
    """조회 결과. `method`가 None이면 읽지 못한 것이고 `reason`에 이유가 있다."""

    method: str | None  # "dense" | "dice" | None
    max_similarity: float = 0.0
    card_ids: list[str] = field(default_factory=list)
    reason: str | None = None


def _failed(reason: str) -> SimilarCards:
    return SimilarCards(method=None, reason=reason)


async def similar_fraud_cards(
    facts: dict, meta: RequestMeta, send: SendFn | None = None, limit: int = 3
) -> SimilarCards:
    """판매자 사실(개인정보 없음)로 비슷한 지난 사기 카드를 찾는다."""
    send = send or call_agent
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=TIMEOUT_SECONDS))
    request = AgentRequest(
        meta=meta.model_copy(update={"deadline": deadline}),
        skill=SKILL,
        input={"facts": facts, "limit": limit},
    )
    try:
        result = await asyncio.wait_for(send(TARGET, request), timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        return _failed(f"{SOURCE} 시간 초과 ({TIMEOUT_SECONDS:g}초)")
    except Exception as error:  # noqa: BLE001 - 보조 조회라 어떤 실패든 확인할 수 없음으로 둔다
        return _failed(f"{SOURCE} 호출 실패 ({type(error).__name__})")
    if result.status == TaskStatus.REJECTED and result.outcome == "service_disabled":
        return _failed(f"{SOURCE} 서비스가 꺼져 있음")
    if result.status != TaskStatus.COMPLETED:
        return _failed(f"{SOURCE} 응답 {result.status.value} ({result.outcome or '-'})")
    try:
        output = FindSimilarFraudCardsOutput.model_validate(result.output or {})
    except ValidationError:
        return _failed(f"{SOURCE} 응답 형식이 계약과 다름")
    return SimilarCards(
        method=output.method,
        max_similarity=output.max_similarity,
        card_ids=[card.card_id for card in output.cards],
    )

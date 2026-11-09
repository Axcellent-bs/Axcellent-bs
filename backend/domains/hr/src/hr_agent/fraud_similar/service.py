"""`find_similar_fraud_cards`: 판매자 사실과 비슷한 지난 사기 카드(kind=fraud)를 찾는다(조회 전용).

data의 사기 점수 `FRAUD_SIMILAR` 규칙이 부른다. 판매자 사실(가입 일수·저가 비율·24시간 등록 수)을
짧은 글로 바꿔 카드 검색(Dense, 안 되면 Dice)에 넣는다. 유사도는 검색 점수 그대로이고 0~1로 자른다.
기준(`dense_min_cosine`) 미만 카드는 돌려주지 않아서, 비슷한 카드가 없으면 `cards`가 비고
`max_similarity`는 0이다. 개인정보는 입력에 없다. 상태를 바꾸지 않는다.
"""

from __future__ import annotations

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Card,
    FindSimilarFraudCardsInput,
    FindSimilarFraudCardsOutput,
    SellerFraudFacts,
    SimilarFraudCard,
)
from hr_agent.results import completed, invalid_input, make_evidence
from hr_agent.services import Services

KIND = "fraud"


def facts_text(facts: SellerFraudFacts) -> str:
    """카드 글과 같은 말투의 질의. 값은 그대로 적고 해석하지 않는다."""
    parts = [
        f"가입 {facts.joined_days}일",
        f"저가 상품 비율 {round(facts.low_price_ratio * 100)}%",
        f"24시간 등록 {facts.listings_last_24h}개",
    ]
    if facts.business_hash_duplicated is not None:
        parts.append("사업자 중복" if facts.business_hash_duplicated else "사업자 중복 없음")
    return "판매자 " + ", ".join(parts)


async def _dense(svc: Services, query: str, limit: int) -> list[tuple[Card, float]] | None:
    if svc.dense is None:
        return None
    try:
        cards = await svc.memory.list_cards(states=["active"])
        fraud = [c for c in cards if c.kind == KIND]
        return await svc.dense.search(
            query, fraud, k=limit, min_cosine=svc.settings.dense_min_cosine
        )
    except Exception:  # noqa: BLE001 - 임베딩 실패가 점수 계산을 막지 않게 Dice로 대체한다
        return None


async def find_similar_fraud_cards(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = FindSimilarFraudCardsInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    query = facts_text(params.facts)

    found = await _dense(svc, query, params.limit)
    if found is not None:
        method = "dense"
        pairs = found
    else:
        method = "dice"
        hits = await svc.memory.search_cards(
            query, kinds=[KIND], states=["active"], limit=params.limit
        )
        pairs = [(h.card, h.score) for h in hits if h.score >= svc.settings.similarity_threshold]

    cards = [
        SimilarFraudCard(card_id=c.card_id, title=c.title, similarity=min(max(s, 0.0), 1.0))
        for c, s in pairs
    ]
    output = FindSimilarFraudCardsOutput(
        cards=cards,
        max_similarity=max((c.similarity for c in cards), default=0.0),
        method=method,
        collected_at=svc.clock(),
    )
    ids = [c.card_id for c in cards]
    summary = (
        f"유사 사기 카드 {len(cards)}건 ({method})"
        if cards
        else "비슷한 지난 사기 카드가 없다 (기준 미만 또는 사기 카드 없음)"
    )
    return completed(
        request,
        output,
        outcome="searched",
        evidence=make_evidence(svc.clock, summary, ids, [query]),
    )

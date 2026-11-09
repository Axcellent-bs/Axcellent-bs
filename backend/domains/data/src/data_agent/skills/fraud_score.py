"""fraud_score skill: 판매자 사기 위험 규칙 점수 (grade read).

shop-api 판매자 정보(`/sellers/{id}/profile`)와 가격(`/sellers/{id}/prices`)을 읽어 규칙 사실을
만들고, 점수는 `metrics/fraud.score_seller`가 계산한다(LLM 없음).
가중치: 재무 확정 (#99, 2026-10-29). 사업자 중복·유사 사기 카드 자료가 생기면 다시 잰다.
점수는 판단 재료이며 승인·보류는 정하지 않는다(재무·정산이 정한다).

확인할 수 없는 사실은 0점으로 셈하지 않는다. 그 규칙은 점수에서 빼고 `unknown_rules`·`reasons`
(`note`)·summary에 "확인할 수 없음: <규칙>"으로 남긴다. summary에는 확인한 규칙 수와 확인한 규칙으로
낼 수 있는 최대 점수(100 − 확인 못 한 규칙 가중치)를 함께 적는다.

점수가 불완전한지(`complete`)와 보수적 상한(`max_possible_score`·`max_possible_level`)도 낸다.
상한은 확인한 점수 + 확인 못 한 규칙 가중치 합(100 상한)으로, 빠진 규칙을 모두 위험으로 본 값이다.
`complete=false`이면 소비자는 `score`만으로 승인하지 않는다(상한을 쓰거나 사람 확인).

- NEW_ACCOUNT: `joined_at`으로 가입 일수. 가입 시각이 지금보다 뒤면 확인할 수 없음
- LOW_PRICE: 판매 중 상품 중 카테고리 중앙값의 50% 미만 비율. 판매 중 상품이 없으면 확인할 수 없음
- BULK_LISTING: `listings_last_24h`
- DUP_BUSINESS: `business_hash_duplicated`가 null이면(지금 shop-api는 늘 null) 확인할 수 없음
- FRAUD_SIMILAR: hr `find_similar_fraud_cards`(A2A, #107)에 판매자 사실(개인정보 없음)을 보낸다
  - method=dense: `max_similarity`를 규칙 값으로 쓰고, 카드 id를 `similar_card_ids`에 넣는다
  - method=dice: 척도가 달라 점수에 쓰지 않는다
    ("확인할 수 없음: FRAUD_SIMILAR (임베딩 없음, dice 대체)").
    카드 id는 참고로 `similar_card_ids`에 넣는다
  - hr 꺼짐·무응답·시간 초과·형식 오류: 확인할 수 없음 (이유를 남긴다)
  - 확인할 수 없으면 다른 규칙처럼 `complete`·`max_possible_score`에 반영된다
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ax_contracts.schemas import Evidence, RequestMeta
from ax_contracts.schemas.data import FraudReason, FraudScoreInput, FraudScoreOutput
from data_agent.clients.hr_cards import SendFn, SimilarCards, similar_fraud_cards
from data_agent.clients.shop_client import SellerClient
from data_agent.metrics.fraud import (
    BULK_LISTING,
    DUP_BUSINESS,
    FRAUD_SIMILAR,
    LOW_PRICE,
    NEW_ACCOUNT,
    WEIGHTS,
    SellerFacts,
    level_for,
    low_price_ratio,
    max_possible_score,
    score_seller,
)

NO_SIMILAR_CARD_SKILL = "hr에 data가 부를 수 있는 유사 사기 카드 skill이 없음"
NO_REQUEST_META = "요청 정보가 없어 hr에 유사 사기 카드를 묻지 않음"
DICE_FALLBACK = "임베딩 없음, dice 대체"
NO_BUSINESS_HASH = "shop-api 판매자 정보에 사업자 해시가 없음"
NO_ACTIVE_PRODUCTS = "판매 중인 상품이 없어 저가 비율을 정할 수 없음"
JOINED_IN_FUTURE = "가입 시각이 지금보다 뒤라 가입 일수를 정할 수 없음"
LEVEL_KO = {"low": "낮음", "mid": "중간", "high": "높음"}
DECISION_NOTE = "가중치는 재무 확정 전 초안이며, 승인·보류는 정하지 않는다(재무·정산이 정한다)."


@dataclass(frozen=True)
class FraudResult:
    output: FraudScoreOutput
    evidence: Evidence


def _describe(code: str, value: float) -> str:
    if code == NEW_ACCOUNT:
        return f"가입 {int(value)}일"
    if code == LOW_PRICE:
        return f"저가 상품 {round(value * 100, 1):g}%"
    if code == BULK_LISTING:
        return f"24시간 등록 {int(value)}개"
    if code == DUP_BUSINESS:
        return "사업자 중복"
    return f"유사 사기 카드 유사도 {value:g}"


async def score_fraud(
    data: FraudScoreInput,
    shop: SellerClient,
    now: datetime | None = None,
    *,
    meta: RequestMeta | None = None,
    send: SendFn | None = None,
) -> FraudResult:
    """판매자 사실을 shop-api에서 읽어 점수를 낸다. 못 읽으면 `ShopUnavailable`이 그대로 나간다.

    `meta`가 있으면 hr에 유사 사기 카드를 묻는다(`send`, 기본 `call_agent`). 없으면 묻지 않고
    FRAUD_SIMILAR는 확인할 수 없음이다.
    """
    now = now or datetime.now(UTC)
    profile = await shop.seller_profile(data.seller_id)
    prices = await shop.seller_prices(data.seller_id)

    unknown: dict[str, str] = {}
    joined_days = (now - profile.joined_at).days
    if profile.joined_at > now:
        unknown[NEW_ACCOUNT] = JOINED_IN_FUTURE
    pairs = [(item.price, item.category_median_price) for item in prices.items]
    ratio = low_price_ratio(pairs) if pairs else 0.0
    if not pairs:
        unknown[LOW_PRICE] = NO_ACTIVE_PRODUCTS
    if profile.business_hash_duplicated is None:
        unknown[DUP_BUSINESS] = NO_BUSINESS_HASH
    if meta is None:
        similar = SimilarCards(method=None, reason=NO_REQUEST_META)
    else:
        similar = await similar_fraud_cards(
            {
                "joined_days": max(joined_days, 0),
                "low_price_ratio": ratio,
                "listings_last_24h": profile.listings_last_24h,
                "business_hash_duplicated": profile.business_hash_duplicated,
            },
            meta,
            send,
        )
    similarity = 0.0
    if similar.method == "dense":
        similarity = similar.max_similarity
    elif similar.method == "dice":  # 척도가 달라 점수에 쓰지 않는다(카드 id는 참고로 남긴다)
        unknown[FRAUD_SIMILAR] = DICE_FALLBACK
    else:
        unknown[FRAUD_SIMILAR] = similar.reason or NO_SIMILAR_CARD_SKILL

    # 확인 못 한 규칙에는 점수가 나오지 않는 값을 넣고, 결과에서 그 규칙을 뺀다(점수 계산은 그대로)
    facts = SellerFacts(
        joined_days=max(joined_days, 0) if NEW_ACCOUNT not in unknown else 10**6,
        low_price_ratio=ratio,
        business_hash_duplicated=bool(profile.business_hash_duplicated),
        listings_last_24h=profile.listings_last_24h,
        max_fraud_card_similarity=similarity,
    )
    scored = score_seller(facts)
    checked = [code for code in WEIGHTS if code not in unknown]
    triggered = [r for r in scored.reasons if r.rule_code not in unknown]
    unknown_rules = [code for code in WEIGHTS if code in unknown]
    checked_max = 100 - sum(WEIGHTS[code] for code in unknown_rules)
    worst = max_possible_score(scored.score, unknown_rules)
    worst_level = level_for(worst)

    reasons = [
        FraudReason(rule_code=r.rule_code, value=r.value, weight=round(r.points, 2))
        for r in triggered
    ] + [
        FraudReason(
            rule_code=code, value=0.0, weight=0.0, note=f"확인할 수 없음: {code} ({unknown[code]})"
        )
        for code in unknown_rules
    ]
    basis = (
        ", ".join(
            f"{_describe(r.rule_code, r.value)}({r.rule_code} {r.points:g}점)" for r in triggered
        )
        or "기준을 넘은 규칙 없음"
    )
    parts = [
        f"판매자 {data.seller_id} 사기 위험 점수 {scored.score}점"
        f"({LEVEL_KO[scored.level]}). 근거: {basis}."
    ]
    parts += [f"확인할 수 없음: {code} ({unknown[code]})." for code in unknown_rules]
    if unknown_rules:
        parts.append(
            f"확인한 규칙 {len(checked)}개 기준 점수, "
            f"확인한 규칙으로 낼 수 있는 최대 {checked_max}점."
        )
        parts.append(
            f"불완전: 확인 못 한 규칙을 모두 위험으로 보면 최대 {worst}점({LEVEL_KO[worst_level]})."
        )
    parts.append(DECISION_NOTE)

    output = FraudScoreOutput(
        seller_id=data.seller_id,
        score=scored.score,
        level=scored.level,
        reasons=reasons,
        similar_card_ids=similar.card_ids,
        summary=" ".join(parts),
        unknown_rules=unknown_rules,
        complete=not unknown_rules,
        max_possible_score=worst,
        max_possible_level=worst_level,
    )
    lines = [
        f"가입 {profile.joined_at.isoformat()} ({joined_days}일 전)",
        f"판매 중 상품 {profile.product_count}개, 24시간 등록 {profile.listings_last_24h}개",
        f"가격 확인 상품 {len(pairs)}개, 저가(카테고리 중앙값 50% 미만) 비율 {ratio:.4f}"
        if pairs
        else f"확인할 수 없음: {LOW_PRICE} ({NO_ACTIVE_PRODUCTS})",
        f"확인한 규칙: {', '.join(checked) or '없음'}",
        (
            f"유사 사기 카드 ({similar.method}): 최대 유사도 {similar.max_similarity:g}, "
            f"카드 {', '.join(similar.card_ids) or '없음'}"
            if similar.method
            else f"유사 사기 카드: 읽지 못함 ({similar.reason})"
        ),
        *(f"확인할 수 없음: {code} ({unknown[code]})" for code in unknown_rules),
        f"확인한 규칙으로 낼 수 있는 최대 {checked_max}점, "
        f"확인 못 한 규칙을 모두 위험으로 본 최대 {worst}점({worst_level}) (가중치 초안: "
        + ", ".join(f"{code} {weight}" for code, weight in WEIGHTS.items())
        + ")",
    ]
    evidence = Evidence(
        queries=[
            f"shop_seller_profile seller_id={data.seller_id}",
            f"shop_seller_prices seller_id={data.seller_id}",
            "hr.find_similar_fraud_cards",
        ],
        data_summary="\n".join(lines),
        collected_at=datetime.now(UTC),
    )
    return FraudResult(output=output, evidence=evidence)

"""fraud_score FRAUD_SIMILAR: hr `find_similar_fraud_cards`(A2A) 연결.

- method=dense: max_similarity를 규칙 값으로, 카드 id를 similar_card_ids에
- method=dice: 척도가 달라 점수에 쓰지 않음, 카드 id는 참고로 넣음
  ("확인할 수 없음: FRAUD_SIMILAR (임베딩 없음, dice 대체)")
- hr 꺼짐·무응답·시간 초과: 확인할 수 없음
- complete·max_possible_score는 채워진 규칙만큼 다시 계산
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesResult
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfile
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import FraudScoreInput
from data_agent.clients import hr_cards
from data_agent.metrics.fraud import DUP_BUSINESS, FRAUD_SIMILAR, WEIGHTS
from data_agent.skills.fraud_score import score_fraud

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class Seller:
    """가입 30일, 상품 4개 중 저가 0개, 24시간 등록 10개, 사업자 해시 없음 → 확인한 규칙은 0점."""

    async def seller_profile(self, seller_id: str) -> ShopSellerProfile:
        return ShopSellerProfile.model_validate(
            {
                "seller_id": seller_id,
                "joined_at": (NOW - timedelta(days=30)).isoformat(),
                "product_count": 4,
                "listings_last_24h": 10,
                "business_hash_duplicated": None,
            }
        )

    async def seller_prices(self, seller_id: str) -> ShopSellerPricesResult:
        items = [
            {"product_id": f"p{i}", "category": "book", "price": 20000,
             "category_median_price": 20000}
            for i in range(4)
        ]  # fmt: skip
        return ShopSellerPricesResult.model_validate({"items": items})


META = RequestMeta(
    context_id="ctx_fraud_sim",
    token="mock:finance",
    path=["finance"],
    deadline=datetime.now(UTC) + timedelta(seconds=30),
)
SEEN: list[AgentRequest] = []


def _hr(method: str, max_similarity: float, ids: list[str]):  # noqa: ANN202
    async def send(target: str, request: AgentRequest) -> AgentResult:
        SEEN.append(request)
        assert (target, request.skill) == ("hr", "find_similar_fraud_cards")
        cards = [{"card_id": i, "title": "지난 사기", "similarity": max_similarity} for i in ids]
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output={
                "cards": cards,
                "max_similarity": max_similarity,
                "method": method,
                "collected_at": NOW.isoformat(),
            },
        )

    return send


def _score(send):  # noqa: ANN001, ANN202
    data = FraudScoreInput(seller_id="sel_x")
    return asyncio.run(score_fraud(data, Seller(), NOW, meta=META, send=send)).output


def test_dense_similarity_is_scored_and_card_ids_returned() -> None:
    SEEN.clear()
    out = _score(_hr("dense", 0.8, ["card_f1", "card_f2"]))
    assert FRAUD_SIMILAR not in out.unknown_rules
    assert out.similar_card_ids == ["card_f1", "card_f2"]
    reason = next(r for r in out.reasons if r.rule_code == FRAUD_SIMILAR)
    assert (reason.value, reason.weight) == (0.8, 20.0)  # 25 × 0.8
    assert out.score == 20
    # 보낸 사실: 개인정보 없이 가입 일수·저가 비율·24시간 등록·사업자 중복(모름)
    facts = SEEN[0].input["facts"]
    assert facts == {
        "joined_days": 30,
        "low_price_ratio": 0.0,
        "listings_last_24h": 10,
        "business_hash_duplicated": None,
    }


def test_dice_is_not_scored_but_card_ids_are_kept() -> None:
    out = _score(_hr("dice", 0.9, ["card_f1"]))
    assert FRAUD_SIMILAR in out.unknown_rules
    assert out.similar_card_ids == ["card_f1"]  # 참고
    assert out.score == 0
    assert "확인할 수 없음: FRAUD_SIMILAR (임베딩 없음, dice 대체)" in out.summary


async def _off(target: str, request: AgentRequest) -> AgentResult:
    raise ConnectionError("hr 없음")


async def _disabled(target: str, request: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.REJECTED,
        outcome="service_disabled",
        output={"to_service": "decisions"},
    )


@pytest.mark.parametrize(
    ("send", "why"),
    [(_off, "호출 실패 (ConnectionError)"), (_disabled, "서비스가 꺼져 있음")],
    ids=["unreachable", "service_disabled"],
)
def test_hr_down_is_unknown(send, why) -> None:  # noqa: ANN001
    out = _score(send)
    assert FRAUD_SIMILAR in out.unknown_rules
    assert out.similar_card_ids == []
    assert f"확인할 수 없음: FRAUD_SIMILAR (hr.find_similar_fraud_cards {why})" in out.summary


def test_timeout_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hr_cards, "TIMEOUT_SECONDS", 0.05)

    async def slow(target: str, request: AgentRequest) -> AgentResult:
        await asyncio.sleep(1)
        raise AssertionError("시간 초과 전에 끝나면 안 된다")

    out = _score(slow)
    assert FRAUD_SIMILAR in out.unknown_rules
    assert "시간 초과" in out.summary


def test_complete_and_upper_bound_follow_filled_rules() -> None:
    dense = _score(_hr("dense", 0.0, []))  # dense로 채워지면 FRAUD_SIMILAR는 확인한 규칙
    assert dense.unknown_rules == [DUP_BUSINESS]
    assert dense.complete is False
    assert dense.max_possible_score == dense.score + WEIGHTS[DUP_BUSINESS]

    dice = _score(_hr("dice", 0.0, []))  # dice면 확인 못 한 규칙이 둘
    assert dice.unknown_rules == [DUP_BUSINESS, FRAUD_SIMILAR]
    assert dice.max_possible_score == dice.score + WEIGHTS[DUP_BUSINESS] + WEIGHTS[FRAUD_SIMILAR]


def test_bad_hr_response_is_unknown() -> None:
    async def broken(target: str, request: AgentRequest) -> AgentResult:
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output={"x": 1})

    out = _score(broken)
    assert FRAUD_SIMILAR in out.unknown_rules
    assert "형식이 계약과 다름" in out.summary

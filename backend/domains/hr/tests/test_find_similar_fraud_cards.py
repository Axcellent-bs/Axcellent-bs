"""`find_similar_fraud_cards`: data의 FRAUD_SIMILAR 규칙용 유사 사기 카드 조회(읽기 전용)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import Card, FindSimilarFraudCardsOutput, SellerFraudFacts
from hr_agent.config import HrSettings, ManualClock
from hr_agent.fraud_similar.service import facts_text
from hr_agent.main import app
from hr_agent.retrieval.cards import DenseCardSearch
from hr_agent.retrieval.dense import DenseIndex
from hr_agent.services import build_services, set_services

FACTS = {
    "joined_days": 2,
    "low_price_ratio": 0.9,
    "listings_last_24h": 80,
    "business_hash_duplicated": True,
}


class MockEmbedder:
    def __init__(self) -> None:
        self._mock = MockLLMClient()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._mock.embed(texts)


class BrokenEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("429 rate limited")


def _body(inp: dict, requester: str = "data") -> dict:
    return {
        "meta": {
            "context_id": "ctx_fr_0001",
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": "ctx_fr_0001:fs",
        },
        "skill": "find_similar_fraud_cards",
        "input": inp,
    }


def _card(svc, card_id: str, kind: str, title: str, body: str, **kw) -> None:
    card = Card(
        card_id=card_id,
        kind=kind,
        context_id="ctx_seed_0001",
        title=title,
        body=body,
        author="finance",
        confidence="high",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        **kw,
    )
    asyncio.run(svc.memory.write_card(card))


@pytest.fixture
def make(tmp_path):
    made = []

    def _make(*, embedder=None, **settings):
        index = DenseIndex(str(tmp_path / "v.db"), embedder, "mock") if embedder else None
        services = build_services(
            HrSettings(mock=True, **settings),
            clock=ManualClock(),
            dense=DenseCardSearch(index) if index else None,
        )
        set_services(services)
        made.append(services)
        return services, TestClient(app)

    yield _make
    set_services(None)
    for s in made:
        s.store.close()


def _call(client, inp=None) -> FindSimilarFraudCardsOutput:
    res = client.post("/a2a", json=_body({"facts": FACTS} if inp is None else inp)).json()
    assert res["status"] == "completed", res
    return FindSimilarFraudCardsOutput.model_validate(res["output"])


FRAUD_TITLE = "신규 판매자 저가 대량 등록 사기"
FRAUD_BODY = "판매자 가입 2일, 저가 상품 비율 90%, 24시간 등록 80개, 사업자 중복"


def test_facts_text_states_values_without_interpreting() -> None:
    text = facts_text(SellerFraudFacts(**FACTS))
    assert text == "판매자 가입 2일, 저가 상품 비율 90%, 24시간 등록 80개, 사업자 중복"
    unknown = facts_text(SellerFraudFacts(**{**FACTS, "business_hash_duplicated": None}))
    assert "사업자" not in unknown


def test_dense_returns_only_fraud_cards_with_similarity(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    _card(svc, "card_fraud_1", "fraud", FRAUD_TITLE, FRAUD_BODY)
    _card(
        svc, "card_decision_1", "decision", FRAUD_TITLE, FRAUD_BODY
    )  # 같은 글이어도 사기 카드가 아니다
    out = _call(client)
    assert out.method == "dense"
    assert [c.card_id for c in out.cards] == ["card_fraud_1"]
    assert 0 < out.cards[0].similarity <= 1
    assert out.max_similarity == out.cards[0].similarity


def test_no_fraud_cards_gives_empty_and_zero(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    _card(svc, "card_decision_1", "decision", FRAUD_TITLE, FRAUD_BODY)
    out = _call(client)
    assert out.cards == [] and out.max_similarity == 0.0


def test_unrelated_fraud_card_is_below_the_cut(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.9)
    _card(svc, "card_fraud_2", "fraud", "배송 지연 환불 정책", "택배사 사정으로 늦어진 주문의 환불")
    assert _call(client).cards == []


def test_falls_back_to_dice_without_embedding(make) -> None:
    svc, client = make(similarity_threshold=0.1)
    _card(svc, "card_fraud_1", "fraud", FRAUD_TITLE, FRAUD_BODY)
    out = _call(client)
    assert out.method == "dice"
    assert [c.card_id for c in out.cards] == ["card_fraud_1"]


def test_falls_back_to_dice_when_embedding_fails(make) -> None:
    svc, client = make(embedder=BrokenEmbedder(), similarity_threshold=0.1)
    _card(svc, "card_fraud_1", "fraud", FRAUD_TITLE, FRAUD_BODY)
    assert _call(client).method == "dice"


def test_limit_and_order(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.0)
    for n in range(4):
        _card(svc, f"card_fraud_{n}", "fraud", f"사기 사례 {n}", f"{FRAUD_BODY} {n}")
    out = _call(client, {"facts": FACTS, "limit": 2})
    assert len(out.cards) == 2
    assert out.cards[0].similarity >= out.cards[1].similarity


def test_bad_input_is_rejected_and_state_unchanged(make) -> None:
    svc, client = make()
    res = client.post("/a2a", json=_body({"facts": {**FACTS, "low_price_ratio": 1.5}})).json()
    assert res["status"] != "completed"
    assert svc.gate.calls == []


def test_only_data_may_call(make) -> None:
    _, client = make()
    res = client.post("/a2a", json=_body({"facts": FACTS}, requester="marketing")).json()
    assert res["status"] != "completed"

"""fraud_score 핸들러: shop-api 판매자 정보·가격을 실제 shop-api 앱(경로·토큰 포함)으로 읽는다.

- 사업자 해시(shop-api에 없음)와 유사 사기 카드(hr skill 없음)는 "확인할 수 없음"이고
  점수에서 빠진다.
- shop-api를 못 읽으면 failed + unavailable, 없는 판매자는 failed + not_found.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_agent_template import config, events
from ax_contracts.data_access.shop_seller_prices import SellerProductPrice, ShopSellerPricesResult
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfile
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import FraudScoreInput, FraudScoreOutput
from ax_shop_api.db import Db
from ax_shop_api.main import create_app
from data_agent import handlers
from data_agent.clients.shop_client import HttpSellerClient, ShopApi
from data_agent.metrics.fraud import (
    DUP_BUSINESS,
    FRAUD_SIMILAR,
    LOW_PRICE,
    NEW_ACCOUNT,
    WEIGHTS,
    level_for,
    max_possible_score,
)
from data_agent.skills.fraud_score import score_fraud

# 소비자 확인용: 재무 판매자 심사가 data 출력을 읽는 함수 (finance 코드는 고치지 않고 부르기만 한다)
from finance_agent.sources import fraud_score_from


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()
    before = config._agent_name
    yield
    config._agent_name = before  # shop-api 앱이 프로세스 이름을 shop으로 바꾼다


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch) -> Db:
    """실제 shop-api 앱. 핸들러의 판매자 클라이언트가 이 앱을 data 토큰으로 부른다."""
    db = Db(":memory:")
    app = create_app(db, seed_data=False)

    def client() -> HttpSellerClient:
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://shop")
        return HttpSellerClient(ShopApi("http://shop", token="mock:data", client=http))

    monkeypatch.setattr(handlers, "get_seller_client", client)
    return db


def _seller(db: Db, seller_id: str, joined: datetime, products: list[tuple[int, datetime]]) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sellers VALUES (?, ?, 'book', 'shop-demo', NULL, ?)",
            (seller_id, seller_id, _iso(joined)),
        )
        for i, (price, created) in enumerate(products):
            conn.execute(
                "INSERT INTO products VALUES (?, ?, ?, 'book', ?, 1, 1, 'shop-demo', NULL, ?, ?)",
                (f"prd_{seller_id}_{i}", seller_id, "상품", price, _iso(created), _iso(created)),
            )


def _call(seller_id: str):  # noqa: ANN202
    meta = RequestMeta(
        context_id="ctx_fraud_test",
        token="mock:finance",
        path=["finance"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    request = AgentRequest(meta=meta, skill="fraud_score", input={"seller_id": seller_id})
    return asyncio.run(handlers.HANDLERS["fraud_score"](request))


def test_scores_known_rules_only_and_lists_unknown_rules(shop: Db) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    _seller(shop, "sel_ref", now - timedelta(days=400), [(20000, old)] * 6)  # 카테고리 기준 가격
    # 가입 2일, 상품 4개 중 2개 저가(5,000원 < 중앙값 20,000원의 50%), 4개 모두 24시간 안에 등록
    recent = now - timedelta(hours=2)
    products = [(5000, recent), (5000, recent), (20000, recent), (20000, recent)]
    _seller(shop, "sel_new", now - timedelta(days=2, hours=1), products)

    result = _call("sel_new")
    assert result.status == TaskStatus.COMPLETED
    out = FraudScoreOutput.model_validate(result.output)
    # NEW_ACCOUNT 15 + LOW_PRICE 25 × 0.5 = 12.5 → 28점 (BULK_LISTING은 50개 미만이라 0)
    assert (out.score, out.level) == (28, "low")
    assert out.unknown_rules == [DUP_BUSINESS, FRAUD_SIMILAR]
    assert out.similar_card_ids == []
    points = {r.rule_code: r.weight for r in out.reasons if r.note is None}
    assert points == {"NEW_ACCOUNT": 15.0, "LOW_PRICE": 12.5}
    notes = [r.note for r in out.reasons if r.note]
    assert len(notes) == 2 and all(n.startswith("확인할 수 없음: ") for n in notes)
    assert all(r.weight == 0.0 for r in out.reasons if r.note)
    checked_max = 100 - WEIGHTS[DUP_BUSINESS] - WEIGHTS[FRAUD_SIMILAR]
    assert "확인할 수 없음: DUP_BUSINESS" in out.summary
    assert "확인할 수 없음: FRAUD_SIMILAR" in out.summary
    assert (
        f"확인한 규칙 3개 기준 점수, 확인한 규칙으로 낼 수 있는 최대 {checked_max}점" in out.summary
    )
    # 불완전: 28 + DUP_BUSINESS 20 + FRAUD_SIMILAR 25 = 73점(high)
    assert out.complete is False
    assert (out.max_possible_score, out.max_possible_level) == (73, "high")
    assert "불완전: 확인 못 한 규칙을 모두 위험으로 보면 최대 73점(높음)" in out.summary
    assert "승인·보류는 정하지 않는다" in out.summary
    assert result.evidence is not None
    assert "확인할 수 없음: FRAUD_SIMILAR" in result.evidence.data_summary


def test_seller_without_active_products_cannot_tell_low_price(shop: Db) -> None:
    _seller(shop, "sel_empty", datetime.now(UTC) - timedelta(days=100), [])
    out = FraudScoreOutput.model_validate(_call("sel_empty").output)
    assert out.unknown_rules == ["LOW_PRICE", DUP_BUSINESS, FRAUD_SIMILAR]
    assert out.score == 0
    assert "확인한 규칙 2개 기준 점수, 확인한 규칙으로 낼 수 있는 최대 30점" in out.summary
    # 0 + LOW_PRICE 25 + DUP_BUSINESS 20 + FRAUD_SIMILAR 25 = 70점(high)
    assert (out.complete, out.max_possible_score, out.max_possible_level) == (False, 70, "high")


def test_unknown_seller_is_not_found(shop: Db) -> None:
    result = _call("sel_none")
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "not_found"
    assert result.output["code"] == "SELLER_NOT_FOUND"
    assert result.output["message"].startswith("확인할 수 없음")


def test_shop_api_down_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def client() -> HttpSellerClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(down))
        return HttpSellerClient(ShopApi("http://shop", token="mock:data", client=http))

    monkeypatch.setattr(handlers, "get_seller_client", client)
    result = _call("sel_1")
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "unavailable"
    assert result.output["code"] == "UNAVAILABLE"
    assert "score" not in result.output  # 숫자를 지어내지 않는다


def test_shop_api_rejecting_the_token_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(Db(":memory:"))

    def client() -> HttpSellerClient:
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://shop")
        return HttpSellerClient(ShopApi("http://shop", token="mock:finance", client=http))

    monkeypatch.setattr(handlers, "get_seller_client", client)
    result = _call("sel_0001")
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "unavailable")
    assert result.output["code"] == "FORBIDDEN"


def test_invalid_input(shop: Db) -> None:
    meta = RequestMeta(
        context_id="ctx_fraud_test",
        token="mock:finance",
        path=["finance"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    request = AgentRequest(meta=meta, skill="fraud_score", input={})
    result = asyncio.run(handlers.HANDLERS["fraud_score"](request))
    assert result.outcome == "invalid_input"


class _Seller:
    """사업자 중복이 확인된 판매자 (shop-api에 해시 컬럼이 생긴 뒤의 모양)."""

    def __init__(self, now: datetime, duplicated: bool) -> None:
        self.now, self.duplicated = now, duplicated

    async def seller_profile(self, seller_id: str) -> ShopSellerProfile:
        return ShopSellerProfile(
            seller_id=seller_id,
            joined_at=self.now - timedelta(days=2, hours=1),
            business_hash="a" * 64,
            business_hash_duplicated=self.duplicated,
            product_count=2,
            listings_last_24h=2,
        )

    async def seller_prices(self, seller_id: str) -> ShopSellerPricesResult:
        items = [
            SellerProductPrice(
                product_id=f"p{i}", category="book", price=p, category_median_price=20000
            )
            for i, p in enumerate([5000, 20000])
        ]
        return ShopSellerPricesResult(items=items)


def test_bound_with_one_unknown_rule() -> None:
    now = datetime.now(UTC)
    result = asyncio.run(score_fraud(FraudScoreInput(seller_id="sel_x"), _Seller(now, True), now))
    out = result.output
    # NEW_ACCOUNT 15 + LOW_PRICE 12.5 + DUP_BUSINESS 20 = 47.5 → 48점, FRAUD_SIMILAR만 빠짐
    assert (out.score, out.level) == (48, "mid")
    assert out.unknown_rules == [FRAUD_SIMILAR]
    assert out.complete is False
    assert (out.max_possible_score, out.max_possible_level) == (48 + WEIGHTS[FRAUD_SIMILAR], "high")
    assert "확인한 규칙으로 낼 수 있는 최대 75점" in out.summary
    assert "불완전: 확인 못 한 규칙을 모두 위험으로 보면 최대 73점(높음)" in out.summary


def test_max_possible_score_when_complete_equals_score() -> None:
    # 지금 핸들러는 FRAUD_SIMILAR가 늘 빠져 complete=true를 낼 수 없다.
    # 그래서 핸들러가 쓰는 계산으로 확인한다
    for score in (0, 29, 30, 69, 70, 100):
        assert max_possible_score(score, []) == score
        assert level_for(score) == ("high" if score >= 70 else "mid" if score >= 30 else "low")
    out = FraudScoreOutput(
        seller_id="sel_x",
        score=48,
        level="mid",
        summary="s",
        complete=True,
        max_possible_score=max_possible_score(48, []),
        max_possible_level=level_for(48),
    )
    assert (out.complete, out.max_possible_score, out.max_possible_level) == (True, 48, "mid")


def test_max_possible_score_is_capped_at_100() -> None:
    assert max_possible_score(90, [FRAUD_SIMILAR]) == 100
    assert max_possible_score(80, [LOW_PRICE, DUP_BUSINESS, NEW_ACCOUNT]) == 100
    assert max_possible_score(28, [DUP_BUSINESS, FRAUD_SIMILAR]) == 73


def test_finance_reads_the_bound_from_this_output(shop: Db) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    _seller(shop, "sel_ref", now - timedelta(days=400), [(20000, old)] * 6)
    recent = now - timedelta(hours=2)
    products = [(5000, recent), (5000, recent), (20000, recent), (20000, recent)]
    _seller(shop, "sel_new", now - timedelta(days=2, hours=1), products)

    output = _call("sel_new").output  # A2A로 나가는 dict 그대로
    assert output["complete"] is False
    assert output["score"] == 28
    # 재무는 불완전한 점수(28)가 아니라 보수적 상한(73)으로 심사한다
    bound = fraud_score_from(output)
    assert bound is not None and (bound.score, bound.confirmed) == (73, 28)
    assert bound.unknown_rules == (DUP_BUSINESS, FRAUD_SIMILAR)

    complete = {**output, "unknown_rules": [], "complete": True, "max_possible_score": 28}
    full = fraud_score_from(complete)
    assert full is not None and (full.score, full.confirmed, full.unknown_rules) == (28, 28, ())
    legacy = {k: v for k, v in output.items() if k not in ("complete", "max_possible_score")}
    assert fraud_score_from(legacy) is None  # 상한이 없는 예전 응답은 판단하지 않는다

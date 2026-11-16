"""사기 점수 평가용 판매자 데이터를 가짜 판매 서버(`fake_shop.py`)로 만든다 (`cases/sellers.json`).

판매자마다 가짜 판매 서버 DB를 하나 만들고, 정답지의 `facts`가 나오도록 판매자·상품을 넣는다.
data의 fraud_score 핸들러는 data의 `HttpSellerClient`로 가짜 판매 서버의 판매자 조회
(`/api/shop/sellers/{id}/profile`·`/prices`, 판매 서버와 같은 계산)를 읽는다(이상 탐지 정답지와 같은
방식). 판매 서버 코드(`ax_shop_api`)는 import하지 않는다.

- 판매자: 판매 서버에는 판매자를 만드는 API가 없어 시드와 같은 INSERT로 넣는다.
  가입 시각 = 지금 − `joined_days`일 − 1시간 (그래서 핸들러가 센 가입 일수가 `joined_days`다).
- 상품: 가짜 판매 서버 `create_product`로 넣는다. 시각은 가짜 판매 서버에 넣어 준 시계가 정한다.
  - 상품 수 n = max(`listings_last_24h`, 20). 그중 `listings_last_24h`개는 1시간 전, 나머지는
    24시간 30분 전에 만든다.
  - 저가 상품 k = n × `low_price_ratio`개는 5,000원, 나머지는 20,000원 (모두 `book`).
  - 기준 판매자(`sel_ref`)가 20,000원 상품을 n + 1개 판다. 그래서 카테고리 중앙값은 20,000원이고
    5,000원은 그 50% 미만이다.
- 넣을 수 없는 사실(`business_hash_duplicated`, `max_fraud_card_similarity`)은 넣지 않는다.
  판매 서버에 그 자료가 없어서 핸들러 결과에서 "확인할 수 없음"이 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from eval_world import fake_shop  # 같은 폴더의 가짜 판매 서버 (결제 평가 생성기와 같은 모듈)

REFERENCE_SELLER = "sel_ref"
NORMAL_PRICE = 20_000
LOW_PRICE = 5_000
MIN_PRODUCTS = 20
CATEGORY = "book"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_seller(shop: Any, seller_id: str, joined: datetime) -> None:
    with shop.db.tx() as conn:  # 판매 서버 시드와 같은 열 순서
        conn.execute(
            "INSERT INTO sellers VALUES (?, ?, ?, ?, NULL, ?)",
            (seller_id, seller_id, CATEGORY, fake_shop.TENANT_ID, _iso(joined)),
        )


def build(seller: dict[str, Any], now: datetime) -> Any:
    """판매자 하나의 가짜 판매 서버(`fake_shop.FakeShop`). `facts` 중 넣을 수 있는 것만 넣는다."""
    facts = seller["facts"]
    listings = int(facts["listings_last_24h"])
    total = max(listings, MIN_PRODUCTS)
    low = round(total * float(facts["low_price_ratio"]))
    if abs(low / total - float(facts["low_price_ratio"])) > 1e-9:
        raise ValueError(f"{seller['id']}: 상품 {total}개로 저가 비율을 정확히 만들 수 없다")

    clock = [now]
    shop = fake_shop.FakeShop(fake_shop.FakeShopDb(":memory:"), now=lambda: clock[0])
    _insert_seller(shop, REFERENCE_SELLER, now - timedelta(days=1000))
    _insert_seller(shop, seller["id"], now - timedelta(days=int(facts["joined_days"]), hours=1))
    clock[0] = now - timedelta(days=900)
    for _ in range(total + 1):  # 기준 가격이 과반이라 중앙값이 NORMAL_PRICE다
        shop.create_product(REFERENCE_SELLER, "기준 상품", CATEGORY, NORMAL_PRICE, 10)
    for i in range(total):
        recent = i < listings
        clock[0] = now - (timedelta(hours=1) if recent else timedelta(hours=24, minutes=30))
        price = LOW_PRICE if i < low else NORMAL_PRICE
        shop.create_product(seller["id"], f"상품 {i}", CATEGORY, price, 10)
    return shop


def transport(shop: Any) -> httpx.MockTransport:
    """판매 서버 `/api/shop/sellers/{id}/profile`·`/prices`와 같은 응답.

    지금 시각은 핸들러와 같은 실제 시계다(판매 서버처럼).
    """
    return fake_shop.FakeShopTransport(shop)

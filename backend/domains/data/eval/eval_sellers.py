"""사기 점수 평가용 판매자 데이터를 실제 shop-api 코드로 만든다 (`cases/sellers.json`).

판매자마다 임시 shop-api DB를 하나 만들고, 정답지의 `facts`가 나오도록 판매자·상품을 넣는다.
data의 fraud_score 핸들러는 data의 `HttpSellerClient`로 shop-api 조회 코드(`Lookups`)를 읽는다
(이상 탐지 정답지와 같은 방식).

- 판매자: shop-api에는 판매자를 만드는 API가 없어 시드(`ax_shop_api.seed`)와 같은 INSERT로 넣는다.
  가입 시각 = 지금 − `joined_days`일 − 1시간 (그래서 핸들러가 센 가입 일수가 `joined_days`다).
- 상품: shop-api `Shop.create_product`로 넣는다. 시각은 shop-api의 `_now`를 바꿔 끼워 정한다.
  - 상품 수 n = max(`listings_last_24h`, 20). 그중 `listings_last_24h`개는 1시간 전, 나머지는
    24시간 30분 전에 만든다.
  - 저가 상품 k = n × `low_price_ratio`개는 5,000원, 나머지는 20,000원 (모두 `book`).
  - 기준 판매자(`sel_ref`)가 20,000원 상품을 n + 1개 판다. 그래서 카테고리 중앙값은 20,000원이고
    5,000원은 그 50% 미만이다.
- 넣을 수 없는 사실(`business_hash_duplicated`, `max_fraud_card_similarity`)은 넣지 않는다.
  shop-api에 그 자료가 없어서 핸들러 결과에서 "확인할 수 없음"이 된다.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesQuery
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfileQuery
from ax_shop_api import service
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.lookups import Lookups
from ax_shop_api.service import ShopError

REFERENCE_SELLER = "sel_ref"
NORMAL_PRICE = 20_000
LOW_PRICE = 5_000
MIN_PRODUCTS = 20
CATEGORY = "book"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def _clock() -> Iterator[list[datetime]]:
    now: list[datetime] = [datetime.now(UTC)]
    original = service._now
    service._now = lambda: _iso(now[0])
    try:
        yield now
    finally:
        service._now = original


def _insert_seller(db: Db, seller_id: str, joined: datetime) -> None:
    with db.tx() as conn:  # 시드(`seed.py`)와 같은 열 순서
        conn.execute(
            "INSERT INTO sellers VALUES (?, ?, ?, ?, NULL, ?)",
            (seller_id, seller_id, CATEGORY, TENANT_ID, _iso(joined)),
        )


def build(seller: dict[str, Any], now: datetime) -> Db:
    """판매자 하나의 shop-api DB. `facts` 중 shop-api에 넣을 수 있는 것만 넣는다."""
    facts = seller["facts"]
    listings = int(facts["listings_last_24h"])
    total = max(listings, MIN_PRODUCTS)
    low = round(total * float(facts["low_price_ratio"]))
    if abs(low / total - float(facts["low_price_ratio"])) > 1e-9:
        raise ValueError(f"{seller['id']}: 상품 {total}개로 저가 비율을 정확히 만들 수 없다")

    db = Db(":memory:")
    shop = service.Shop(db)
    _insert_seller(db, REFERENCE_SELLER, now - timedelta(days=1000))
    _insert_seller(db, seller["id"], now - timedelta(days=int(facts["joined_days"]), hours=1))
    with _clock() as clock:
        clock[0] = now - timedelta(days=900)
        for _ in range(total + 1):  # 기준 가격이 과반이라 중앙값이 NORMAL_PRICE다
            shop.create_product(REFERENCE_SELLER, "기준 상품", CATEGORY, NORMAL_PRICE, 10)
        for i in range(total):
            recent = i < listings
            clock[0] = now - (timedelta(hours=1) if recent else timedelta(hours=24, minutes=30))
            price = LOW_PRICE if i < low else NORMAL_PRICE
            shop.create_product(seller["id"], f"상품 {i}", CATEGORY, price, 10)
    return db


def transport(db: Db) -> httpx.MockTransport:
    """shop-api `/api/shop/sellers/{id}/profile`·`/prices`와 같은 응답 (`Lookups`로 만든다)."""
    lookups = Lookups(db)  # 지금 시각: 핸들러와 같은 실제 시계

    def handle(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")  # ['', 'api', 'shop', 'sellers', id, kind]
        if len(parts) != 6 or parts[3] != "sellers":
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        seller_id, kind = parts[4], parts[5]
        try:
            if kind == "profile":
                data = lookups.seller_profile(ShopSellerProfileQuery(seller_id=seller_id))
            elif kind == "prices":
                data = lookups.seller_prices(ShopSellerPricesQuery(seller_id=seller_id))
            else:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        except ShopError as error:
            return httpx.Response(error.status, json={"error": {"code": error.code}})
        body = {"data": data.model_dump(mode="json"), "meta": {}}
        return httpx.Response(200, content=json.dumps(body))

    return httpx.MockTransport(handle)

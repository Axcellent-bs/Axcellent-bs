"""고객사 쇼핑몰(캣북스, shop-api)에 실제 고객 주문을 계속 넣는다. 실제 모드 실행(`run_all.sh`)이 함께 띄운다.

시드 구매자 중 한 명이 판매 중인 상품을 골라 주문하고 카드로 결제한다. 결제 성공·실패는 이 스크립트가
정하지 않는다. 쇼핑몰에 결제 장애가 주입돼 있으면 결제 서버가 5xx로 실패시키고, 쇼핑몰 감시가 그걸 재서
`shop.degraded`를 낸다. 그래서 장애 대응 사건이 실제 주문·결제 기록에서 시작한다.

    python scripts/shop_traffic.py            # SHOP_API_URL(기본 http://127.0.0.1:8010), 3초마다 1건
    SHOP_TRAFFIC_INTERVAL_S=1 python scripts/shop_traffic.py

표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("SHOP_API_URL", "http://127.0.0.1:8010").rstrip("/")
INTERVAL = float(os.getenv("SHOP_TRAFFIC_INTERVAL_S", "3"))


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def main() -> int:
    rng = random.Random()
    while True:
        try:
            _, users = call("GET", "/api/shop/users")
            _, products = call("GET", "/api/shop/products")
            buyers = [u["buyer_id"] for u in users.get("data", [])]
            items = [p for p in products.get("data", []) if p.get("active", True) and p.get("stock", 1) > 0]
            if buyers and items:
                picked = rng.sample(items, k=min(len(items), rng.choice([1, 1, 2])))
                status, order = call(
                    "POST",
                    "/api/shop/orders",
                    {"buyer_id": rng.choice(buyers), "items": [{"product_id": p["product_id"], "qty": 1} for p in picked]},
                )
                if status == 201:
                    order_id = order["data"]["order_id"]
                    status, paid = call("POST", f"/api/shop/orders/{order_id}/payments", {"method_code": "card"})
                    result = paid.get("data", {}).get("status") or paid.get("error", {}).get("code")
                    print(f"{time.strftime('%H:%M:%S')} {order_id} 결제 {status} {result}", flush=True)
                else:
                    print(f"{time.strftime('%H:%M:%S')} 주문 실패 {status} {order.get('error')}", flush=True)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
            print(f"{time.strftime('%H:%M:%S')} 쇼핑몰 호출 오류: {error}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())

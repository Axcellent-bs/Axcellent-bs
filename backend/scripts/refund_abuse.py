"""재무 AI가 주인공인 장애: 캣북스 고객 한 명이 짧은 시간에 주문을 연달아 사고
전부 환불받는다(환불 남용).

실제 흐름을 그대로 탄다. 이 스크립트는 고객과 상담원 역할만 한다.
1. 고객(시드 구매자 한 명)이 판매 중인 상품으로 주문 N건을 하고 카드로 결제한다 (shop-api).
2. 같은 고객이 그 주문들을 "단순 변심 취소"로 신청한다
   (`POST /api/shop/claims` → 고객지원 AI `create_claim`).
   고객지원 AI가 규칙으로 판정해 5만 원 미만은 자동 승인하고 환불 대기열에 올린다.
3. 상담원이 환불 대기열을 처리한다
   (dashboard-api `POST /api/dashboard/queue-entries/process`, 사람 토큰).
   고객지원 AI가 건마다 안전 관문(`refund_request`)을 거쳐 캣북스에서 현금을 환불한다.
4. 재무 AI 사후 감사가 안전 관문 실행 기록에서 "같은 고객 반복 환불"·"환불률 급변"을
   찾는다 (/finance).

    python scripts/refund_abuse.py               # 구매자는 무작위, 주문 4건
    python scripts/refund_abuse.py buy_0007 5    # 구매자·건수 지정

사람 토큰은 run_all.sh 가 만든 backend/.data/web.env 의 VITE_SAFETY_HUMAN_TOKEN을
읽는다(출력하지 않는다).
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
from pathlib import Path

SHOP = os.getenv("SHOP_API_URL", "http://127.0.0.1:8010").rstrip("/")
DASH = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8020").rstrip("/")
# 고객지원 자동 승인 한도(claim_rules.py)와 같게: 이 금액 미만 상품만 고른다
AUTO_APPROVE_LIMIT = 50000


def call(
    method: str, url: str, body: dict | None = None, token: str | None = None
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def human_token() -> str:
    env = Path(__file__).resolve().parent.parent / ".data" / "web.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_SAFETY_HUMAN_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(
        "backend/.data/web.env 가 없습니다. "
        "먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요."
    )


def main() -> int:
    rng = random.Random()
    _, users = call("GET", f"{SHOP}/api/shop/users")
    buyers = {u["buyer_id"]: u.get("display_name") or u["buyer_id"] for u in users.get("data", [])}
    buyer = sys.argv[1] if len(sys.argv) > 1 else rng.choice(sorted(buyers))
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    name = buyers.get(buyer, buyer)
    _, products = call("GET", f"{SHOP}/api/shop/products")
    items = [
        p for p in products.get("data", [])
        if p.get("active", True)
        and p.get("stock", 1) > 0
        and p.get("price", 0) + 3000 < AUTO_APPROVE_LIMIT
    ]
    if not items:
        raise SystemExit("주문할 상품이 없습니다.")
    print(f"고객: {name} ({buyer}) · 주문 {count}건을 사고 전부 취소·환불 신청합니다")

    orders = []
    for _ in range(count):
        product = rng.choice(items)
        order_body = {"buyer_id": buyer, "items": [{"product_id": product["product_id"], "qty": 1}]}
        status, order = call("POST", f"{SHOP}/api/shop/orders", order_body)
        if status != 201:
            print(f"  주문 실패 {status} {order.get('error')}")
            continue
        order_id = order["data"]["order_id"]
        status, paid = call(
            "POST", f"{SHOP}/api/shop/orders/{order_id}/payments", {"method_code": "card"}
        )
        state = paid.get("data", {}).get("status")
        print(f"  {order_id} {product.get('name', '')} 결제 {state}")
        if state == "paid":
            orders.append(order_id)
    if not orders:
        raise SystemExit(
            "결제된 주문이 없습니다. 캣북스에 결제 장애가 켜져 있으면 먼저 shop_fault.sh clear"
        )

    time.sleep(1)
    for order_id in orders:
        status, claim = call("POST", f"{SHOP}/api/shop/claims", {
            "buyer_id": buyer, "customer_name": name, "order_id": order_id,
            "type": "cancel", "reason": "change_of_mind",
            "description": "마음이 바뀌어서 취소할게요.",
        })
        data = claim.get("data", {})
        verdict = data.get("status") or claim.get("error")
        print(f"  {order_id} 취소 신청 {status} → 고객지원 AI 판정 {verdict}")

    time.sleep(1)
    status, done = call(
        "POST", f"{DASH}/api/dashboard/queue-entries/process", {}, token=human_token()
    )
    result = done.get("data", {})
    paid_out = result.get("processed") or result.get("results") or result
    print(f"상담원이 환불 대기열 처리 {status}: {json.dumps(paid_out, ensure_ascii=False)[:300]}")
    print("→ 재무 AI 화면(/finance)의 사후 감사에서 '같은 고객 반복 환불'을 확인하세요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""고객사 쇼핑몰(캣북스, shop-api)에 실제 고객 주문을 계속 넣는다.
실제 모드 실행(`run_all.sh`)이 함께 띄운다.

시드 구매자 중 한 명이 판매 중인 상품을 골라 주문하고 카드로 결제한다.
결제 성공·실패는 이 스크립트가 정하지 않는다. 쇼핑몰에 결제 장애가 주입돼 있으면
결제 서버가 5xx로 실패시키고, 쇼핑몰 감시가 그걸 재서
`shop.degraded`를 낸다. 그래서 장애 대응 사건이 실제 주문·결제 기록에서 시작한다.

결제가 실패한 고객 일부(`SHOP_TRAFFIC_INQUIRY_RATE`, 기본 0.3)는
쇼핑몰 문의 창구(`POST /api/shop/inquiries`)로 그 주문에 대해 문의를 남긴다.
실제 고객이 하는 행동을 흉내 낸 것이고, 문의 문장은 아래 예시 중에서 고른다.
문의는 고객지원 AI가 받아 처리한다.

재고 (서버 운영에서 재고가 바닥나 주문이 조용히 멈춘 일이 있었다):
- 판매 중인 상품의 재고가 `SHOP_TRAFFIC_LOW_STOCK`(기본 10) 아래로 떨어지면 경고 로그를 한 번 남긴다
- 주문할 상품이 하나도 없으면 1분마다 "재고 없음으로 대기 중" 로그를 남긴다
- `SHOP_TRAFFIC_REFILL=1`이면 기준 아래 상품을 `SHOP_TRAFFIC_REFILL_TO`(기본 200)개로 채운다
  (기본 꺼짐).
  보충은 상품 수정 API(`PATCH /api/shop/products/{id}`)라 관리자(admin) 토큰이 필요하다:
  `SHOP_ADMIN_TOKEN`, 없으면 에이전트 토큰 명부 `AX_AGENT_TOKENS`의 `admin` 값을 쓴다

    # SHOP_API_URL(기본 http://127.0.0.1:8010), 분당 20건(3초마다 1건)
    python scripts/shop_traffic.py
    SHOP_TRAFFIC_ORDERS_PER_MIN=60 python scripts/shop_traffic.py
    SHOP_TRAFFIC_REFILL=1 python scripts/shop_traffic.py

`SHOP_TRAFFIC_ORDERS_PER_MIN`이 없으면 예전 설정 `SHOP_TRAFFIC_INTERVAL_S`(기본 3초)를 쓴다.
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
from collections.abc import Callable

BASE = os.getenv("SHOP_API_URL", "http://127.0.0.1:8010").rstrip("/")
INQUIRY_RATE = float(os.getenv("SHOP_TRAFFIC_INQUIRY_RATE", "0.3"))
DEFAULT_ORDERS_PER_MIN = 20.0  # 예전 기본값(3초마다 1건)과 같다
WAIT_LOG_EVERY_S = 60.0  # 주문할 상품이 없을 때 대기 로그 간격


def interval_s() -> float:
    """주문 간격(초). `SHOP_TRAFFIC_ORDERS_PER_MIN` → `SHOP_TRAFFIC_INTERVAL_S` → 분당 20건."""
    per_min = os.getenv("SHOP_TRAFFIC_ORDERS_PER_MIN")
    if per_min:
        value = float(per_min)
        if value <= 0:
            raise ValueError("SHOP_TRAFFIC_ORDERS_PER_MIN은 0보다 커야 한다")
        return 60.0 / value
    return float(os.getenv("SHOP_TRAFFIC_INTERVAL_S") or 60.0 / DEFAULT_ORDERS_PER_MIN)


def admin_token() -> str:
    """재고 보충용 관리자 토큰. 값은 로그에 남기지 않는다."""
    token = os.getenv("SHOP_ADMIN_TOKEN", "").strip()
    if token:
        return token
    for item in os.getenv("AX_AGENT_TOKENS", "").split(","):
        name, sep, value = item.strip().partition("=")
        if sep and name.strip() == "admin" and value.strip():
            return value.strip()
    return ""


# 결제가 실패한 고객이 남기는 문의 (고객 시뮬레이터의 예시 문장)
FAILED_PAYMENT_INQUIRIES = [
    ("결제가 안 돼요", "결제 버튼을 눌렀는데 계속 오류가 나요. 카드 결제가 안 되는 건가요?"),
    ("결제 오류 문의", "결제하다가 오류가 났는데 돈이 빠져나간 건 아니죠? 확인 부탁드려요."),
    ("주문이 결제 실패로 나와요", "방금 주문했는데 결제 실패라고 떠요. 다시 해도 똑같아요."),
    ("선물용 책 결제", "내일까지 선물해야 하는데 결제가 계속 실패해요. 언제쯤 되나요?"),
    ("카드 결제 실패", "다른 카드로도 해 봤는데 둘 다 결제 오류가 나요. 사이트 문제인가요?"),
]


Call = Callable[..., tuple[int, dict]]


def now() -> str:
    return time.strftime("%H:%M:%S")


def call(
    method: str, path: str, body: dict | None = None, token: str | None = None
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


class StockWatch:
    """재고 경고·대기 로그·자동 보충. 로그는 `log`로 내보낸다(기본 print)."""

    def __init__(
        self,
        call: Call,
        *,
        low: int,
        refill: bool,
        refill_to: int,
        token: str,
        log: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.call = call
        self.low = low
        self.refill = refill
        self.refill_to = max(refill_to, low + 1)
        self.token = token
        self.log = log
        self.clock = clock
        self.warned: set[str] = set()  # 기준 아래로 떨어졌다고 이미 알린 상품
        self.waiting_since: float | None = None
        self.last_wait_log: float | None = None
        if refill and not token:
            log(f"{now()} 경고: 자동 보충이 켜졌지만 관리자 토큰이 없어 보충하지 않는다")

    def check(self, products: list[dict]) -> list[dict]:
        """판매 중인 상품의 재고를 보고, 필요하면 보충한 뒤 주문할 수 있는 상품을 돌려준다."""
        selling = [p for p in products if p.get("active", True)]
        for product in selling:
            pid, stock = product["product_id"], int(product.get("stock", 0))
            if stock >= self.low:
                self.warned.discard(pid)
                continue
            if pid not in self.warned:
                self.warned.add(pid)
                self.log(
                    f"{now()} 경고: 재고 부족 {pid} {product.get('name', '')} "
                    f"재고 {stock}개 (기준 {self.low}개)"
                )
            if self.refill and self.token:
                self._refill(product)
        items = [p for p in selling if int(p.get("stock", 0)) > 0]
        self._note_waiting(not items)
        return items

    def _refill(self, product: dict) -> None:
        pid, before = product["product_id"], int(product.get("stock", 0))
        status, body = self.call(
            "PATCH", f"/api/shop/products/{pid}", {"stock": self.refill_to}, self.token
        )
        if status == 200:
            product["stock"] = body.get("data", {}).get("stock", self.refill_to)
            self.warned.discard(pid)
            self.log(f"{now()} 재고 보충 {pid} {before}개 → {product['stock']}개")
        else:
            code = body.get("error", {}).get("code")
            self.log(f"{now()} 경고: 재고 보충 실패 {pid} {status} {code}")

    def _note_waiting(self, empty: bool) -> None:
        if not empty:
            if self.waiting_since is not None:
                self.log(f"{now()} 재고가 생겨 주문을 다시 넣는다")
            self.waiting_since = self.last_wait_log = None
            return
        current = self.clock()
        if self.waiting_since is None:
            self.waiting_since = current
        if self.last_wait_log is None or current - self.last_wait_log >= WAIT_LOG_EVERY_S:
            self.last_wait_log = current
            minutes = int((current - self.waiting_since) // 60)
            hint = "" if self.refill else " (SHOP_TRAFFIC_REFILL=1로 자동 보충을 켤 수 있다)"
            self.log(f"{now()} 재고 없음으로 대기 중: 주문할 상품이 없다 ({minutes}분째){hint}")


def _print(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    rng = random.Random()
    interval = interval_s()
    watch = StockWatch(
        call,
        low=int(os.getenv("SHOP_TRAFFIC_LOW_STOCK", "10")),
        refill=os.getenv("SHOP_TRAFFIC_REFILL") == "1",
        refill_to=int(os.getenv("SHOP_TRAFFIC_REFILL_TO", "200")),
        token=admin_token(),
        log=_print,
    )
    refill = "켜짐" if watch.refill else "꺼짐"
    _print(f"{now()} 주문 트래픽 시작: 분당 {60 / interval:g}건, 자동 보충 {refill}")
    while True:
        try:
            _, users = call("GET", "/api/shop/users")
            _, products = call("GET", "/api/shop/products")
            buyers = [u["buyer_id"] for u in users.get("data", [])]
            items = watch.check(products.get("data", []))
            if buyers and items:
                picked = rng.sample(items, k=min(len(items), rng.choice([1, 1, 2])))
                status, order = call(
                    "POST",
                    "/api/shop/orders",
                    {
                        "buyer_id": rng.choice(buyers),
                        "items": [{"product_id": p["product_id"], "qty": 1} for p in picked],
                    },
                )
                if status == 201:
                    order_id = order["data"]["order_id"]
                    status, paid = call(
                        "POST", f"/api/shop/orders/{order_id}/payments", {"method_code": "card"}
                    )
                    result = paid.get("data", {}).get("status") or paid.get("error", {}).get("code")
                    print(f"{now()} {order_id} 결제 {status} {result}", flush=True)
                    if result == "payment_failed" and rng.random() < INQUIRY_RATE:
                        buyer = order["data"]["buyer_id"]
                        subject, body = rng.choice(FAILED_PAYMENT_INQUIRIES)
                        inquiry = {
                            "buyer_id": buyer,
                            "order_id": order_id,
                            "subject": subject,
                            "body": body,
                        }
                        st, _ = call("POST", "/api/shop/inquiries", inquiry)
                        print(f"{now()} {order_id} 고객 문의 접수 {st}", flush=True)
                else:
                    print(f"{now()} 주문 실패 {status} {order.get('error')}", flush=True)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
            print(f"{now()} 쇼핑몰 호출 오류: {error}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

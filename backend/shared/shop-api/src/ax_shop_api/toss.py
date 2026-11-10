"""토스페이먼츠 테스트 결제: 설정, 승인 API 호출, 승인 흐름.

사람이 화면에서 결제할 때만 쓴다(토스 결제위젯 → `prepare` → 위젯 → `confirm` → 토스 승인 API).
자동 트래픽(`shop_traffic.py`)과 `POST /orders/{id}/payments`는 그대로 가짜 결제다.

- `PAYMENT_PROVIDER`: `fake`(기본) | `toss_test`. `TOSS_SECRET_KEY`는 `backend/.env`에서만 읽는다
  (`run_all.sh`가 환경변수로 넘긴다). 키가 없거나 `test_`로 시작하지 않으면(라이브 키 포함)
  가짜 결제로 동작하고 시작할 때 경고 한 줄을 남긴다. 키 값은 어디에도 출력·로그하지 않는다.
- 장애 주입(SHOP-06): `failure`면 토스를 부르지 않고 `PG_UNAVAILABLE`(5xx)로 실패시킨다.
  `latency`는 가짜 결제처럼 값으로만 더한다(실제로 기다리지 않는다).
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from ax_shop_api.service import BASELINE_LATENCY_MS, PaymentOutcome, Shop, ShopError

PROVIDER_ENV = "PAYMENT_PROVIDER"
KEY_ENV = "TOSS_SECRET_KEY"
FAKE, TOSS_TEST = "fake", "toss_test"
CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"
TIMEOUT_S = 10.0

# 토스 응답 `method`(한글) → 우리 `method_code`. 그 밖(가상계좌·상품권 등)은 other
METHOD_CODES = {"카드": "card", "계좌이체": "transfer", "간편결제": "easy_pay", "휴대폰": "phone"}


@dataclass(frozen=True)
class PaymentConfig:
    provider: str  # fake | toss_test (실제로 동작하는 값)
    secret_key: str | None = field(default=None, repr=False)  # repr·로그에 나가지 않게
    warning: str | None = None  # 시작할 때 남길 경고 한 줄 (키 값은 담지 않는다)


def load_config(env: Mapping[str, str] | None = None) -> PaymentConfig:
    env = os.environ if env is None else env
    wanted = (env.get(PROVIDER_ENV) or FAKE).strip().lower()
    if wanted == FAKE:
        return PaymentConfig(FAKE)
    key = (env.get(KEY_ENV) or "").strip()
    if wanted != TOSS_TEST:
        why = f"{PROVIDER_ENV} 값이 fake·toss_test가 아니라"
    elif not key:
        why = f"{KEY_ENV}가 없어"
    elif key.startswith("live_"):
        why = f"{KEY_ENV}가 라이브 키라 쓰지 않는다."
    elif not key.startswith("test_"):
        why = f"{KEY_ENV}가 테스트 키(test_)가 아니라"
    else:
        return PaymentConfig(TOSS_TEST, key)
    return PaymentConfig(FAKE, warning=f"{why} 가짜 결제로 동작한다")


def _code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return str(code) if code else None


class TossClient:
    """토스 승인 API(`POST /v1/payments/confirm`) 한 번 호출. 걸린 시간을 실제로 잰다."""

    def __init__(
        self,
        secret_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._auth = "Basic " + base64.b64encode(f"{secret_key}:".encode()).decode()
        self._transport = transport
        self._timeout = timeout
        self._timer = timer

    def __repr__(self) -> str:  # 인증 값이 repr로 새지 않게
        return "TossClient()"

    async def confirm(self, payment_key: str, toss_order_id: str, amount: int) -> PaymentOutcome:
        headers = {"Authorization": self._auth, "Idempotency-Key": toss_order_id}
        body = {"paymentKey": payment_key, "orderId": toss_order_id, "amount": amount}
        started = self._timer()
        try:
            client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
            async with client:
                response = await client.post(CONFIRM_URL, json=body, headers=headers)
        except httpx.TimeoutException:
            return self._unavailable(started, "TIMEOUT")
        except httpx.HTTPError:
            return self._unavailable(started, "CONNECT_ERROR")
        elapsed = (self._timer() - started) * 1000
        if response.status_code == 200:
            data = response.json()
            method = METHOD_CODES.get(str(data.get("method") or ""), "other")
            return PaymentOutcome(True, None, elapsed, method_code=method)
        if 400 <= response.status_code < 500:  # 거절·잘못된 요청: 5xx로 세지 않는다
            return PaymentOutcome(False, "PG_DECLINED", elapsed, failure_detail=_code(response))
        detail = _code(response) or f"HTTP_{response.status_code}"
        return PaymentOutcome(False, "PG_UNAVAILABLE", elapsed, True, failure_detail=detail)

    def _unavailable(self, started: float, detail: str) -> PaymentOutcome:
        elapsed = (self._timer() - started) * 1000
        return PaymentOutcome(False, "PG_UNAVAILABLE", elapsed, True, failure_detail=detail)


class TossPayments:
    """승인 흐름. 같은 시도를 동시에 승인해도 토스는 한 번만 부른다(프로세스 안 잠금)."""

    def __init__(self, shop: Shop, client: TossClient) -> None:
        self.shop = shop
        self.client = client
        self._locks: dict[str, asyncio.Lock] = {}

    async def confirm(
        self, toss_order_id: str, payment_key: str, amount: int
    ) -> tuple[dict[str, Any], bool]:
        """`(주문, 처음 결과를 돌려준 것인지)`."""
        self.shop.toss_start(toss_order_id, amount)  # 없는 시도·금액 불일치는 잠금 전에 거른다
        lock = self._locks.setdefault(toss_order_id, asyncio.Lock())
        async with lock:
            start = self.shop.toss_start(toss_order_id, amount)
            if start.replay is not None:
                return start.replay, True
            gateway = self.shop.gateway
            if gateway.failing:  # 장애 주입: 토스를 부르지 않는다
                outcome = PaymentOutcome(
                    False, "PG_UNAVAILABLE", BASELINE_LATENCY_MS + gateway.delay_ms, True,
                    failure_detail="FAULT_INJECTED",
                )  # fmt: skip
            else:
                outcome = await self.client.confirm(payment_key, toss_order_id, start.amount)
                outcome.latency_ms += gateway.delay_ms
            order = self.shop.toss_finish(start, outcome, payment_key)
        self._locks.pop(toss_order_id, None)
        return order, False


def require(payments: TossPayments | None) -> TossPayments:
    if payments is None:
        raise ShopError("TOSS_DISABLED", "토스 테스트 결제가 꺼져 있다(가짜 결제 모드)", 409)
    return payments

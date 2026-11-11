"""ax-shop-api 서버 진입점.

경로는 `/api/shop/<복수 명사>`, 성공은 `{data, meta}`, 실패는 `{error: {code, message}}`다.
PR 1: 상품·주문·가짜 결제·시드·이벤트 발행. PR 2: 장애 주입·해제와 `shop.degraded` 측정.
관리 API(`/api/shop/admin/*`)는 `Authorization: Bearer <토큰>`의 요청자가 허용 목록에 있어야 한다.
사람 로그인(JWT)은 아직 없어서 관리자는 토큰 이름 `admin`으로 대신한다(TODO).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field

from ax_agent_template.config import set_agent_name
from ax_agent_template.server import verify_token
from ax_agent_template.services import department_of
from ax_contracts.data_access.shop_failed_orders import ShopFailedOrdersQuery
from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesQuery
from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesQuery
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfileQuery
from ax_shop_api import (
    claim_inbox,
    inbox,
    inquiries,
    marketing_client,
    orchestrator_relay,
    outbox,
    retention,
    support_client,
    toss,
)
from ax_shop_api.db import Db
from ax_shop_api.lookups import Lookups
from ax_shop_api.promotions import Promotions
from ax_shop_api.refunds import Refunds
from ax_shop_api.seed import seed
from ax_shop_api.service import ORDER_PAGE_DEFAULT, ORDER_PAGE_MAX, Shop, ShopError
from ax_shop_api.storefront import Storefront


class ProductIn(BaseModel):
    seller_id: str
    name: str
    category_code: str
    price: int = Field(ge=0)
    stock: int = Field(ge=0)


class SellerProductIn(BaseModel):
    name: str
    category_code: str
    price: int = Field(ge=0)
    stock: int = Field(ge=0)


class SellerIn(BaseModel):
    """판매자 입점 신청. 처음 올릴 상품을 같이 받는다 (입점 심사가 상품 가격·수를 본다)."""

    name: str
    category_codes: list[str] = Field(min_length=1)
    documents_verified: bool = False
    products: list[SellerProductIn] = Field(default_factory=list, max_length=500)


class ProductPatch(BaseModel):
    name: str | None = None
    price: int | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    active: bool | None = None


class OrderLine(BaseModel):
    product_id: str
    qty: int = Field(gt=0)


class OrderIn(BaseModel):
    buyer_id: str
    items: list[OrderLine]
    points_used: int = Field(default=0, ge=0)  # 포인트 사용분(원). 품목 금액 비율로 나눠 기록한다
    # 같은 구매자의 같은 키는 주문 하나다. 재시도·중복 전송이 주문을 두 번 만들지 않게 한다
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    # 본인의 쿠폰 한 건과 쓸 장수. 할인은 상품 금액 범위 안이고 배송비는 제외한다
    coupon_id: str | None = None
    coupon_units: int = Field(default=1, ge=1)
    # 유입 출처(utm_source·utm_medium·utm_campaign). 선택. 영문·숫자·`-`·`_`·`.` 64자까지이고
    # 어긋난 값은 주문을 막지 않고 그 값만 비운다. 멱등 비교에는 넣지 않는다
    src_source: str | None = None
    src_medium: str | None = None
    src_campaign: str | None = None


class CartPriceIn(BaseModel):
    items: list[OrderLine]
    points_used: int = Field(default=0, ge=0)
    coupon_id: str | None = None
    coupon_units: int = Field(default=1, ge=1)
    buyer_id: str | None = None  # 쿠폰을 계산할 때만 필요하다(본인 쿠폰인지 확인한다)


class SessionIn(BaseModel):
    buyer_id: str  # 데모 사용자 선택. 비밀번호·토큰은 없다


class ShippedIn(BaseModel):
    shipped_at: AwareDatetime | None = None  # 비우면 지금(시연·시드용으로 지난 시각도 가능)


class DeliveredIn(BaseModel):
    delivered_at: AwareDatetime | None = None  # 비우면 지금(시연·시드용으로 지난 시각도 가능)


class PaymentIn(BaseModel):
    method_code: str = "card"
    # 가짜 결제 서버에 성공·실패를 요청한다. 장애 주입은 이후 PR에서 게이트웨이가 정한다
    simulate: Literal["success", "failure"] | None = None


class TossConfirmIn(BaseModel):
    """토스 결제위젯 성공 리다이렉트의 값 그대로. 금액은 시도 금액과 비교만 한다(믿지 않는다)."""

    toss_order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,64}$")
    payment_key: str = Field(min_length=1, max_length=200)
    amount: int = Field(ge=0)


class CancelIn(BaseModel):
    reason_code: Literal["buyer", "seller", "system"] = "buyer"


class FaultIn(BaseModel):
    kind: Literal["latency", "failure"]
    delay_ms: int = Field(default=2000, ge=0, le=60000)  # kind=latency일 때 더할 지연


class BannerIn(BaseModel):
    notice_id: str
    text: str = Field(min_length=1, max_length=200)  # 공개 문구. 화면이 이스케이프해서 보여 준다
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    context_id: str | None = None  # 사후 추적용 사건 번호(선택)


class ClaimItemIn(BaseModel):
    order_item_id: str
    qty: int = Field(ge=1)


class ClaimIn(BaseModel):
    """고객 반품·환불 신청. 금액은 받지 않는다(고객지원이 주문 사실로 계산한다)."""

    buyer_id: str
    customer_name: str = Field(min_length=1, max_length=100)
    order_id: str
    type: Literal["cancel", "return", "exchange"]
    reason: Literal[
        "change_of_mind",
        "defect",
        "wrong_item",
        "damaged",
        "missing_item",
        "late_delivery",
        "other",
    ]
    items: list[ClaimItemIn] | None = None  # 없으면 주문 전체
    unsealed: bool = False
    evidence_count: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=2000)  # 고객이 쓴 문장. untrusted로만 넘긴다


class InquiryImageIn(BaseModel):
    """문의에 붙이는 사진 한 장. 내용은 로그·오류에 싣지 않고 고객지원에 `untrusted`로만 넘긴다."""

    model_config = {"extra": "forbid"}

    ref: str = Field(max_length=200)
    media_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"
    data_base64: (
        str  # 크기는 `inquiries.clean_images`가 확인한다(413, 사진 값은 오류에 싣지 않는다)
    )


class InquiryIn(BaseModel):
    """고객 문의 접수. 주문 선택이 필수다(결정 1). 제목·본문 길이는 서버가 확인한다."""

    model_config = {"extra": "forbid"}

    buyer_id: str
    order_id: str
    subject: str | None = None
    body: str
    images: list[InquiryImageIn] = Field(
        default_factory=list
    )  # 영수증 사진. 서버가 개수·크기를 확인


class ClaimResultIn(BaseModel):
    """고객지원이 판정한 클레임 결과를 되돌려 쓴다. `result`는 고객지원이 돌려준 클레임 그대로다."""

    model_config = {"extra": "forbid"}

    support_claim_id: int | None = Field(default=None, ge=1)  # 클레임이 안 만들어졌으면 비운다
    status: str = Field(min_length=1, max_length=40)
    result: dict


class InquirySyncedIn(BaseModel):
    """AXcellent가 문의를 읽어 처리했다는 표시. 고객지원이 만든 문의 번호를 함께 보낸다."""

    model_config = {"extra": "forbid"}

    support_inquiry_id: int = Field(ge=1)


class IntakeFailedIn(BaseModel):
    """읽어 간 쪽이 접수를 처리하지 못했다는 알림. 사유는 짧은 코드다(원문을 싣지 않는다)."""

    model_config = {"extra": "forbid"}

    reason: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:-]+$")


class IdeaIn(BaseModel):
    """고객 아이디어. 문의·불만과 따로 받는다. 글은 `untrusted`로만 넘긴다."""

    model_config = {"extra": "forbid"}

    buyer_id: str
    kind: Literal["event", "feature", "improvement", "product", "other"]
    text: str = Field(min_length=1, max_length=500)


class InquiryFeedbackIn(BaseModel):
    """해결된 문의에 고객이 남기는 평가. 후기 글은 `untrusted`로만 넘긴다."""

    model_config = {"extra": "forbid"}

    buyer_id: str
    rating: int = Field(ge=1, le=5)
    resolved: bool  # 고객이 "문제가 해결됐다"고 답했는지
    comment: str = Field(default="", max_length=300)


class ReturnReceivedIn(BaseModel):
    order_id: str  # 클레임의 주문(고객지원 응답과 대조한다)
    inspection: Literal["pass", "fail", "partial"] | None = None


class RefundIn(BaseModel):
    refund_amount: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1)  # `claim.context_id:refund_request:claim:<id>`
    context_id: str | None = None


class CouponIn(BaseModel):
    idempotency_key: str = Field(min_length=1)  # `context_id + 액션 + 대상`
    purpose_code: Literal["campaign", "compensation"]
    buyer_id: str
    count: int = Field(gt=0)
    unit_value: int = Field(gt=0)
    campaign_id: str | None = None
    context_id: str | None = None  # 사후 추적용 사건 번호(선택). 멱등 비교에는 쓰지 않는다
    expires_at: AwareDatetime | None = None  # 이 시각부터 주문에 쓸 수 없다(없으면 무기한)


# 브라우저(shop-web 3000, dashboard-web 3001)가 직접 부르므로 허용할 출처만 연다.
# `SHOP_CORS_ORIGINS`(쉼표로 구분)로 바꾼다.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"
)

# 관리 API 허용 요청자. 장애 해제는 infra 롤백이 관문을 거쳐 부른다(후속 결정 15)
INJECT_ALLOWED = {"admin"}
# 상품 등록·수정(재고 보충 포함)은 운영자(admin)만
PRODUCT_WRITERS = {"admin"}
RELEASE_ALLOWED = {"admin", "infra"}
# 공지·쿠폰 쓰기는 마케팅 관문 토큰만, 쿠폰 조회는 사후 검증·중복 확인을 하는 부서와
# 캠페인 성과를 세는 data(읽기 전용)까지
MARKETING_ONLY = {"marketing"}
COUPON_READERS = {"marketing", "support", "finance", "orchestrator", "data"}
# 주문 유입 출처 조회(#647)는 데이터분석 커넥터 토큰(data)만. 관리자(admin)도 뺀다
ATTRIBUTION_READERS = {"data"}
# 현금 환불(SHOP-12)은 고객지원 관문 토큰만(마케팅 토큰 거부)
REFUND_ALLOWED = {"support"}
# 문의함 읽기·읽음 표시는 AXcellent 고객지원(과 운영자)만. 문의 원문과 사진이 들어 있다
INQUIRY_READERS = {"support", "admin"}
# 데이터 조회(읽기 전용)는 분석을 하는 data와 관리자만
DATA_READERS = {"data", "admin"}
# 총괄 사건 읽기 중계는 관리자와 읽기 전용 열람자(`viewer`)만.
# 사람 로그인(소유자·공동소유자·분야 관리자)은 사람 인증 뒤에 붙인다
ORCHESTRATOR_VIEWERS = {"admin", "viewer"}
# 장애 주입 상태 읽기(GET)는 열람자도 본다. 주입·해제는 그대로 admin·infra만
FAULT_STATE_READERS = INJECT_ALLOWED | RELEASE_ALLOWED | {"viewer"}
# 읽기 전용 열람 토큰(`SHOP_VIEWER_TOKEN`). 에이전트 토큰 명부(`AX_AGENT_TOKENS`)와 따로 둔다.
# 명부에 넣으면 총괄 등 다른 서버가 명부의 모든 에이전트를 받아 주는 길(`require_agent()`)이
# 같이 열린다.
# 이 서버의 `ORCHESTRATOR_VIEWERS`·`FAULT_STATE_READERS`에서만 통하고 그 밖은 403이다.
VIEWER_TOKEN_ENV = "SHOP_VIEWER_TOKEN"
VIEWER_TOKEN_MIN_LENGTH = 24  # 짧은 값은 켜지 않는다


def _is_human_token(token: str) -> bool:
    """사람 토큰(`SAFETY_HUMAN_TOKENS="이름:토큰,..."`)인가. 관리 API에는 쓸 수 없다(403)."""
    for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        if sep and name and known and hmac.compare_digest(known, token):
            return True
    return False


def _is_viewer_token(token: str) -> bool:
    """읽기 전용 열람 토큰인가. 설정이 없거나 너무 짧으면 꺼져 있다."""
    known = os.getenv(VIEWER_TOKEN_ENV, "").strip()
    return len(known) >= VIEWER_TOKEN_MIN_LENGTH and hmac.compare_digest(known, token)


def _caller(allowed: set[str]) -> Callable[..., str]:
    def dependency(authorization: str | None = Header(default=None)) -> str:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ShopError("UNAUTHORIZED", "토큰이 필요하다", 401)
        if _is_viewer_token(token.strip()):
            if "viewer" not in allowed:
                raise ShopError("FORBIDDEN", "읽기 전용 토큰으로는 호출할 수 없는 API다", 403)
            return "viewer"
        try:
            requester = verify_token(token.strip())  # 요청자는 토큰에서 꺼낸다
        except PermissionError as error:
            if _is_human_token(token.strip()):
                raise ShopError("FORBIDDEN", "에이전트 토큰이 필요하다", 403) from error
            raise ShopError("UNAUTHORIZED", "토큰을 검증할 수 없다", 401) from error
        # 서비스로 나뉜 에이전트(claims 등)는 원래 부서 이름(support)으로도 인정한다
        if requester not in allowed and department_of(requester) not in allowed:
            raise ShopError("FORBIDDEN", "이 API를 호출할 수 없는 요청자다", 403)
        return requester

    return dependency


def _ok(data: Any, **meta: Any) -> dict[str, Any]:
    return {"data": data, "meta": meta}


def _query(model: type[BaseModel], **values: Any) -> Any:
    """쿼리 값을 계약 모델로 검증한다. 시간대가 없는 시각·모르는 지표는 400이다."""
    try:
        return model.model_validate(values)
    except ValueError as error:
        raise ShopError(
            "INVALID_QUERY", f"조회 조건이 올바르지 않다: {error.__class__.__name__}"
        ) from error


def create_app(
    db: Db | None = None,
    seed_data: bool = True,
    toss_transport: httpx.AsyncBaseTransport | None = None,  # 시험용(토스 흉내)
) -> FastAPI:
    set_agent_name("shop")  # SHOP_MOCK=1로 목 모드(환경변수 접두사 SHOP)
    database = db or Db()
    if seed_data:
        seed(database)
    shop = Shop(database)
    payment_config = toss.load_config()
    if payment_config.warning:  # 키 값은 담지 않는다
        logging.getLogger(__name__).warning(payment_config.warning)
    toss_payments = (
        toss.TossPayments(shop, toss.TossClient(payment_config.secret_key, toss_transport))
        if payment_config.provider == toss.TOSS_TEST and payment_config.secret_key
        else None
    )
    promotions = Promotions(database)
    lookups = Lookups(database)
    refunds = Refunds(database)
    storefront = Storefront(database)
    interval = float(os.getenv("SHOP_MONITOR_INTERVAL_S") or 5)  # 0이면 주기 판정을 끈다

    async def _monitor_loop() -> None:
        """결제 요청이 없어도 지속 시간을 판정하도록 주기적으로 평가한다."""
        while True:
            await asyncio.sleep(interval)
            shop.evaluate_monitor()
            await outbox.drain(database)  # 쌓인 미발행은 여기서 묶음씩 비운다

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_monitor_loop()) if interval > 0 else None
        retention_task = retention.start_background(database)  # 파일 DB일 때만, 기본 dry-run
        try:
            yield
        finally:
            if task:
                task.cancel()
            if retention_task:
                retention_task.cancel()

    app = FastAPI(title="ax-shop-api", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in (os.getenv("SHOP_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS).split(",")
            if origin.strip()
        ],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.state.shop = shop
    app.state.promotions = promotions
    app.state.lookups = lookups
    app.state.refunds = refunds
    inquiry_limiter = inquiries.InquiryRateLimiter()
    app.state.inquiry_limiter = inquiry_limiter
    feedback_limiter = inquiries.InquiryRateLimiter()  # 문의 접수와 한도를 따로 센다
    idea_limiter = inquiries.InquiryRateLimiter()  # 아이디어도 문의와 따로 센다

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        """422 본문에서 `input`(요청이 보낸 값)을 뺀다. 사진·본문이 오류로 되돌아가지 않게."""
        errors = [{k: v for k, v in e.items() if k != "input"} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @app.exception_handler(ShopError)
    async def _shop_error(_: Request, error: ShopError) -> JSONResponse:
        body = {"error": {"code": error.code, "message": error.message}}
        return JSONResponse(status_code=error.status, content=body)

    @app.get("/health")
    def health() -> dict:
        return {"data": {"status": "ok"}, "meta": {}}

    @app.get("/api/shop/users")
    async def list_users() -> dict:
        return _ok(storefront.list_users())

    @app.get("/api/shop/users/{buyer_id}")
    async def get_user(buyer_id: str) -> dict:
        return _ok(storefront.get_user(buyer_id))

    @app.post("/api/shop/sessions")
    async def select_user(body: SessionIn) -> dict:
        # 로그인은 "선택"일 뿐이다. 서버는 세션을 저장하지 않고 화면이 buyer_id를 들고 있는다
        return _ok({"user": storefront.get_user(body.buyer_id)})

    @app.get("/api/shop/categories")
    async def list_categories() -> dict:
        return _ok(storefront.list_categories())

    @app.post("/api/shop/cart/price")
    async def price_cart(body: CartPriceIn) -> dict:
        lines = [(i.product_id, i.qty) for i in body.items]
        return _ok(
            storefront.price_cart(
                lines, body.points_used, body.coupon_id, body.coupon_units, body.buyer_id
            )
        )

    @app.get("/api/shop/sellers")
    async def list_sellers() -> dict:
        return _ok(shop.list_sellers())

    @app.get("/api/shop/products")
    async def list_products(seller_id: str | None = None, category_code: str | None = None) -> dict:
        return _ok(shop.list_products(seller_id, category_code))

    @app.get("/api/shop/products/{product_id}")
    async def get_product(product_id: str) -> dict:
        return _ok(shop.get_product(product_id))

    @app.post("/api/shop/sellers", status_code=201)
    async def register_seller(body: SellerIn, _: str = Depends(_caller(PRODUCT_WRITERS))) -> dict:
        """판매자 입점 (운영자만). `seller.registered`로 총괄이 입점 심사를 연다."""
        created = shop.register_seller(
            body.name,
            body.category_codes,
            body.documents_verified,
            [p.model_dump() for p in body.products],
        )
        await outbox.flush(database)
        return _ok(created)

    @app.post("/api/shop/products", status_code=201)
    async def create_product(body: ProductIn, _: str = Depends(_caller(PRODUCT_WRITERS))) -> dict:
        return _ok(shop.create_product(**body.model_dump()))

    @app.patch("/api/shop/products/{product_id}")
    async def update_product(
        product_id: str, body: ProductPatch, _: str = Depends(_caller(PRODUCT_WRITERS))
    ) -> dict:
        return _ok(shop.update_product(product_id, body.model_dump(exclude_none=True)))

    # 동기 핸들러: FastAPI가 스레드에서 돌려 DB를 읽는 동안 이벤트 루프(/health 등)를 막지 않는다.
    # Db는 연결 하나를 RLock으로 감싸고(check_same_thread=False) sqlite는 직렬 모드라
    # 스레드에서 써도 된다
    @app.get("/api/shop/orders")
    def list_orders(
        buyer_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: Annotated[int, Query(ge=1, le=ORDER_PAGE_MAX)] = ORDER_PAGE_DEFAULT,
        cursor: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")] = None,
    ) -> dict:
        """order_id 순 한 페이지. 다음 페이지는 `meta.next_cursor`를 cursor로 준다(끝이면 null)."""
        orders, next_cursor = shop.list_orders(buyer_id, since, until, limit, cursor)
        return _ok(orders, next_cursor=next_cursor)

    # 고정 경로는 `{order_id}` 경로보다 먼저 등록해야 가로채이지 않는다
    @app.get("/api/shop/orders/attributed")
    async def attributed_orders(
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        campaign_id: Annotated[str | None, Query(max_length=64)] = None,
        source: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        _: str = Depends(_caller(ATTRIBUTION_READERS)),
    ) -> dict:
        if from_.tzinfo is None or to.tzinfo is None:
            raise ShopError("INVALID_QUERY", "from·to에는 시간대가 필요하다")
        return _ok(lookups.attributed_orders(from_, to, campaign_id, source, limit))

    @app.get("/api/shop/orders/{order_id}")
    async def get_order(order_id: str) -> dict:
        return _ok(shop.get_order(order_id))

    @app.post("/api/shop/orders", status_code=201)
    async def create_order(body: OrderIn, response: Response) -> dict:
        order, replayed = shop.place_order(
            body.buyer_id,
            [(i.product_id, i.qty) for i in body.items],
            body.points_used,
            body.idempotency_key,
            body.coupon_id,
            body.coupon_units,
            body.src_source,
            body.src_medium,
            body.src_campaign,
        )
        if replayed:
            response.status_code = 200  # 새로 만든 게 아니라 처음 주문을 돌려준다
        await outbox.flush(database)
        return _ok(order, replayed=replayed)

    @app.post("/api/shop/orders/{order_id}/payments")
    async def pay(order_id: str, body: PaymentIn) -> dict:
        order = shop.pay(order_id, body.method_code, body.simulate)
        shop.evaluate_monitor()
        await outbox.flush(database)
        return _ok(order)

    @app.get("/api/shop/payment-config")
    def payment_config_view() -> dict:
        return _ok({"provider": payment_config.provider})  # 키는 담지 않는다

    @app.post("/api/shop/orders/{order_id}/payments/toss/prepare")
    def toss_prepare(order_id: str) -> dict:
        toss.require(toss_payments)
        return _ok(shop.toss_prepare(order_id))

    @app.post("/api/shop/payments/toss/confirm")
    async def toss_confirm(body: TossConfirmIn) -> dict:
        payments = toss.require(toss_payments)
        order, replayed = await payments.confirm(body.toss_order_id, body.payment_key, body.amount)
        shop.evaluate_monitor()
        await outbox.flush(database)
        return _ok(order, replayed=replayed, toss_order_id=body.toss_order_id)

    @app.post("/api/shop/orders/{order_id}/cancel")
    async def cancel(order_id: str, body: CancelIn) -> dict:
        order = shop.cancel(order_id, body.reason_code)
        await outbox.flush(database)
        return _ok(order)

    @app.post("/api/shop/admin/orders/{order_id}/shipped")
    async def mark_shipped(
        order_id: str, body: ShippedIn, _: str = Depends(_caller(INJECT_ALLOWED))
    ) -> dict:
        return _ok(shop.mark_shipped(order_id, body.shipped_at))

    @app.post("/api/shop/admin/orders/{order_id}/delivered")
    async def mark_delivered(
        order_id: str, body: DeliveredIn, _: str = Depends(_caller(INJECT_ALLOWED))
    ) -> dict:
        return _ok(shop.mark_delivered(order_id, body.delivered_at))

    @app.get("/api/shop/admin/faults")
    async def fault_state(_: str = Depends(_caller(FAULT_STATE_READERS))) -> dict:
        return _ok(shop.fault_state())

    @app.post("/api/shop/admin/faults", status_code=201)
    async def inject_fault(body: FaultIn, _: str = Depends(_caller(INJECT_ALLOWED))) -> dict:
        return _ok(shop.inject_fault(body.kind, body.delay_ms))

    @app.delete("/api/shop/admin/faults")
    async def release_fault(caller: str = Depends(_caller(RELEASE_ALLOWED))) -> dict:
        state = shop.release_fault(released_by=caller)  # 복구 신호에 누가 풀었는지 싣는다
        await outbox.flush(database)
        return _ok(state)

    # --- 총괄 읽기 중계: 관리자 화면(shop-web)이 shop-api만 부르게 한다. GET만 ----------
    @app.get("/api/shop/admin/orchestrator/incidents")
    async def orchestrator_incidents(_: str = Depends(_caller(ORCHESTRATOR_VIEWERS))) -> dict:
        data, meta = await orchestrator_relay.incidents()
        return _ok(data, **meta)

    @app.get("/api/shop/admin/orchestrator/incidents/{context_id}")
    async def orchestrator_incident(
        context_id: str, _: str = Depends(_caller(ORCHESTRATOR_VIEWERS))
    ) -> dict:
        data, meta = await orchestrator_relay.incident(context_id)
        return _ok(data, **meta)

    @app.get("/api/shop/admin/orchestrator/departments")
    async def orchestrator_departments(_: str = Depends(_caller(ORCHESTRATOR_VIEWERS))) -> dict:
        data, meta = await orchestrator_relay.departments()
        return _ok(data, **meta)

    # --- 마케팅 받은 기획안 읽기 중계(A2A 읽기 skill). 관리자와 읽기 전용 열람자만 ----------
    @app.get("/api/shop/admin/marketing/briefs")
    async def marketing_briefs(
        limit: int = 50, _: str = Depends(_caller(ORCHESTRATOR_VIEWERS))
    ) -> dict:
        return _ok(await marketing_client.list_briefs(limit))

    @app.get("/api/shop/admin/marketing/briefs/{proposal_id}")
    async def marketing_brief(
        proposal_id: str, _: str = Depends(_caller(ORCHESTRATOR_VIEWERS))
    ) -> dict:
        return _ok(await marketing_client.get_brief(proposal_id))

    # --- 공지 배너(SHOP-08): 고객 화면이 읽고, 마케팅 관문 토큰만 게시·해제한다 ---------
    @app.get("/api/shop/banners")
    async def list_banners() -> dict:
        return _ok(promotions.active_banners())

    @app.post("/api/shop/banners", status_code=201)
    async def post_banner(body: BannerIn, _: str = Depends(_caller(MARKETING_ONLY))) -> dict:
        return _ok(
            promotions.post_banner(
                body.notice_id, body.text, body.valid_from, body.valid_to, body.context_id
            )
        )

    @app.delete("/api/shop/banners/{notice_id}")
    async def end_banner(notice_id: str, _: str = Depends(_caller(MARKETING_ONLY))) -> dict:
        return _ok(promotions.end_banner(notice_id))

    # --- 쿠폰(SHOP-10): 발급은 마케팅 관문 토큰만, `idempotency_key` 필수 ---------------
    @app.post("/api/shop/coupons", status_code=201)
    async def issue_coupon(
        body: CouponIn, response: Response, _: str = Depends(_caller(MARKETING_ONLY))
    ) -> dict:
        coupon, replayed = promotions.issue_coupon(**body.model_dump())
        if replayed:
            response.status_code = 200  # 같은 키의 재요청은 이전 쿠폰을 그대로 돌려준다
        return _ok(coupon, replayed=replayed)

    @app.get("/api/shop/coupons")
    async def list_coupons(
        buyer_id: str | None = None,
        purpose_code: str | None = None,
        campaign_id: str | None = None,
        _: str = Depends(_caller(COUPON_READERS)),
    ) -> dict:
        return _ok(promotions.list_coupons(buyer_id, purpose_code, campaign_id))

    @app.get("/api/shop/coupons/{coupon_id}")
    async def get_coupon(coupon_id: str, _: str = Depends(_caller(COUPON_READERS))) -> dict:
        return _ok(promotions.get_coupon(coupon_id))

    # --- 클레임 접수·반품 입고·환불 (#117): 쇼핑몰 → 고객지원 → 재무 경로의 입구 -----------
    @app.post("/api/shop/claims", status_code=201)
    async def create_claim(body: ClaimIn) -> dict:
        order = shop.get_order(body.order_id)
        if order["buyer_id"] != body.buyer_id:
            raise ShopError("BUYER_MISMATCH", "주문의 구매자가 아니다", 403)
        items = [item.model_dump() for item in body.items] if body.items else None
        claim_digest = claim_inbox.digest(
            body.buyer_id,
            body.order_id,
            body.type,
            body.reason,
            items,
            body.unsealed,
            body.description,
        )
        # 고객지원을 부르지 않고 이 서버에 "접수됨"으로 저장한다.
        # AXcellent가 읽어 가 판정하고 결과를 적는다
        receipt = await asyncio.to_thread(
            claim_inbox.add,
            database,
            digest=claim_digest,
            context_id=f"{support_client.CLAIM_CONTEXT_PREFIX}{claim_digest[:12]}",
            buyer_id=body.buyer_id,
            customer_name=body.customer_name,
            order_id=body.order_id,
            type_=body.type,
            reason=body.reason,
            items=items,
            unsealed=body.unsealed,
            evidence_count=body.evidence_count,
            description=body.description,
        )
        return _ok(receipt, context_id=receipt["context_id"])

    # --- 내 클레임 목록·결과: 이 서버에 저장된 신청과 AXcellent가 적어 준 결과 (읽기) ----
    @app.get("/api/shop/claims")
    async def list_my_claims(buyer_id: str, order_id: str | None = None) -> dict:
        inquiries.buyer_number(buyer_id)
        if order_id is not None:
            inquiries.check_order_id(order_id)
        items = await asyncio.to_thread(claim_inbox.for_customer, database, buyer_id, order_id)
        return _ok(items, total=len(items))

    # --- 클레임함 읽기: AXcellent 고객지원이 새 신청을 읽어 가고 판정 결과를 적는다 ------
    @app.get("/api/shop/admin/claims")
    async def read_claim_inbox(
        state: Annotated[str, Query(pattern=r"^(new|open)$")] = "new",
        limit: Annotated[int, Query(ge=1, le=claim_inbox.READ_LIMIT_MAX)] = (
            claim_inbox.READ_LIMIT_DEFAULT
        ),
        after: Annotated[int, Query(ge=0)] = 0,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        if state == "open":  # 읽어 갔지만 아직 끝나지 않은 신청(상태를 다시 적을 수 있다)
            open_items, next_after = await asyncio.to_thread(
                claim_inbox.open_claims, database, after, limit
            )
            return _ok(open_items, next_after=next_after)
        items, total, failed = await asyncio.to_thread(claim_inbox.pending, database, limit)
        return _ok(items, total=total, failed=failed)

    @app.post("/api/shop/admin/claims/{seq}/failed")
    async def report_claim_failed(
        seq: Annotated[int, Path(ge=1)],
        body: IntakeFailedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(claim_inbox.report_failure, database, seq, body.reason)
        if done is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/claims/{seq}/result")
    async def write_claim_result(
        seq: Annotated[int, Path(ge=1)],
        body: ClaimResultIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        if len(json.dumps(body.result, ensure_ascii=False)) > claim_inbox.MAX_RESULT_CHARS:
            raise ShopError("RESULT_TOO_LARGE", "결과가 너무 크다", 413)
        try:
            done = await asyncio.to_thread(
                claim_inbox.write_result,
                database,
                seq,
                body.support_claim_id,
                body.status,
                body.result,
            )
        except ValueError as error:
            raise ShopError(
                "CLAIM_MISMATCH", "이 신청에는 다른 클레임 번호가 적혀 있다", 409
            ) from error
        if done is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임함에 없는 접수 번호다", 404)
        return _ok(done)

    # --- 고객 문의 접수 (#385 B안): 쇼핑몰 → 고객지원 `create_inquiry` ---------------------
    @app.post("/api/shop/inquiries", status_code=201)
    async def create_inquiry(body: InquiryIn) -> dict:
        inquiries.buyer_number(body.buyer_id)  # 형식 확인(저장은 구매자 ID 그대로)
        inquiries.check_order_id(body.order_id)
        subject, message = inquiries.clean_text(body.subject, body.body, body.order_id)
        photos = inquiries.clean_images([i.model_dump() for i in body.images])
        order = shop.get_order(body.order_id)  # 읽기만 한다. 없으면 404
        if order["buyer_id"] != body.buyer_id:  # 남의 주문도 같은 404로 답한다
            raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
        inquiry_limiter.hit(body.buyer_id)
        digest = inquiries.digest(
            body.buyer_id, body.order_id, subject, message, images_key=inquiries.images_key(photos)
        )
        context_id = (
            f"{support_client.CLAIM_CONTEXT_PREFIX}{digest[:12]}"  # 같은 접수는 같은 사건·키
        )
        # 고객지원을 부르지 않고 이 서버에 저장한다. AXcellent가 정해진 간격으로 읽어 간다
        saved = await asyncio.to_thread(
            inbox.add,
            database,
            digest=digest,
            context_id=context_id,
            buyer_id=body.buyer_id,
            customer_name=storefront.get_user(body.buyer_id)["display_name"],
            order_id=body.order_id,
            subject=subject,
            message=message,
            images=photos or None,
        )
        # 고객에게는 접수 사실만 돌려준다. 문의 번호는 이 서버의 접수 번호다
        receipt = {
            "id": saved["seq"],
            "status": "open",
            "order_id": saved["order_id"],
            "updated_at": saved["created_at"],
        }
        return _ok(receipt, context_id=saved["context_id"])

    # --- 문의함 읽기: AXcellent 고객지원이 새 문의를 읽어 가고 읽었다고 알린다 ------------------
    @app.get("/api/shop/admin/inquiries")
    async def read_inquiry_inbox(
        limit: Annotated[int, Query(ge=1, le=inbox.READ_LIMIT_MAX)] = inbox.READ_LIMIT_DEFAULT,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        items, total, failed = await asyncio.to_thread(inbox.pending, database, limit)
        return _ok(items, total=total, failed=failed)

    @app.post("/api/shop/admin/inquiries/{seq}/failed")
    async def report_inquiry_failed(
        seq: Annotated[int, Path(ge=1)],
        body: IntakeFailedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(inbox.report_failure, database, seq, body.reason)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.get("/api/shop/admin/intake/stuck")
    async def list_stuck_intake(_: str = Depends(_caller(INQUIRY_READERS))) -> dict:
        """실패로 빼 둔 문의·클레임 접수(번호·사유·횟수만). 사람이 확인하고 다시 시도한다."""
        a = await asyncio.to_thread(inbox.stuck, database)
        b = await asyncio.to_thread(claim_inbox.stuck, database)
        items = sorted(a + b, key=lambda i: (i["failed_at"] or "", i["seq"]))
        return _ok(items, total=len(items))

    @app.post("/api/shop/admin/inquiries/{seq}/retry")
    async def retry_inquiry(
        seq: Annotated[int, Path(ge=1)], _: str = Depends(_caller(INQUIRY_READERS))
    ) -> dict:
        done = await asyncio.to_thread(inbox.retry, database, seq)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/claims/{seq}/retry")
    async def retry_claim(
        seq: Annotated[int, Path(ge=1)], _: str = Depends(_caller(INQUIRY_READERS))
    ) -> dict:
        done = await asyncio.to_thread(claim_inbox.retry, database, seq)
        if done is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/inquiries/{seq}/synced")
    async def mark_inquiry_synced(
        seq: Annotated[int, Path(ge=1)],
        body: InquirySyncedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(inbox.mark_synced, database, seq, body.support_inquiry_id)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    # --- 내 문의 목록·상태: 쇼핑몰 → 고객지원 `list_inquiries`·`get_inquiry` (읽기) --------
    # 고객지원이 `user_id`로 본인 문의만 골라 고객용으로 줄여 주고, 여기서 한 번 더 좁힌다.
    @app.get("/api/shop/inquiries")
    async def list_my_inquiries(
        buyer_id: str, limit: Annotated[int, Query(ge=1, le=100)] = 50
    ) -> dict:
        user_id = inquiries.buyer_number(buyer_id)
        # 고객이 받은 번호는 이 서버의 접수 번호다. 고객지원이 아직 읽어 가지 않은 문의도 보여 준다
        own = await asyncio.to_thread(inbox.for_buyer, database, buyer_id)
        found: dict[int, dict] = {}
        if any(o["synced"] for o in own):
            out = await support_client.call(
                "list_inquiries", support_client.new_context_id(), {"user_id": user_id}
            )
            found = {i["id"]: i for i in out.get("items") or [] if isinstance(i.get("id"), int)}
        items = [inquiries.own_view(o, found.get(o["support_inquiry_id"])) for o in own]
        return _ok(items[:limit], total=len(items))

    @app.get("/api/shop/inquiries/{inquiry_id}")
    async def get_my_inquiry(inquiry_id: Annotated[int, Path(ge=1)], buyer_id: str) -> dict:
        user_id = inquiries.buyer_number(buyer_id)
        # 번호는 이 서버의 접수 번호다. 본인 접수가 아니면 없는 번호와 똑같이 404다
        own = await asyncio.to_thread(inbox.get_for_buyer, database, inquiry_id, buyer_id)
        if own is None:
            raise ShopError("NOT_FOUND", "문의를 찾을 수 없다", 404)
        if not own["synced"]:  # 고객지원이 아직 읽어 가지 않았다: 접수한 내용만 보여 준다
            return _ok(inquiries.own_view(own, None, with_messages=True))
        out = await support_client.call(
            "get_inquiry",
            support_client.new_context_id(),
            {"inquiry_id": own["support_inquiry_id"], "user_id": user_id},
        )
        if "id" not in out:  # 문의가 아닌 응답은 성공으로 내보내지 않는다
            raise ShopError("UPSTREAM_ERROR", "고객지원 응답이 문의 형식이 아니다", 502)
        return _ok(inquiries.own_view(own, out))

    # --- 고객 아이디어(의견): 쇼핑몰 → 고객지원 `submit_idea`. 문의·불만과 따로 쌓는다 ----------
    @app.post("/api/shop/ideas", status_code=201)
    async def submit_idea(body: IdeaIn) -> dict:
        user_id = inquiries.buyer_number(body.buyer_id)
        idea_limiter.hit(body.buyer_id)
        text = " ".join(body.text.split())
        digest = hashlib.sha1(f"{body.kind}|{text}".encode()).hexdigest()[:12]
        context_id = support_client.new_context_id()
        done = await support_client.call(
            "submit_idea",
            context_id,
            {"kind": body.kind, "user_id": user_id},
            {"text": text},
            target=f"user:{user_id}:{digest}",
        )
        if "idea_id" not in done:  # 아이디어 접수 결과가 아닌 응답은 성공으로 내보내지 않는다
            raise ShopError("UPSTREAM_ERROR", "고객지원 응답이 아이디어 형식이 아니다", 502)
        # 고객에게는 접수 사실만 돌려준다 (영역·묶음 같은 내부 분류는 보이지 않는다)
        return _ok({"idea_id": done["idea_id"], "received": True}, context_id=context_id)

    # --- 해결 결과 평가: 쇼핑몰 → 고객지원 `submit_feedback` -----------------------------
    @app.post("/api/shop/inquiries/{inquiry_id}/feedback", status_code=201)
    async def submit_inquiry_feedback(
        inquiry_id: Annotated[int, Path(ge=1)], body: InquiryFeedbackIn
    ) -> dict:
        user_id = inquiries.buyer_number(body.buyer_id)
        feedback_limiter.hit(body.buyer_id)
        context_id = support_client.new_context_id()
        # 번호는 이 서버의 접수 번호다. 본인 접수가 아니면 없는 번호와 똑같이 404다
        own = await asyncio.to_thread(inbox.get_for_buyer, database, inquiry_id, body.buyer_id)
        if own is None:
            raise ShopError("NOT_FOUND", "문의를 찾을 수 없다", 404)
        if not own["synced"]:  # 고객지원이 읽어 가기 전에는 평가할 수 없다(해결된 문의도 아니다)
            raise ShopError("INVALID_STATE", "아직 고객지원이 확인하기 전인 문의다", 409)
        support_id = own["support_inquiry_id"]
        done = await support_client.call(
            "submit_feedback",
            context_id,
            {
                "inquiry_id": support_id,
                "rating": body.rating,
                "resolved": body.resolved,
                "user_id": user_id,
            },
            {"comment": body.comment},
            target=f"inquiry:{support_id}",
        )
        if done.get("rated") is not True:  # 먼저 들어온 요청이 이미 남겼다
            raise ShopError("INVALID_STATE", "이미 평가를 남긴 문의다", 409)
        # 고객에게는 접수 사실만 돌려준다(공개·숨김 같은 내부 판단은 보이지 않는다)
        receipt = {
            "inquiry_id": inquiry_id,
            "rated": done.get("rated"),
            "rating": done.get("rating"),
        }
        return _ok(receipt, context_id=context_id)

    @app.post("/api/shop/admin/claims/{claim_id}/return-received")
    async def return_received(
        claim_id: int, body: ReturnReceivedIn, _: str = Depends(_caller(INJECT_ALLOWED))
    ) -> dict:
        done = refunds.get_return(claim_id)
        if done is not None:  # 이미 입고 처리했다: 고객지원도 이벤트도 다시 부르지 않는다
            return _ok(done, replayed=True)
        shop.get_order(body.order_id)
        context_id = support_client.new_context_id()
        claim = await support_client.call(
            "receive_return",
            context_id,
            {"claim_id": claim_id, "inspection": body.inspection},
            target=str(claim_id),
        )
        if str(claim.get("order_id")) != body.order_id:
            raise ShopError("ORDER_MISMATCH", "클레임의 주문과 다르다", 409)
        recorded, _replayed = refunds.record_return(
            claim_id, body.order_id, body.inspection, context_id
        )
        await outbox.flush(database)
        return _ok(recorded | {"claim": claim})

    @app.post("/api/shop/payments/{payment_id}/refund", status_code=201)
    async def refund_payment(
        payment_id: str,
        body: RefundIn,
        response: Response,
        _: str = Depends(_caller(REFUND_ALLOWED)),
    ) -> dict:
        refund, replayed = refunds.refund(
            payment_id, body.refund_amount, body.idempotency_key, body.context_id
        )
        await outbox.flush(database)
        if replayed:
            response.status_code = 200  # 같은 키의 재요청은 이전 환불을 그대로 돌려준다
        return _ok(refund, replayed=replayed)

    # --- 데이터 조회(contracts/data-access): data 에이전트가 읽는 읽기 전용 API -----------
    # 아래 두 조회는 동기 핸들러: FastAPI가 스레드에서 돌려 DB를 읽는 동안 이벤트 루프를 막지 않는다
    # (주문 목록과 같은 이유, #959)
    @app.get("/api/shop/failed-orders")
    def failed_orders(
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        category: str | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        _: str = Depends(_caller(DATA_READERS)),
    ) -> dict:
        query = _query(ShopFailedOrdersQuery, from_=from_, to=to, category=category, limit=limit)
        result = lookups.failed_orders(query)
        return _ok(result.model_dump(mode="json"))

    @app.get("/api/shop/metric-series")
    def metric_series(
        metric: str,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        bucket: str = "5m",
        _: str = Depends(_caller(DATA_READERS)),
    ) -> dict:
        query = _query(ShopMetricSeriesQuery, metric=metric, from_=from_, to=to, bucket=bucket)
        result = lookups.metric_series(query)
        return _ok(result.model_dump(mode="json"))

    @app.get("/api/shop/sellers/{seller_id}/profile")
    async def seller_profile(seller_id: str, _: str = Depends(_caller(DATA_READERS))) -> dict:
        query = _query(ShopSellerProfileQuery, seller_id=seller_id)
        return _ok(lookups.seller_profile(query).model_dump(mode="json"))

    @app.get("/api/shop/sellers/{seller_id}/prices")
    async def seller_prices(seller_id: str, _: str = Depends(_caller(DATA_READERS))) -> dict:
        query = _query(ShopSellerPricesQuery, seller_id=seller_id)
        return _ok(lookups.seller_prices(query).model_dump(mode="json"))

    return app


app = create_app()

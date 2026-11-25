"""새 회사(변환기 사이트) 재무 화면 (11/25 실서버: 누비투어 재무 overview·incidents 500).

변환기(`site_adapter`)가 만든 주문의 결제에는 결제 번호(`payment_id`)가 없다. PG 정산 계산이
`p["payment_id"]`에서 KeyError를 내 overview·incidents가 500, closing_report가 handler_error였다.
결제 번호가 없으면 주문 번호·순번으로 정한다. 또 같은 출처 nginx가 읽기 Authorization을
공용 토큰으로 바꿔도 `X-AX-Session`의 세션 회사로 답한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope, human_auth
from finance_agent import books as bk
from finance_agent import closing, finance_api, handlers

NUBI = "c-cf2f0d"
PAID_AT = "2026-11-24T03:00:00+09:00"


def _order(n: int, status: str, paid: int, refunded: int = 0) -> dict:
    """변환기 `resources.to_order` 모양(결제 번호 없음)."""
    return {
        "order_id": f"NB{n}", "status": status, "created_at": PAID_AT, "updated_at": PAID_AT,
        "buyer_id": f"m{n}", "total_amount": 1_290_000, "refunded_amount": refunded,
        "items": [{"product_id": "T-JEJU-3D", "qty": 2, "unit_price": 645_000,
                   "paid_amount": paid}],
        "payments": (
            [{"amount": paid, "status": "succeeded", "created_at": PAID_AT}] if paid else []
        ),
    }  # fmt: skip


ORDERS = [_order(1, "paid", 1_290_000), _order(2, "cancelled", 0),
          _order(3, "completed", 1_290_000, 645_000), _order(4, "confirmed", 300_000)]  # fmt: skip


def _adapter(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/api/shop/orders"):
        return httpx.Response(200, json={"data": ORDERS, "meta": {"next_cursor": None}})
    if path.endswith("/api/shop/products"):
        return httpx.Response(200, json={"data": [{"product_id": "T-JEJU-3D", "price": 645_000}]})
    if path.endswith("/api/safety/executions"):
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404, json={"error": {"code": "NOT_MAPPED", "message": "-"}})


def _books() -> bk.Books:
    async def run() -> bk.Books:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_adapter)) as client:
            with company_scope.using(NUBI):
                return await bk.live_books(http_client=client)

    return asyncio.run(run())


def test_adapter_payments_get_an_id_and_books_compute() -> None:
    books = _books()
    pays = books.payments()
    assert pays and all(p["payment_id"] for p in pays)
    assert len({p["payment_id"] for p in pays}) == len(pays)  # 주문마다 다른 번호
    assert bk.pg_settlements(books)  # KeyError 없이 계산된다


def test_new_company_overview_and_closing_do_not_fail() -> None:
    handlers.reset_state()
    books = _books()

    async def run() -> None:
        with company_scope.using(NUBI):

            async def loader() -> bk.Books:
                return books

            handlers.state().books_loader = loader
            data, _meta = await finance_api.overview_data()
            assert "kpis" in data
            report = await closing.closing_report(books, "day", dt.date(2026, 11, 24), None, {})
            assert report["period"] == "day"

    asyncio.run(run())


def test_public_token_with_session_header_reads_session_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nginx가 Authorization을 공용 토큰으로 바꿔도 X-AX-Session의 회사 장부로 답한다."""
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    handlers.reset_state()
    seen: list[str | None] = []
    books = _books()

    async def loader() -> bk.Books:
        seen.append(company_scope.active())
        return books

    handlers.state().books_loader = loader  # 캣북스(기본) 기록
    tok, _ = human_auth.issue_session(account_id="acc_n", name="누비", role="admin",
                                      company_id=NUBI, company_role="admin")  # fmt: skip
    from finance_agent.main import app

    client = TestClient(app)
    headers = {"Authorization": "Bearer human:public", "X-AX-Session": tok}
    r = client.get("/api/finance/overview", headers=headers)
    assert r.status_code == 200
    assert NUBI in seen  # 세션 회사로 장부를 읽었다(캣북스가 아님)

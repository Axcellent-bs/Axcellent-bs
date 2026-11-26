"""사이트 변환기 알아서 연결 (`POST /propose` + `auto`, 11/25).

주소·키만 넣으면 목록 경로를 고르고, 규칙으로 못 채운 칸은 회사 AI가 채우고, 빈 칸이 없으면
첫 쪽을 바꿔 보고 바로 저장한다. AI는 가짜 LLM 서버(httpx.MockTransport)로 흉내 낸다.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template.llm import LLMClient, LLMSettings
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import ai_propose
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
BASE = "/api/dashboard/site-adapter"
KEY = "site-secret-key"
NUBI_OPENAPI = {
    "paths": {
        "/api/v1/tours": {"get": {}},
        "/api/v1/bookings": {"get": {}},
        "/api/v1/login": {"post": {}},
    }
}
# 칸 이름이 규칙(동의어)에 없는 사이트
ODD_OPENAPI = {"paths": {"/api/v2/reservas": {"get": {}}, "/api/v2/paquetes": {"get": {}}}}
ODD_ORDER_RENAME = {
    "code": "nro_reserva",
    "status": "situacion",
    "product_code": "paquete",
    "adults": "adultos",
    "children": "ninos",
    "total_amount": "importe_total",
    "paid_amount": "importe_pagado",
    "member_id": "socio",
    "created_at": "alta",
    "updated_at": "modificado",
}
ODD_PRODUCT_RENAME = {
    "code": "ref",
    "name": "titulo",
    "base_price": "tarifa",
    "region": "zona",
    "on_sale": "vendible",
}
ODD_STATUS = {"deposit_paid": "pagado", "cancelled": "cancelada"}

AI_PATHS = {"orders_path": "/api/v2/reservas", "products_path": "/api/v2/paquetes", "why": "이름"}
AI_ORDERS = {
    "fields": [
        {"name": "order_id", "expr": "nro_reserva", "why": "예약 번호"},
        {"name": "status", "expr": "situacion", "why": "상태"},
        {"name": "created_at", "expr": "alta", "why": "만든 시각"},
        {"name": "buyer_id", "expr": "socio", "why": "회원"},
        {"name": "product_id", "expr": "paquete", "why": "상품"},
        {"name": "qty", "expr": "sum:adultos,ninos", "why": "인원"},
        {"name": "total_amount", "expr": "importe_total", "why": "총액"},
        {"name": "paid_amount", "expr": "importe_pagado", "why": "낸 금액"},
    ],
    "statuses": [{"value": "pagado", "to": "paid"}],
    "unplaced": [{"path": "depart_date", "meaning": "출발일"}],
}
AI_PRODUCTS = {
    "fields": [
        {"name": "product_id", "expr": "ref", "why": "번호"},
        {"name": "name", "expr": "titulo", "why": "이름"},
        {"name": "price", "expr": "tarifa", "why": "가격"},
        {"name": "category_code", "expr": "zona", "why": "지역"},
        {"name": "active", "expr": "vendible", "why": "판매 중"},
    ]
}


def _rename(rows: list[dict[str, Any]], table: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        new = {table.get(k, k): v for k, v in row.items() if k != "id"}
        out.append(new)
    return out


class FakeLlm:
    """OpenAI 호환 LLM 흉내. 스키마 이름과 자원으로 답을 고른다. 받은 요청을 남긴다."""

    def __init__(self, orders: dict[str, Any] = AI_ORDERS) -> None:
        self.requests: list[dict[str, Any]] = []
        self.orders = orders

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        name = body["response_format"]["json_schema"]["name"]
        text = json.dumps(body["messages"], ensure_ascii=False)
        if name == "PathPick":
            answer: dict[str, Any] = AI_PATHS
        elif '\\"resource\\": \\"orders\\"' in text or '"resource": "orders"' in text:
            answer = self.orders
        else:
            answer = AI_PRODUCTS
        content = json.dumps(answer, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLlm:
    fake = FakeLlm()
    settings = LLMSettings(model="test-model", base_url="http://llm.test/v1", api_key="k")
    monkeypatch.setattr(
        ai_propose,
        "make_client",
        lambda: LLMClient(ai_propose.DEPT, settings, transport=httpx.MockTransport(fake.handle)),
    )
    return fake


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pages: dict[str, Any]) -> TestClient:
    monkeypatch.setenv("AX_SITE_ADAPTER_DIR", str(tmp_path / "site_adapter"))
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.delenv(up.TOKEN_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)

    def handle(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "nope"})
        return httpx.Response(200, json=body)

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(handle))
    return TestClient(app)


@pytest.fixture
def odd_pages(nubi: Any) -> dict[str, Any]:
    bookings = _rename(nubi("nubi_bookings_p1.json")["data"], ODD_ORDER_RENAME)
    for row in bookings:
        row["situacion"] = ODD_STATUS.get(row["situacion"], row["situacion"])
    tours = _rename(nubi("nubi_tours.json")["data"], ODD_PRODUCT_RENAME)
    return {
        "/openapi.json": ODD_OPENAPI,
        "/api/v2/reservas": {"data": bookings, "next_cursor": None},
        "/api/v2/paquetes": {"data": tours, "next_cursor": None},
    }


def auto(client: TestClient) -> Any:
    return client.post(BASE + "/propose", json={"base_url": SITE, "token": KEY, "auto": True})


def test_rule_site_connects_without_calling_ai(tmp_path, monkeypatch, nubi) -> None:
    """규칙으로 다 차는 사이트(누비투어)는 AI 없이 바로 연결된다(회사 AI가 없어도)."""

    def no_ai() -> LLMClient:
        raise AssertionError("AI를 부르면 안 된다")

    monkeypatch.setattr(ai_propose, "make_client", no_ai)
    pages = {
        "/openapi.json": NUBI_OPENAPI,
        "/api/v1/tours": nubi("nubi_tours.json"),
        "/api/v1/bookings": nubi("nubi_bookings_p1.json"),
    }
    # 누비투어 상태 낱말은 규칙으로 다 정해진다
    client = _client(tmp_path, monkeypatch, pages)
    res = auto(client)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["saved"] is True, data.get("reason")
    assert data["configured"] is True and data["counts"] == {"orders": 2, "products": 2}
    assert data["mapping"]["resources"]["orders"]["path"] == "/api/v1/bookings"
    assert KEY not in json.dumps(res.json())


def test_odd_site_is_connected_by_ai(tmp_path, monkeypatch, odd_pages, llm) -> None:
    client = _client(tmp_path, monkeypatch, odd_pages)
    res = auto(client)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["saved"] is True, data.get("reason")
    orders = data["mapping"]["resources"]["orders"]
    assert orders["path"] == "/api/v2/reservas"
    assert orders["fields"]["qty"] == "sum:adultos,ninos"
    assert orders["values"]["status"] == {"pagado": "paid", "cancelada": "cancelled"}
    assert data["ai"]["orders"]["filled"]["order_id"]["expr"] == "nro_reserva"
    assert data["ai"]["orders"]["statuses"] == {"pagado": "paid"}
    assert data["ai"]["orders"]["unplaced"] == [{"path": "depart_date", "meaning": "출발일"}]
    assert data["counts"] == {"orders": 2, "products": 2}
    # 저장한 짝으로 변환기 읽기가 된다
    up.clear_cache()
    assert client.get("/site-adapter/api/shop/products").status_code == 200


def test_ai_gets_field_names_not_customer_values(
    tmp_path, monkeypatch, odd_pages, llm, nubi
) -> None:
    client = _client(tmp_path, monkeypatch, odd_pages)
    assert auto(client).status_code == 200
    sent = json.dumps(llm.requests, ensure_ascii=False)
    row = nubi("nubi_bookings_p1.json")["data"][0]
    assert row["contact"]["phone"] not in sent and str(row["total_amount"]) not in sent
    assert "NB-20261110-0001" not in sent  # 예약 번호 값도 보내지 않는다
    assert "nro_reserva" in sent and "contact.phone" in sent  # 칸 경로만
    assert KEY not in sent


def test_bad_ai_answers_are_dropped_and_nothing_is_saved(
    tmp_path, monkeypatch, odd_pages, llm
) -> None:
    llm.orders = {
        "fields": [
            {"name": "order_id", "expr": "const:A-1", "why": "지어냄"},
            {"name": "status", "expr": "no_such_field", "why": "없는 칸"},
            {"name": "qty", "expr": "sum:adultos,alta", "why": "글자 칸 더하기"},
        ],
        "statuses": [{"value": "pagado", "to": "shipped"}],
        "unplaced": [{"path": "made_up", "meaning": "없는 칸"}],
    }
    client = _client(tmp_path, monkeypatch, odd_pages)
    res = auto(client)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["saved"] is False
    assert "orders.order_id" in data["reason"] and "orders.status" in data["reason"]
    assert data["ai"]["orders"]["filled"] == {} and data["ai"]["orders"]["unplaced"] == []
    state = client.get(BASE).json()["data"]
    assert state["configured"] is False  # 아무것도 저장하지 않았다


def test_odd_site_without_company_ai_says_where_to_put_the_key(
    tmp_path, monkeypatch, odd_pages
) -> None:
    monkeypatch.setattr(
        ai_propose, "make_client", lambda: LLMClient(ai_propose.DEPT, LLMSettings())
    )
    client = _client(tmp_path, monkeypatch, odd_pages)
    res = auto(client)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AI_NOT_CONFIGURED"
    assert "공통 AI 도구 연결" in res.json()["error"]["message"]


def test_rule_paths_pick_list_paths_by_name() -> None:
    found = ai_propose.rule_paths(["/api/v1/login", "/api/v1/bookings", "/api/v1/tours"])
    assert found == {"orders": "/api/v1/bookings", "products": "/api/v1/tours"}
    assert ai_propose.rule_paths(["/api/v2/reservas"]) == {"orders": "", "products": ""}


def test_shape_keeps_only_kinds() -> None:
    rows = [{"a": "x", "b": 3, "c": {"d": True, "e": None}, "f": [1]}]
    assert ai_propose.shape(rows) == {
        "a": "글자",
        "b": "숫자",
        "c": "묶음",
        "c.d": "참거짓",
        "c.e": "빈 값",
        "f": "목록",
    }


# ---------------------------------------------------------------- 문의 받은함 (11/26)

NUBI_WITH_INQUIRIES = {"paths": {**NUBI_OPENAPI["paths"], "/api/v1/inquiries": {"get": {}}}}


def _nubi_pages(nubi: Any, inquiries: Any) -> dict[str, Any]:
    return {
        "/openapi.json": NUBI_WITH_INQUIRIES,
        "/api/v1/tours": nubi("nubi_tours.json"),
        "/api/v1/bookings": nubi("nubi_bookings_p1.json"),
        "/api/v1/inquiries": inquiries,
    }


def _no_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_ai() -> LLMClient:
        raise AssertionError("AI를 부르면 안 된다")

    monkeypatch.setattr(ai_propose, "make_client", no_ai)


def test_auto_connect_also_maps_the_inquiry_inbox(tmp_path, monkeypatch, nubi) -> None:
    """누비투어: 알아서 연결이 문의 받은함 짝도 규칙으로 넣는다(AI 없이)."""
    _no_ai(monkeypatch)
    client = _client(tmp_path, monkeypatch, _nubi_pages(nubi, nubi("nubi_inquiries.json")))
    data = auto(client).json()["data"]
    assert data["saved"] is True, data.get("reason")
    report = data["ai"]["inquiries"]
    assert report["saved"] is True and report["path"] == "/api/v1/inquiries"
    assert report["fields"] == {
        "inquiry_id": "id",
        "subject": "title",
        "message": "body",
        "status": "status",
        "order_id": "booking_code",
        "customer_name": "name",
        "created_at": "created_at",
        # 문의에 회원 id가 없어 예약의 회원 칸을 읽는다
        "buyer_id": "order.member_id",
    }
    saved = client.get(BASE + "/writes").json()["data"]["inboxes"]["inquiries"]
    assert saved["path"] == "/api/v1/inquiries"
    assert saved["values"]["status"] == {"open": "open", "answered": "answered"}


def test_unknown_inquiry_status_skips_the_inbox_but_still_connects(
    tmp_path, monkeypatch, nubi
) -> None:
    """문의 상태 낱말을 모르면 문의 짝은 넣지 않는다(답한 문의를 다시 읽지 않게).

    주문·상품은 그대로 연결한다.
    """
    _no_ai(monkeypatch)
    rows = nubi("nubi_inquiries.json")
    rows["data"][0]["status"] = "hold"
    client = _client(tmp_path, monkeypatch, _nubi_pages(nubi, rows))
    data = auto(client).json()["data"]
    assert data["saved"] is True, data.get("reason")
    assert data["ai"]["inquiries"]["saved"] is False
    assert "hold" in data["ai"]["inquiries"]["reason"]
    assert client.get(BASE + "/writes").json()["data"]["inboxes"]["inquiries"] is None


def test_inquiry_statuses_rule() -> None:
    from ax_dashboard_api.site_adapter import propose

    words = ["open", "answered", "unanswered", "new", "closed", "hold"]
    assert propose.inquiry_statuses(words) == {
        "open": "open",
        "answered": "answered",
        "unanswered": "open",
        "new": "open",
        "closed": "answered",
        "hold": "",
    }

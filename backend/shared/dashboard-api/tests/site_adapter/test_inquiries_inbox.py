"""사이트 변환기 문의 받은함 (`GET /admin/inquiries`, 11/25).

누비투어 근거 (`Catbooks-demo/nubi-tour` dev): `GET /api/v1/inquiries` (`inquiryV1`, 커서 쪽 넘김)
→ `{id, booking_code, member, name, email, category, title, body, status(open|answered), ...}`.
회원 id는 내보내지 않으므로 구매자는 예약(`order.member_id`)에서 꺼낸다.
예약이 없으면 문의마다 손님 번호.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import inquiries
from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
INBOX = "/site-adapter/api/shop/admin/inquiries"
SUPPORT = {"authorization": "Bearer sup-tok"}
FIXTURES = Path(__file__).parent / "fixtures"

INQUIRIES_MAP = {
    "path": "/api/v1/inquiries",
    "paging": {
        "items_field": "data",
        "next_field": "next_cursor",
        "cursor_param": "cursor",
        "limit_param": "limit",
        "limit": 200,
    },
    "fields": {
        "inquiry_id": "id",
        "subject": "title",
        "message": "body",
        "status": "status",
        "order_id": "booking_code",
        "buyer_id": "order.member_id",
        "customer_name": "name",
        "created_at": "created_at",
    },
    "values": {"status": {"open": "open", "answered": "answered"}},
}


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeSite:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.pages = {
            "/api/v1/inquiries": load("nubi_inquiries.json"),
            "/api/v1/bookings": {
                "data": load("nubi_bookings_p1.json")["data"]
                + load("nubi_bookings_p2.json")["data"],
                "next_cursor": None,
            },
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return httpx.Response(200, json=self.pages[request.url.path])


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    fake = FakeSite()
    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_dir: Path, inbox: bool):
    path = tmp_path / "company_connection.json"
    path.write_text(
        json.dumps({"shop_api_url": SITE, "kind": "adapter", "adapter": {"base_url": SITE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv("AX_AGENT_TOKENS", "support=sup-tok,dashboard-api=dash")
    monkeypatch.setenv("AX_SITE_API_TOKEN", "site-secret-token-9")
    if inbox:
        raw = json.loads((adapter_dir / "mapping.json").read_text(encoding="utf-8"))
        raw["inboxes"] = {"inquiries": INQUIRIES_MAP}
        (adapter_dir / "mapping.json").write_text(json.dumps(raw), encoding="utf-8")
    return TestClient(app)


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_dir: Path, site: FakeSite
) -> Iterator[TestClient]:
    yield _client(tmp_path, monkeypatch, adapter_dir, inbox=True)


def inbox(client: TestClient) -> dict[str, Any]:
    resp = client.get(INBOX, params={"state": "new", "limit": 20}, headers=SUPPORT)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_mapping_accepts_inquiries_inbox() -> None:
    found = m.SiteMapping.model_validate(
        {**load("nubi_mapping.json"), "inboxes": {"inquiries": INQUIRIES_MAP}}
    )
    assert found.inboxes["inquiries"].path == "/api/v1/inquiries"


def test_open_inquiries_become_support_inbox_rows(client: TestClient) -> None:
    body = inbox(client)
    # 답 전(open)만, 번호 순. 답한 iq_03은 빠진다
    assert [r["key"] for r in body["data"]] == ["iq_01", "iq_02", "iq_04"]
    assert body["meta"]["total"] == 3 and body["meta"]["failed"] == 0
    first = body["data"][0]
    assert first == {
        "seq": 1,
        "key": "iq_01",
        "context_id": "ctx_site_inquiry_1",
        "buyer_id": "buy_0001",  # 고객지원은 buy_<숫자>만 받는다
        "customer_name": "김*진",
        "order_id": "NB-20261110-0001",
        "subject": "출발 시간 문의",
        "message": "공항에 몇 시까지 가면 되나요?",
        "created_at": "2026-11-24T01:00:00.000Z",
    }
    rows = {r["key"]: r for r in body["data"]}
    # 같은 예약(같은 회원)의 문의는 같은 구매자 번호, 예약 없는 손님 문의는 따로
    assert rows["iq_04"]["buyer_id"] == "buy_0001"
    assert rows["iq_02"]["buyer_id"] != "buy_0001" and rows["iq_02"]["order_id"] is None


def test_numbers_stay_the_same_across_reads(client: TestClient) -> None:
    first = {r["key"]: (r["seq"], r["buyer_id"]) for r in inbox(client)["data"]}
    up.clear_cache()
    again = {r["key"]: (r["seq"], r["buyer_id"]) for r in inbox(client)["data"]}
    assert first == again


def test_synced_inquiry_leaves_the_inbox(client: TestClient) -> None:
    resp = client.post(f"{INBOX}/1/synced", json={"support_inquiry_id": 41}, headers=SUPPORT)
    assert resp.status_code == 200, resp.text
    assert [r["key"] for r in inbox(client)["data"]] == ["iq_02", "iq_04"]
    assert inquiries.book()["1"]["support_inquiry_id"] == 41


def test_failed_three_times_moves_out_then_retry_brings_back(client: TestClient) -> None:
    for _ in range(inquiries.MAX_FAILS):
        client.post(f"{INBOX}/2/failed", json={"reason": "BAD"}, headers=SUPPORT)
    body = inbox(client)
    assert [r["key"] for r in body["data"]] == ["iq_01", "iq_04"]
    assert body["meta"]["failed"] == 1
    client.post(f"{INBOX}/2/retry", json={}, headers=SUPPORT)
    assert [r["key"] for r in inbox(client)["data"]] == ["iq_01", "iq_02", "iq_04"]


def test_state_file_keeps_no_customer_text(client: TestClient, adapter_dir: Path) -> None:
    client.post(f"{INBOX}/1/synced", json={"support_inquiry_id": 41}, headers=SUPPORT)
    client.post(f"{INBOX}/2/failed", json={"reason": "BAD"}, headers=SUPPORT)
    text = (adapter_dir / inquiries.STATE_FILE).read_text(encoding="utf-8")
    assert "공항" not in text and "김*진" not in text and "BAD" not in text


def test_open_views_are_empty_page(client: TestClient) -> None:
    resp = client.get(f"{INBOX}/open", params={"after": 0, "limit": 100}, headers=SUPPORT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


def test_needs_token(client: TestClient) -> None:
    assert client.get(INBOX, params={"state": "new"}).status_code in (401, 403)


def test_without_inbox_mapping_still_not_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_dir: Path, site: FakeSite
) -> None:
    c = _client(tmp_path, monkeypatch, adapter_dir, inbox=False)
    resp = c.get(INBOX, params={"state": "new"}, headers=SUPPORT)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_MAPPED"
    assert c.get(f"{INBOX}/open", headers=SUPPORT).status_code == 404

"""문의함: 판매 서버가 저장하고 AXcellent 고객지원만 읽어 간다 (읽기·읽음 표시·권한·보존)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SUPPORT = {"Authorization": "Bearer mock:support"}
ADMIN = {"Authorization": "Bearer mock:admin"}
PHOTO = {"ref": "img_1", "media_type": "image/jpeg", "data_base64": "QUJDREVGRw=="}
READ = "/api/shop/admin/inquiries"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_INQUIRY_RATE_PER_BUYER", "50")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _inquire(client: TestClient, body: str, buyer: str = "buy_0001", **extra) -> dict:  # noqa: ANN003
    order = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": "prd_0001", "qty": 1}]},
    ).json()["data"]["order_id"]
    done = client.post(
        "/api/shop/inquiries",
        json={"buyer_id": buyer, "order_id": order, "body": body, **extra},
    )
    assert done.status_code == 201
    return done.json()["data"]


def test_pending_inquiries_are_read_oldest_first_with_the_total(client: TestClient) -> None:
    for n in range(3):
        _inquire(client, f"문의 {n}")
    res = client.get(READ, params={"limit": 2}, headers=SUPPORT)
    assert res.status_code == 200
    body = res.json()
    assert [i["message"] for i in body["data"]] == ["문의 0", "문의 1"]
    assert body["meta"]["total"] == 3  # 읽어 간 것이 아니라 남은 전체 수
    assert body["meta"]["failed"] == 0
    item = body["data"][0]
    assert set(item) == {
        "seq", "key", "context_id", "buyer_id", "customer_name", "order_id",
        "subject", "message", "images", "attempts", "created_at",
    }  # fmt: skip
    assert item["key"] and item["attempts"] == 0  # 멱등 키로 쓰는 접수 지문
    assert item["buyer_id"] == "buy_0001" and item["context_id"].startswith("ctx_shop_")


def test_marking_synced_removes_it_from_the_pending_list(client: TestClient) -> None:
    first = _inquire(client, "첫 문의")["id"]
    _inquire(client, "둘째 문의")
    done = client.post(f"{READ}/{first}/synced", json={"support_inquiry_id": 41}, headers=SUPPORT)
    assert done.status_code == 200 and done.json()["data"]["support_inquiry_id"] == 41
    rest = client.get(READ, headers=SUPPORT).json()
    assert [i["message"] for i in rest["data"]] == ["둘째 문의"] and rest["meta"]["total"] == 1


def test_marking_synced_twice_keeps_the_first_result(client: TestClient) -> None:
    seq = _inquire(client, "문의")["id"]
    first = client.post(f"{READ}/{seq}/synced", json={"support_inquiry_id": 5}, headers=SUPPORT)
    again = client.post(f"{READ}/{seq}/synced", json={"support_inquiry_id": 9}, headers=SUPPORT)
    assert again.status_code == 200
    assert again.json()["data"] == first.json()["data"]


def test_an_unknown_number_is_a_404(client: TestClient) -> None:
    res = client.post(f"{READ}/99/synced", json={"support_inquiry_id": 1}, headers=SUPPORT)
    assert (res.status_code, res.json()["error"]["code"]) == (404, "INQUIRY_NOT_FOUND")


@pytest.mark.parametrize("body", [{}, {"support_inquiry_id": 0}, {"support_inquiry_id": 1, "x": 1}])
def test_a_bad_synced_body_is_a_422(client: TestClient, body: dict) -> None:
    seq = _inquire(client, "문의")["id"]
    assert client.post(f"{READ}/{seq}/synced", json=body, headers=SUPPORT).status_code == 422


def test_only_support_and_admin_may_read_the_inbox(client: TestClient) -> None:
    _inquire(client, "문의 원문")
    assert client.get(READ).status_code == 401  # 토큰 없음
    for who in ("marketing", "finance", "data"):
        res = client.get(READ, headers={"Authorization": f"Bearer mock:{who}"})
        assert res.status_code == 403 and "문의 원문" not in res.text
    assert client.get(READ, headers=ADMIN).status_code == 200
    synced = client.post(
        f"{READ}/1/synced",
        json={"support_inquiry_id": 1},
        headers={"Authorization": "Bearer mock:marketing"},
    )
    assert synced.status_code == 403


def test_the_customer_list_shows_only_the_buyers_own_inbox_text(client: TestClient) -> None:
    _inquire(client, "내 원문 문의")
    mine = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0001"})
    assert "내 원문 문의" in mine.text  # 본인이 쓴 글의 앞부분은 처리 전에도 보인다
    other = client.get("/api/shop/inquiries", params={"buyer_id": "buy_0002"})
    assert "내 원문 문의" not in other.text


def test_synced_inquiries_drop_their_photos_but_keep_the_text() -> None:
    database = Db(":memory:")
    client = TestClient(create_app(database))
    seq = _inquire(client, "사진 문의", images=[PHOTO])["id"]
    assert client.get(READ, headers=SUPPORT).json()["data"][0]["images"] == [PHOTO]
    client.post(f"{READ}/{seq}/synced", json={"support_inquiry_id": 3}, headers=SUPPORT)
    with database.tx() as conn:
        row = conn.execute(
            "SELECT images, message FROM inquiry_inbox WHERE seq = ?", (seq,)
        ).fetchone()
    assert row["images"] is None and row["message"] == "사진 문의"


def test_inquiries_survive_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "shop.db")
    first = TestClient(create_app(Db(path)))
    _inquire(first, "재시작 전 문의")
    again = TestClient(create_app(Db(path)))
    items = again.get(READ, headers=SUPPORT).json()["data"]
    assert [i["message"] for i in items] == ["재시작 전 문의"]

"""접수 때 붙은 영수증 사진: 주문과 대조한 결과를 내력에 남기고, 실패해도 접수는 된다."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, handlers, receipts
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore

PHOTO = {"ref": "img_1", "media_type": "image/jpeg", "data_base64": "QUJD"}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("SUPPORT_MOCK", raising=False)
    monkeypatch.delenv("SUPPORT_ORDER_SOURCE", raising=False)
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    receipts.set_vision_extractor(None)
    yield
    receipts.set_vision_extractor(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(
    client: TestClient,
    skill: str,
    input: dict[str, Any],
    untrusted: dict[str, Any] | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_inq_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input,
        "untrusted": untrusted or {},
    }
    return client.post("/a2a", json=body).json()


def create(client: TestClient, images: list[Any] | None, order_id: str | None = "101") -> int:
    untrusted: dict[str, Any] = {"subject": "영수증 확인", "message": "영수증 사진을 올립니다."}
    if images is not None:
        untrusted["images"] = images
    out = call(
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": order_id},
        untrusted,
        key=f"k-{id(images)}-{order_id}",
    )
    assert out["status"] == "completed"
    return out["output"]["id"]


def receipt_entries(client: TestClient, inquiry_id: int) -> list[dict[str, Any]]:
    out = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]
    return [a for a in out["activity"] if a["kind"] == "receipt_checked"]


def test_an_inquiry_without_photos_has_no_receipt_entry(client: TestClient) -> None:
    assert receipt_entries(client, create(client, None)) == []


def test_photos_are_read_and_the_check_is_kept_in_the_history(client: TestClient) -> None:
    async def extractor(images: list[Any]) -> receipts.ReceiptFacts:
        return receipts.ReceiptFacts(total=1)  # 주문 금액과 다른 값

    receipts.set_vision_extractor(extractor)
    entries = receipt_entries(client, create(client, [PHOTO]))
    assert len(entries) == 1
    data = entries[0]["data"]
    assert data["images"] == 1 and data["verdict"] == "mismatch"
    assert data["analysis"]["images_status"] == "read"  # 콘솔의 영수증 카드가 그대로 읽는 모양
    assert "data_base64" not in str(entries[0])  # 사진 내용은 내력에 없다


def test_without_an_image_reader_it_is_recorded_as_unavailable(client: TestClient) -> None:
    entries = receipt_entries(client, create(client, [PHOTO]))
    assert entries[0]["data"]["analysis"]["images_status"] == "unavailable"


def test_a_failing_reader_does_not_block_the_inquiry(client: TestClient) -> None:
    async def broken(images: list[Any]) -> receipts.ReceiptFacts:
        raise RuntimeError("vision down")

    receipts.set_vision_extractor(broken)
    inquiry_id = create(client, [PHOTO])
    assert receipt_entries(client, inquiry_id)[0]["data"]["analysis"]["images_status"] == "failed"


def test_too_many_photos_are_skipped_but_the_inquiry_is_created(client: TestClient) -> None:
    entries = receipt_entries(client, create(client, [PHOTO] * 5))
    assert entries[0]["data"]["analysis"]["images_status"] == "skipped"


def test_a_malformed_photo_list_is_skipped_not_fatal(client: TestClient) -> None:
    assert create(client, [{"nope": 1}]) > 0


def test_photos_without_an_order_leave_a_note_and_still_create_the_inquiry(
    client: TestClient,
) -> None:
    entries = receipt_entries(client, create(client, [PHOTO], order_id=None))
    assert entries and entries[0]["data"]["verdict"] is None


def test_photo_bytes_are_not_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    async def broken(images: list[Any]) -> receipts.ReceiptFacts:
        raise RuntimeError("boom")

    receipts.set_vision_extractor(broken)
    secret = {"ref": "img_9", "media_type": "image/png", "data_base64": "U0VDUkVUUEhPVE8="}
    with caplog.at_level(logging.DEBUG):
        create(client, [secret])
    assert "U0VDUkVUUEhPVE8" not in caplog.text

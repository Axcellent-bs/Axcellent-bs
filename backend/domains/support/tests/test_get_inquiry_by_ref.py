"""`get_inquiry`를 context_id(재무 기록의 inquiry_ref)로도 부른다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import handlers
from support_agent.main import app
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("SUPPORT_MOCK", raising=False)
    store = InquiryStore()
    for user_id, context_id in ((1, "ctx_inq_aaaa"), (2, "ctx_inq_bbbb"), (1, "ctx_inq_aaaa")):
        store.create(
            context_id=context_id,
            tenant_id="shop-demo",
            user_id=user_id,
            customer_name="김하늘",
            subject="배송 문의",
            order_id=None,
            order_no=None,
            first_message="언제 오나요",
            analysis=Analysis(),
        )
    monkeypatch.setattr(handlers, "STORE", store)
    yield


def call(**input_: Any) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_ui_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "get_inquiry",
        "input": input_,
    }
    return TestClient(app).post("/a2a", json=body).json()


def test_finds_the_inquiry_by_its_context_id() -> None:
    result = call(context_id="ctx_inq_bbbb")
    assert result["status"] == "completed" and result["outcome"] != "not_found"
    assert result["output"]["id"] == 2


def test_same_context_id_gives_the_latest_inquiry() -> None:
    assert call(context_id="ctx_inq_aaaa")["output"]["id"] == 3


def test_unknown_context_id_says_the_inquiry_is_gone_from_the_store() -> None:
    result = call(context_id="ctx_inq_zzzz")
    assert result["outcome"] == "not_found"
    assert "고객지원 저장소에 없어요" in result["output"]["message"]


def test_a_customer_cannot_open_someone_elses_inquiry_by_context_id() -> None:
    result = call(context_id="ctx_inq_bbbb", user_id=1)  # 문의 2는 고객 2의 것
    assert result["outcome"] == "not_found"


def test_the_inquiry_number_still_works() -> None:
    assert call(inquiry_id=1)["output"]["id"] == 1


def test_giving_both_or_neither_key_is_rejected() -> None:
    for input_ in ({}, {"inquiry_id": 1, "context_id": "ctx_inq_aaaa"}):
        assert call(**input_)["status"] != "completed"

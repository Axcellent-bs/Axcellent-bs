"""판매 서버 자료를 다시 만들기 전에 받은 문의에 "옛 자료" 표시(`legacy_intake`)를 붙인다.

설정(`SUPPORT_LEGACY_INTAKE_BEFORE`)과 접수일만 비교한다.
판매 서버는 부르지 않고, 상태·재무는 바꾸지 않는다.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import handlers
from support_agent.main import app
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore

ENV = "SUPPORT_LEGACY_INTAKE_BEFORE"
# 11/14 0시(한국 시간) = 11/13 15:00 UTC. 이 시각 정각은 옛 자료가 아니고, 1초 전은 옛 자료다
BOUNDARY = "2026-11-13T15:00:00Z"
BEFORE = "2026-11-13T14:59:59Z"
LATER = "2026-11-17T07:28:52Z"


def _make(store: InquiryStore, received: str, *, user_id: int = 1, ctx: str = "ctx_inq_x") -> int:
    conversation = store.create(
        context_id=ctx,
        tenant_id="shop-demo",
        user_id=user_id,
        customer_name="김하늘",
        subject="배송 문의",
        order_id="ord_1",
        order_no=None,
        first_message="언제 오나요",
        analysis=Analysis(),
    )
    conversation.messages[0].created_at = received
    conversation.updated_at = received
    return conversation.id


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("SUPPORT_MOCK", raising=False)
    monkeypatch.delenv(ENV, raising=False)
    store = InquiryStore()
    # 번호 순서와 접수일 순서를 일부러 엇갈리게 한다: 1=나중, 2=정각, 3=1초 전, 4=옛날
    _make(store, LATER, ctx="ctx_inq_1")
    _make(store, BOUNDARY, ctx="ctx_inq_2")
    _make(store, BEFORE, ctx="ctx_inq_3")
    _make(store, "2026-11-11T10:47:35Z", ctx="ctx_inq_4")
    monkeypatch.setattr(handlers, "STORE", store)
    yield


def call(skill: str, **input_: Any) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_ui_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input_,
    }
    return TestClient(app).post("/a2a", json=body).json()


def marks(**input_: Any) -> dict[int, Any]:
    items = call("list_inquiries", **input_)["output"]["items"]
    return {i["id"]: i.get("legacy_intake") for i in items}


def test_inquiries_received_before_the_setting_date_are_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "2026-11-14")
    assert marks() == {1: False, 2: False, 3: True, 4: True}


def test_the_mark_follows_the_received_date_not_the_inquiry_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "2026-11-14")
    # 번호가 가장 큰 5번이 옛날에 받은 문의여도 표시된다
    _make(handlers.STORE, "2026-11-01T00:00:00Z", ctx="ctx_inq_5")
    assert marks()[5] is True and marks()[1] is False


def test_nothing_is_marked_without_a_setting() -> None:
    assert marks() == {1: False, 2: False, 3: False, 4: False}


@pytest.mark.parametrize("raw", ["11/14", "2026-13-40", "내일", "  "])
def test_nothing_is_marked_when_the_setting_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(ENV, raw)
    assert set(marks().values()) == {False}


def test_marking_changes_neither_the_status_nor_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "2026-11-14")
    items = call("list_inquiries")["output"]["items"]
    assert len(items) == 4  # 백엔드는 숨기지 않는다 (숨기는 것은 화면이다)
    assert {i["status"] for i in items} == {"open"}


def test_customer_calls_do_not_get_the_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "2026-11-14")
    items = call("list_inquiries", user_id=1)["output"]["items"]
    assert items and all("legacy_intake" not in i for i in items)


def test_the_detail_carries_the_mark_for_staff_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "2026-11-14")
    assert call("get_inquiry", inquiry_id=4)["output"]["legacy_intake"] is True
    assert call("get_inquiry", inquiry_id=1)["output"]["legacy_intake"] is False
    assert call("get_inquiry", context_id="ctx_inq_3")["output"]["legacy_intake"] is True
    assert "legacy_intake" not in call("get_inquiry", inquiry_id=4, user_id=1)["output"]

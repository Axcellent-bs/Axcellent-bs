"""해결된 문의에 대한 고객 평가(후기): 남기기, 표시 규칙, 만족도 집계, 공개·숨김, 권한."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, feedback, handlers
from support_agent.claims import ClaimStore
from support_agent.main import ALL_HANDLERS, app, load_card
from support_agent.schemas import Conversation
from support_agent.store import InquiryStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(
    client: TestClient,
    skill: str,
    input: dict[str, Any] | None = None,
    untrusted: dict[str, Any] | None = None,
    key: str | None = None,
    token: str = "mock:dashboard-api",
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_inq_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input or {},
        "untrusted": untrusted or {},
    }
    return client.post("/a2a", json=body).json()


def resolved_inquiry(client: TestClient, user_id: int = 1, key: str = "k") -> int:
    created = call(
        client,
        "create_inquiry",
        {"user_id": user_id, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": "책이 찢어졌어요", "message": "표지가 찢어져 왔어요. 환불해 주세요."},
        key=f"{key}-create",
    )["output"]
    call(
        client,
        "set_inquiry_status",
        {"inquiry_id": created["id"], "status": "resolved"},
        key=f"{key}-resolve",
    )
    return created["id"]


def rate(
    client: TestClient,
    inquiry_id: int,
    rating: int,
    resolved: bool,
    comment: str = "",
    key: str | None = None,
) -> dict[str, Any]:
    return call(
        client,
        "submit_feedback",
        {"inquiry_id": inquiry_id, "rating": rating, "resolved": resolved},
        {"comment": comment},
        key=key or f"fb-{inquiry_id}",
    )


# ---------------------------------------------------------------- 표시 규칙 (순수 코드)
def test_a_clean_review_is_public_and_a_low_one_is_a_follow_up() -> None:
    text, flags = feedback.assess("빠르게 해결해 주셔서 감사합니다", 5, True)
    assert (text, flags) == ("빠르게 해결해 주셔서 감사합니다", [])
    assert feedback.initial_status(flags) == "visible"

    _, low = feedback.assess("아직도 안 왔어요", 1, False)
    assert [f.code for f in low] == ["CS_FOLLOW_UP"] and low[0].info is True
    assert feedback.initial_status(low) == "visible"  # 선제 연락은 알림일 뿐 공개를 막지 않는다


@pytest.mark.parametrize(
    ("comment", "code"),
    [
        ("연락은 010-1234-5678 로 주세요", "PERSONAL_INFO"),
        ("자세한 건 https://example.com/x 에서", "LINK_OR_CONTACT"),
        ("카톡으로 연락 주세요", "LINK_OR_CONTACT"),
        ("씨발 이게 뭐냐", "PROFANITY"),
        ("광고 문의는 이쪽으로", "PROMOTION"),
        ("ㅋㅋㅋㅋㅋㅋㅋㅋ", "REPEATED_CHARS"),
    ],
)
def test_risky_words_hold_the_review_for_a_person(comment: str, code: str) -> None:
    _, flags = feedback.assess(comment, 5, True)
    assert code in [f.code for f in flags]
    assert feedback.initial_status(flags) == "flagged"


def test_personal_information_is_masked_before_it_is_stored() -> None:
    text, _ = feedback.assess("010-1234-5678 로 연락 주세요", 4, True)
    assert "010-1234-5678" not in text and "[전화번호]" in text


def test_overview_counts_without_inventing_numbers() -> None:
    empty = feedback.overview([])
    assert empty["average_rating"] is None and empty["satisfied_rate"] is None
    assert empty["response_rate"] is None and empty["rated"] == 0


# ---------------------------------------------------------------- skill
def test_the_skills_are_on_the_card_with_the_right_grades() -> None:
    card = load_card()
    grades = {s.id: s.grade for s in card.skills}
    assert grades["submit_feedback"] == "A" and grades["set_feedback_status"] == "A"
    assert grades["list_feedback"] == "read" and grades["feedback_overview"] == "read"
    assert {"submit_feedback", "list_feedback", "feedback_overview", "set_feedback_status"} <= set(
        ALL_HANDLERS
    )


def test_a_resolved_inquiry_can_be_rated_once(client: TestClient) -> None:
    inquiry_id = resolved_inquiry(client)
    done = rate(client, inquiry_id, 5, True, "감사합니다")
    assert done["status"] == "completed"
    assert {k: v for k, v in done["output"].items() if k != "execution_id"} == {
        "inquiry_id": inquiry_id,
        "rated": True,
        "rating": 5,
    }
    got = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]
    assert (got["feedback"]["rating"], got["feedback"]["status"]) == (5, "visible")
    assert got["activity"][-1]["kind"] == "feedback_received"
    assert got["activity"][-1]["actor"] == "customer"
    assert "감사합니다" not in str(got["activity"])  # 후기 글은 내력에 싣지 않는다

    again = rate(client, inquiry_id, 1, False, key="fb-again")
    assert again["outcome"] == "invalid_state"
    assert (
        call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["feedback"]["rating"] == 5
    )


def test_an_inquiry_that_is_not_resolved_cannot_be_rated(client: TestClient) -> None:
    created = call(
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": "s", "message": "m"},
        key="k-open",
    )["output"]
    result = rate(client, created["id"], 5, True)
    assert result["outcome"] == "invalid_state"
    assert call(client, "get_inquiry", {"inquiry_id": created["id"]})["output"]["feedback"] is None


def test_the_rating_must_be_between_one_and_five(client: TestClient) -> None:
    inquiry_id = resolved_inquiry(client)
    for bad in (0, 6):
        assert rate(client, inquiry_id, bad, True, key=f"fb-bad-{bad}")["status"] == "failed"


def test_a_risky_review_waits_for_a_person_and_can_then_be_published(client: TestClient) -> None:
    inquiry_id = resolved_inquiry(client)
    rate(client, inquiry_id, 5, True, "광고 문의는 010-1234-5678")
    item = call(client, "list_feedback", {"status": "flagged"})["output"]["items"][0]
    assert item["inquiry_id"] == inquiry_id and "010-1234-5678" not in item["feedback"]["comment"]

    done = call(
        client, "set_feedback_status", {"inquiry_id": inquiry_id, "status": "visible"}, key="k-pub"
    )
    assert done["status"] == "completed"
    fb = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["feedback"]
    assert (fb["status"], fb["decided_by"]) == ("visible", "dashboard-api") and fb["decided_at"]
    assert call(client, "list_feedback", {"status": "flagged"})["output"]["items"] == []
    last = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["activity"][-1]
    assert (last["kind"], last["title"]) == (
        "feedback_status_changed",
        "상담원이 후기를 공개로 정했어요",
    )


def test_setting_a_status_needs_a_review(client: TestClient) -> None:
    inquiry_id = resolved_inquiry(client)
    done = call(
        client, "set_feedback_status", {"inquiry_id": inquiry_id, "status": "hidden"}, key="k-h"
    )
    assert done["outcome"] == "invalid_state"


def test_the_overview_counts_every_rating_even_hidden_ones(client: TestClient) -> None:
    first = resolved_inquiry(client, key="a")
    second = resolved_inquiry(client, key="b")
    third = resolved_inquiry(client, key="c")
    open_one = call(  # 해결하지 않은 문의는 응답률 분모에 들어가지 않는다
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": "s", "message": "m"},
        key="k-open2",
    )["output"]
    rate(client, first, 5, True)
    rate(client, second, 4, True)
    rate(client, third, 1, False, "ㅋㅋㅋㅋㅋㅋㅋ")
    call(client, "set_feedback_status", {"inquiry_id": third, "status": "hidden"}, key="k-hide")

    o = call(client, "feedback_overview")["output"]
    assert (o["resolved_inquiries"], o["rated"]) == (3, 3)
    assert o["response_rate"] == 1.0
    assert o["average_rating"] == round((5 + 4 + 1) / 3, 2)
    assert o["satisfied_rate"] == round(2 / 3, 3)  # 4~5점
    assert o["solved_rate"] == round(2 / 3, 3)
    assert o["distribution"] == {"1": 1, "2": 0, "3": 0, "4": 1, "5": 1}
    assert o["hidden"] == 1 and o["follow_up"] == 1 and o["pending_review"] == 0
    assert o["by_category"]["refund"] == {"count": 3, "average": 3.33}
    assert open_one["id"] not in {first, second, third}


def test_list_filters_by_rating_and_is_newest_first(client: TestClient) -> None:
    ids = [resolved_inquiry(client, key=k) for k in ("a", "b", "c")]
    for n, inquiry_id in zip((5, 2, 1), ids, strict=True):
        rate(client, inquiry_id, n, n > 2)
    items = call(client, "list_feedback", {})["output"]["items"]
    assert [i["inquiry_id"] for i in items] == sorted(ids, reverse=True)
    low = call(client, "list_feedback", {"max_rating": 2})["output"]
    assert sorted(i["feedback"]["rating"] for i in low["items"]) == [1, 2] and low["total"] == 2


# ---------------------------------------------------------------- 고객 화면(shop-api)
def test_a_customer_can_only_rate_their_own_inquiry(client: TestClient) -> None:
    inquiry_id = resolved_inquiry(client, user_id=1)
    other = call(
        client,
        "submit_feedback",
        {"inquiry_id": inquiry_id, "rating": 5, "resolved": True, "user_id": 2},
        {"comment": ""},
        token="mock:shop-api",
        key="fb-other",
    )
    assert other["outcome"] == "not_found"  # 남의 문의는 있는지도 알려 주지 않는다
    mine = call(
        client,
        "submit_feedback",
        {"inquiry_id": inquiry_id, "rating": 5, "resolved": True, "user_id": 1},
        {"comment": "좋았어요"},
        token="mock:shop-api",
        key="fb-mine",
    )
    assert mine["status"] == "completed" and mine["output"]["rated"] is True
    assert set(mine["output"]) - {"execution_id"} == {
        "inquiry_id",
        "rated",
        "rating",
    }  # 내부 판단은 보이지 않는다


def test_a_customer_request_needs_a_user_and_cannot_claim_to_be_the_console(
    client: TestClient,
) -> None:
    inquiry_id = resolved_inquiry(client)
    no_user = call(
        client,
        "submit_feedback",
        {"inquiry_id": inquiry_id, "rating": 5, "resolved": True},
        token="mock:shop-api",
        key="fb-nouser",
    )
    assert no_user["status"] == "failed"
    call(
        client,
        "submit_feedback",
        {
            "inquiry_id": inquiry_id,
            "rating": 5,
            "resolved": True,
            "user_id": 1,
            "source": "console",
        },
        token="mock:shop-api",
        key="fb-spoof",
    )
    fb = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["feedback"]
    assert fb["source"] == "customer"  # 고객 화면은 시험용 표시를 쓸 수 없다


def test_only_the_console_may_change_the_visibility(client: TestClient) -> None:
    inquiry_id = resolved_inquiry(client)
    rate(client, inquiry_id, 5, True)
    denied = call(
        client,
        "set_feedback_status",
        {"inquiry_id": inquiry_id, "status": "hidden"},
        token="mock:shop-api",
        key="fb-deny",
    )
    assert denied["status"] != "completed"
    fb = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["feedback"]
    assert fb["status"] == "visible"


def test_conversation_model_has_no_feedback_by_default() -> None:
    assert Conversation.model_fields["feedback"].default is None

"""해결된 문의의 해결 메모: 받는 규칙, 쌓는 순서, 개인정보 가리기, 권한."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, handlers, resolution_notes
from support_agent.claims import ClaimStore
from support_agent.main import ALL_HANDLERS, app, load_card
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


def inquiry(client: TestClient, resolved: bool, key: str = "k") -> int:
    created = call(
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": "책이 찢어졌어요", "message": "표지가 찢어져 왔어요."},
        key=f"{key}-create",
    )["output"]
    if resolved:
        call(
            client,
            "set_inquiry_status",
            {"inquiry_id": created["id"], "status": "resolved"},
            key=f"{key}-resolve",
        )
    return created["id"]


def add_note(
    client: TestClient,
    inquiry_id: int,
    root_cause: Any = "",
    action_taken: Any = "",
    result: Any = "",
    author: str = "김상담",
    key: str | None = None,
    token: str = "mock:dashboard-api",
) -> dict[str, Any]:
    return call(
        client,
        "add_resolution_note",
        {"inquiry_id": inquiry_id, "author": author},
        {"root_cause": root_cause, "action_taken": action_taken, "result": result},
        key=key or f"note-{inquiry_id}-{root_cause}{action_taken}{result}",
        token=token,
    )


def notes_of(client: TestClient, inquiry_id: int) -> list[dict[str, Any]]:
    return call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["resolution_notes"]


# ---------------------------------------------------------------- 규칙 (순수 코드)
def test_blank_fields_become_none_and_a_note_needs_one_field() -> None:
    values = {
        key: resolution_notes.clean(v)
        for key, v in zip(resolution_notes.FIELDS, ("  배송 지연  ", "   ", None), strict=True)
    }
    assert values == {"root_cause": "배송 지연", "action_taken": None, "result": None}
    assert resolution_notes.problem(values) is None
    empty = {key: None for key in resolution_notes.FIELDS}
    assert resolution_notes.problem(empty) == "원인·조치·결과 중 하나는 적어야 한다"


def test_the_length_limit_is_per_field_and_nothing_is_cut() -> None:
    at_limit = {"root_cause": "가" * 1000, "action_taken": None, "result": None}
    assert resolution_notes.problem(at_limit) is None
    over = {"root_cause": None, "action_taken": "나" * 1001, "result": None}
    assert "1000자" in (resolution_notes.problem(over) or "")


def test_personal_information_is_masked_and_the_kind_is_reported() -> None:
    texts, kinds = resolution_notes.mask(
        {"root_cause": "고객 010-1234-5678 로 연락함", "action_taken": None, "result": "완료"}
    )
    assert "010-1234-5678" not in str(texts) and "[전화번호]" in str(texts["root_cause"])
    assert texts["action_taken"] is None and texts["result"] == "완료"
    assert kinds


# ---------------------------------------------------------------- skill
def test_the_skill_is_on_the_card_as_a_console_only_write() -> None:
    card = load_card()
    skill = next(s for s in card.skills if s.id == "add_resolution_note")
    assert skill.grade == "A" and skill.allowed_requesters == ["dashboard-api"]
    assert "add_resolution_note" in ALL_HANDLERS


def test_a_resolved_inquiry_takes_a_note_and_it_shows_with_the_inquiry(
    client: TestClient,
) -> None:
    inquiry_id = inquiry(client, resolved=True)
    done = add_note(client, inquiry_id, "택배사 분류 오류", "재발송 요청", "다음 날 도착")
    assert done["status"] == "completed"
    assert {k: v for k, v in done["output"].items() if k != "execution_id"} == {
        "inquiry_id": inquiry_id,
        "note_id": 1,
    }
    (note,) = notes_of(client, inquiry_id)
    assert (note["author"], note["root_cause"], note["action_taken"], note["result"]) == (
        "김상담",
        "택배사 분류 오류",
        "재발송 요청",
        "다음 날 도착",
    )
    got = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]
    assert got["activity"][-1]["kind"] == "resolution_note_added"
    assert "택배사" not in str(got["activity"])  # 메모 글은 내력에 싣지 않는다


def test_notes_pile_up_newest_first_and_empty_fields_stay_null(client: TestClient) -> None:
    inquiry_id = inquiry(client, resolved=True)
    add_note(client, inquiry_id, root_cause="첫 번째 원인", author="김상담")
    add_note(client, inquiry_id, result="두 번째 결과", author="이상담")
    first, older = notes_of(client, inquiry_id)
    assert (first["id"], first["author"], first["result"]) == (2, "이상담", "두 번째 결과")
    assert first["root_cause"] is None and first["action_taken"] is None
    assert (older["id"], older["author"], older["root_cause"]) == (1, "김상담", "첫 번째 원인")


def test_personal_information_is_masked_before_it_is_stored(client: TestClient) -> None:
    inquiry_id = inquiry(client, resolved=True)
    add_note(client, inquiry_id, root_cause="고객 메일 a@b.com 로 안내")
    (note,) = notes_of(client, inquiry_id)
    assert "a@b.com" not in note["root_cause"] and "[이메일]" in note["root_cause"]


def test_an_inquiry_that_is_not_resolved_takes_no_note(client: TestClient) -> None:
    inquiry_id = inquiry(client, resolved=False)
    result = add_note(client, inquiry_id, "원인")
    assert result["outcome"] == "invalid_state"
    assert notes_of(client, inquiry_id) == []


def test_unknown_inquiry_is_not_found(client: TestClient) -> None:
    assert add_note(client, 999, "원인")["outcome"] == "not_found"


@pytest.mark.parametrize(
    "fields",
    [
        {},  # 셋 다 비었다
        {"root_cause": "   ", "action_taken": "\n", "result": ""},  # 공백뿐
        {"root_cause": "가" * 1001},  # 길이 초과
        {"root_cause": 123, "action_taken": None, "result": ["x"]},  # 문자열이 아니다
    ],
)
def test_an_empty_or_too_long_note_is_refused_and_nothing_is_stored(
    client: TestClient, fields: dict[str, Any]
) -> None:
    inquiry_id = inquiry(client, resolved=True)
    result = add_note(client, inquiry_id, key=f"bad-{len(str(fields))}", **fields)
    assert result["status"] == "failed"
    assert notes_of(client, inquiry_id) == []


def test_the_author_must_be_given(client: TestClient) -> None:
    inquiry_id = inquiry(client, resolved=True)
    assert add_note(client, inquiry_id, "원인", author="")["status"] == "failed"
    assert add_note(client, inquiry_id, "원인", author="가" * 51, key="long")["status"] == "failed"
    assert notes_of(client, inquiry_id) == []


def test_only_the_console_can_write_a_note(client: TestClient) -> None:
    inquiry_id = inquiry(client, resolved=True)
    for requester in ("mock:shop-api", "mock:orchestrator"):
        refused = add_note(client, inquiry_id, "원인", token=requester, key=f"no-{requester}")
        assert refused["status"] == "rejected"
    assert notes_of(client, inquiry_id) == []


def test_a_resent_request_does_not_add_the_note_twice(client: TestClient) -> None:
    inquiry_id = inquiry(client, resolved=True)
    for _ in range(2):
        add_note(client, inquiry_id, "원인", key="same-key")
    assert len(notes_of(client, inquiry_id)) == 1

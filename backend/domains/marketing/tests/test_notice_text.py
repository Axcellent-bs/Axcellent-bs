"""공지 문안: LLM 초안 → 코드 검증 → 통과할 때만 쓰고, 아니면 고정 템플릿 문구."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_agent_template.llm import MockLLMClient
from marketing_agent import handlers
from marketing_agent.main import app
from marketing_agent.notice_text import NoticeTextRead, check_text
from marketing_agent.schemas import DraftNoticeInput

CTX = "ctx_inc_0101"
client = TestClient(app)

DRAFT_INPUT = {
    "kind": "incident_opened",
    "facts": {"component": "payment"},
    "promises": [
        {
            "type": "points",
            "amount": 2000,
            "condition_code": "LATE_3D",
            "condition": "3일 이상 지연",
        }
    ],
}
TEMPLATE = (
    "결제 이용이 원활하지 않은 상황을 확인했습니다. 복구되는 대로 안내드리겠습니다."
    " (3일 이상 지연: 적립금 2,000원)"
)
GOOD = (
    "지금 결제가 잠시 원활하지 않습니다. 확인하는 대로 다시 알려 드리겠습니다."
    " 3일 이상 지연되면 적립금 2,000원을 드립니다."
)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()


def _use_llm(monkeypatch: pytest.MonkeyPatch, text: str | None) -> MockLLMClient | None:
    llm = MockLLMClient(responses={NoticeTextRead: NoticeTextRead(text=text)}) if text else None
    monkeypatch.setattr(handlers, "NOTICE_LLM", lambda: llm)
    return llm


def _draft(data: dict = DRAFT_INPUT, context_id: str = CTX, **extra) -> dict:
    body = {
        "meta": {
            "context_id": context_id,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "draft_notice",
        "input": data,
        **extra,
    }
    return client.post("/a2a", json=body).json()


def test_valid_ai_draft_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _use_llm(monkeypatch, GOOD)
    result = _draft()
    out = result["output"]
    assert result["status"] == "completed" and result["fallback"] is False
    assert out["public_text"] == GOOD and out["source"] == "ai"
    assert out["text_check"] == {"passed": True, "reasons": []}
    assert out["template_text"] == TEMPLATE
    # 사실 필드만 데이터 블록으로 넘긴다
    assert llm is not None and "2,000원" in llm.calls[0]


def test_invented_number_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_llm(monkeypatch, GOOD + " 지금 주문하면 30% 할인 쿠폰도 드립니다.")
    out = _draft()["output"]
    assert out["source"] == "template" and out["public_text"] == TEMPLATE
    reasons = out["text_check"]["reasons"]
    assert {"number_not_in_facts", "percent_not_in_facts", "benefit_not_in_facts"} <= set(reasons)


def test_no_llm_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_llm(monkeypatch, None)
    result = _draft()
    assert result["fallback"] is True
    assert result["output"]["public_text"] == TEMPLATE
    assert result["output"]["source"] == "template"
    assert result["output"]["text_check"]["reasons"] == ["llm_unavailable"]


def test_too_long_draft_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_llm(monkeypatch, GOOD + " 이용에 불편을 드려 정말 죄송합니다." * 10)
    out = _draft()["output"]
    assert out["source"] == "template" and out["public_text"] == TEMPLATE
    assert "too_long" in out["text_check"]["reasons"]


def test_llm_error_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "NOTICE_LLM", lambda: MockLLMClient())  # 응답 없음 → 예외
    out = _draft()["output"]
    assert out["source"] == "template" and out["text_check"]["reasons"] == ["llm_unavailable"]


def test_same_input_reuses_the_saved_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _use_llm(monkeypatch, GOOD)
    first = _draft()["output"]
    assert _draft()["output"] == first
    assert llm is not None and len(llm.calls) == 1


def test_source_is_shown_on_published_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_llm(monkeypatch, GOOD)
    draft_id = _draft()["output"]["draft_id"]
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "publish_notice",
        "input": {"draft_id": draft_id},
    }
    notice_id = client.post("/a2a", json=body).json()["output"]["notice_id"]
    view = handlers.STATE.notices.view(notice_id)
    assert view.source == "ai" and view.public_text == GOOD


def test_other_incident_reuses_live_banner_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 사실의 장애 배너가 떠 있으면 그 문구를 써서 같은 문구 배너 같이 쓰기가 그대로 돈다."""
    _use_llm(monkeypatch, GOOD)
    first = _draft()["output"]["draft_id"]
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "publish_notice",
        "input": {"draft_id": first},
    }
    assert client.post("/a2a", json=body).json()["status"] == "completed"
    _use_llm(monkeypatch, "결제가 지금 조금 느립니다. 3일 이상 지연되면 적립금 2,000원을 드립니다.")
    other = _draft(context_id="ctx_inc_0102")["output"]
    assert other["public_text"] == GOOD and other["source"] == "ai"


def _input(**overrides) -> DraftNoticeInput:
    return DraftNoticeInput.model_validate({**DRAFT_INPUT, **overrides})


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("결제가 원활하지 않습니다. 적립금 2,000원은 반드시 보장합니다.", "forbidden_claim"),
        (
            "서버 버그 때문에 결제가 원활하지 않습니다. 적립금 2,000원을 드립니다.",
            "cause_mentioned",
        ),
        ("결제가 정상으로 돌아왔습니다. 3일 이상 지연 시 적립금 2,000원.", "kind_mismatch"),
        ("결제가 원활하지 않습니다. 확인하는 대로 알려 드리겠습니다.", "promise_missing"),
        ("서비스가 원활하지 않습니다. 3일 이상 지연 시 적립금 2,000원.", "component_missing"),
        ("결제 불편 안내. 오늘까지 3일 이상 지연 시 적립금 2,000원.", "period_not_in_facts"),
        ("결제 지연", "too_short"),
    ],
)
def test_check_rules(text: str, reason: str) -> None:
    check = check_text(text, _input(), TEMPLATE)
    assert not check.passed and reason in check.reasons


def test_date_from_facts_is_allowed() -> None:
    data = _input(valid_to="2026-11-24T09:00:00Z")  # 한국 시각 11월 24일 18:00
    text = (
        "결제가 원활하지 않습니다. 11월 24일 18:00까지 3일 이상 지연 시 적립금 2,000원을 드립니다."
    )
    assert check_text(text, data, TEMPLATE).passed
    wrong = text.replace("24일", "25일")
    assert "number_not_in_facts" in check_text(wrong, data, TEMPLATE).reasons

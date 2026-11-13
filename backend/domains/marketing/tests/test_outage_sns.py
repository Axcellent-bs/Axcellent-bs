"""장애 사과·보상 SNS 글 초안 (`draft_outage_sns_post`, `list_outage_sns_drafts`)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from marketing_agent import handlers, outage_sns, sns
from marketing_agent.main import app, load_card

CTX = "ctx_inc_0001"
client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()


def _call(skill: str, input: dict, token: str = "mock:orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _notice_id(kind: str = "incident_resolved") -> str:
    draft = _call("draft_notice", {"kind": kind, "facts": {"component": "payment"}})["output"]
    return _call("publish_notice", {"draft_id": draft["draft_id"]})["output"]["notice_id"]


def _apology(**over) -> dict:
    payload = {
        "trigger": "apology_notice",
        "cause_code": "recent_update",
        "action_code": "reverted_update",
        "recovered": True,
    } | over
    if "notice_id" not in payload:
        payload["notice_id"] = _notice_id()
    return _call("draft_outage_sns_post", payload)


def test_apology_draft_explains_confirmed_cause_in_plain_words() -> None:
    result = _apology()
    assert result["status"] == "completed", result
    out = result["output"]
    caption = out["caption"]
    # 공지 문구 그대로
    assert "결제 이용이 정상으로 돌아왔습니다. 불편을 드려 죄송합니다." in caption
    assert "원인은 장애 직전에 적용한 서비스 업데이트였습니다." in caption
    assert "이전 상태로 되돌렸고" in caption
    assert "확인 중" not in caption
    assert out["source_kind"] == "outage" and out["outage_trigger"] == "apology_notice"
    assert out["notice_kind"] == "incident_resolved"
    assert out["auto_publish"] is False
    assert out["asset_ref"] is None  # 미디어를 만들지 않는다


def test_draft_keeps_ai_label_and_demo_notice() -> None:
    caption = _apology()["output"]["caption"]
    for text in (sns.AI_LABEL_TEXT, sns.DEMO_TEXT, sns.TERMS_TEXT, "#캣북스"):
        assert text in caption


def test_unknown_cause_says_still_checking() -> None:
    out = _apology(cause_code="unknown", action_code="restored")["output"]
    assert "정확한 원인은 아직 확인 중입니다." in out["caption"]
    assert "업데이트" not in out["caption"]


def test_compensation_draft_without_recovery_does_not_claim_cause_or_fix() -> None:
    result = _call(
        "draft_outage_sns_post",
        {"trigger": "compensation", "component": "payment", "compensation_issued": True},
    )
    assert result["status"] == "completed", result
    caption = result["output"]["caption"]
    assert "캣북스 보상 안내" in caption
    assert "보상 쿠폰을 드렸습니다" in caption
    assert "원인은 아직 확인 중" in caption
    assert "복구되는 대로 다시 안내드리겠습니다." in caption
    assert "정상 이용을 확인" not in caption
    assert result["output"]["outage_trigger"] == "compensation"


@pytest.mark.parametrize(
    "payload",
    [
        {"trigger": "apology_notice"},  # 공지 없이 사과 초안 없음
        {"trigger": "compensation", "compensation_issued": False},  # 나간 쿠폰이 없음
        # 복구 확인 전에는 원인을 확정으로 말하지 않는다
        {"trigger": "compensation", "compensation_issued": True, "cause_code": "recent_update"},
        # 복구 확인 전에는 조치를 적지 않는다
        {"trigger": "compensation", "compensation_issued": True, "action_code": "restored"},
        # 자유 글(원인 원문)은 받지 않는다
        {"trigger": "compensation", "compensation_issued": True, "cause_text": "payment v1.4.2"},
        {"trigger": "compensation", "compensation_issued": True, "cause_code": "db_down"},
    ],
)
def test_rejects_unsupported_or_unconfirmed_input(payload: dict) -> None:
    result = _call("draft_outage_sns_post", payload)
    assert (result["status"], result["outcome"]) == ("failed", "invalid_input")
    assert _call("list_outage_sns_drafts", {})["output"]["total"] == 0


def test_requires_published_incident_notice() -> None:
    missing = _apology(notice_id="ntc_none")
    assert (missing["status"], missing["outcome"]) == ("failed", "notice_not_found")
    product = _call("draft_notice", {"kind": "product_created", "facts": {"product_id": "p1"}})
    product_id = _call("publish_notice", {"draft_id": product["output"]["draft_id"]})["output"][
        "notice_id"
    ]
    wrong = _apology(notice_id=product_id)
    assert (wrong["status"], wrong["outcome"]) == ("failed", "notice_not_incident")


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("접속 주소 10.0.0.251 에서 발생", "ip_address"),
        ("결제 모듈 v1.4.2 문제", "version"),
        ("결제 API 오류", "tech_term"),
        ("서버를 다시 시작했습니다", "tech_term"),
        ("문제 배포를 롤백했습니다", "tech_term"),
        ("담당 admin@catbooks", "email_or_account"),
        ("이벤트 ctx_inc_0001", "internal_id"),
        ("실패 주문 128건", "percent_or_metric"),
        ("오류 비율 19%", "percent_or_metric"),
    ],
)
def test_forbidden_filter_catches_internal_terms(text: str, name: str) -> None:
    assert name in outage_sns.forbidden_terms(text)


def test_every_fixed_sentence_passes_the_filter() -> None:
    sentences = [
        *outage_sns.CAUSE_TEXT.values(),
        *outage_sns.ACTION_TEXT.values(),
        outage_sns.NOT_RECOVERED_TEXT,
        outage_sns.COMPENSATION_TEXT,
        outage_sns.APOLOGY_TEXT,
    ]
    for label in [*outage_sns.COMPONENT_LABEL.values(), outage_sns.DEFAULT_LABEL]:
        for sentence in sentences:
            text = sentence.format(label=label)
            assert outage_sns.forbidden_terms(text) == []
            assert not any(p in text for p in sns.FORBIDDEN_PHRASES)


def test_filter_blocks_draft_with_internal_term(monkeypatch) -> None:
    monkeypatch.setitem(outage_sns.CAUSE_TEXT, "unknown", "결제 서버 10.0.0.1 확인 중")
    result = _apology(cause_code="unknown", action_code="none")
    assert (result["status"], result["outcome"]) == ("failed", "forbidden_term")
    assert set(result["output"]["terms"]) >= {"ip_address", "tech_term"}
    assert _call("list_outage_sns_drafts", {})["output"]["total"] == 0


def test_same_input_gives_same_draft_and_listing_shows_it() -> None:
    notice_id = _notice_id()
    first = _apology(notice_id=notice_id)["output"]
    again = _apology(notice_id=notice_id)["output"]
    assert first["post_draft_id"] == again["post_draft_id"]
    listing = _call("list_outage_sns_drafts", {}, token="mock:shop-api")["output"]
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["post_draft_id"] == first["post_draft_id"]
    assert item["trigger"] == "apology_notice" and item["context_id"] == CTX
    assert item["caption"] == first["caption"] and item["media_attached"] is False


def test_listing_excludes_other_drafts() -> None:
    assert _call("draft_sns_post", {"notice_id": _notice_id()})["status"] == "completed"
    listing = _call("list_outage_sns_drafts", {})["output"]
    assert listing["items"] == [] and listing["unknown_reason"]


def test_draft_does_not_publish_or_call_safety(monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise AssertionError("초안 생성은 execute()·게시를 부르지 않는다")

    notice_id = _notice_id()
    monkeypatch.setattr(handlers, "execute", boom)
    monkeypatch.setattr(handlers, "publish_sns", boom)
    assert _apology(notice_id=notice_id)["status"] == "completed"


def test_caption_only_draft_is_not_publishable_but_text_passes_check() -> None:
    out = _apology()["output"]
    check = _call(
        "check_sns_post",
        {"post_draft_id": out["post_draft_id"], "caption": out["caption"], "is_ai_generated": True},
    )["output"]
    assert check["problems"] == [] and check["unverified_number_count"] == 0
    assert check["publishable"] is False  # 미디어가 없어 그대로는 게시할 수 없다


def test_card_requesters() -> None:
    card = {s.id: s for s in load_card().skills}
    assert card["draft_outage_sns_post"].grade == "read"
    assert card["draft_outage_sns_post"].allowed_requesters == ["orchestrator"]
    assert card["publish_sns"].grade == "C"  # 게시는 그대로 사람 승인
    denied = _call(
        "draft_outage_sns_post",
        {"trigger": "compensation", "compensation_issued": True},
        token="mock:support",
    )
    assert denied["status"] == "rejected"

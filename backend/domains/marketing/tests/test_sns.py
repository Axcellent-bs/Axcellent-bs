from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from marketing_agent import handlers, sns
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


def _notice_id() -> str:
    draft = _call("draft_notice", {"kind": "incident_resolved", "facts": {"component": "payment"}})[
        "output"
    ]["draft_id"]
    return _call("publish_notice", {"draft_id": draft})["output"]["notice_id"]


def _coupon(**drop: bool) -> dict:
    constraints = [
        {"code": code, "text": f"{label} 안내 {i}"}
        for i, (code, label) in enumerate(sns.CONSTRAINT_LABELS.items(), 1)
    ]
    return {
        "campaign_id": "cmp_1",
        "unit_value": 2000,
        "constraints": [c for c in constraints if c["code"] not in drop],
    }


def _draft_notice_post() -> dict:
    result = _call("draft_sns_post", {"notice_id": _notice_id()})
    assert result["status"] == "completed", result
    return result["output"]


def _check(draft: dict, **overrides) -> dict:
    data = {
        "post_draft_id": draft["post_draft_id"],
        "caption": draft["caption"],
        "is_ai_generated": True,
    } | overrides
    return _call("check_sns_post", data)


def test_cards_and_acl() -> None:
    card = {s.id: s for s in load_card().skills}
    assert (card["draft_sns_post"].grade, card["check_sns_post"].grade) == ("read", "read")
    assert card["draft_sns_post"].allowed_requesters == ["orchestrator"]
    denied = _call("draft_sns_post", {"notice_id": "ntc_x"}, token="mock:support")
    assert denied["outcome"] == "FORBIDDEN_SKILL"


def test_notice_draft_has_required_wording_and_no_state_change() -> None:
    notice_id = _notice_id()
    published = len(events.MOCK_PUBLISHED)
    result = _call("draft_sns_post", {"notice_id": notice_id})
    out = result["output"]
    public_text = _call("get_notice", {"notice_id": notice_id})["output"]["public_text"]
    assert public_text in out["caption"]
    for text in (sns.AI_LABEL_TEXT, sns.DEMO_TEXT, sns.TERMS_TEXT):
        assert text in out["caption"]
    assert out["is_ai_generated"] is True
    assert out["post_draft_id"].startswith("spd_")
    assert notice_id in result["evidence"]["ref_ids"]
    assert len(events.MOCK_PUBLISHED) == published  # 읽기 전용
    assert _call("draft_sns_post", {"notice_id": notice_id})["output"] == out  # 결정적


def test_draft_needs_published_notice() -> None:
    assert _call("draft_sns_post", {"notice_id": "ntc_none"})["outcome"] == "notice_not_found"
    draft = _call("draft_notice", {"kind": "product_created", "facts": {}})["output"]["draft_id"]
    assert draft  # 게시하지 않은 초안은 공지가 아니다
    assert _call("draft_sns_post", {"notice_id": draft})["outcome"] == "notice_not_found"


def test_ended_notice_is_not_reposted() -> None:
    opened = _call("draft_notice", {"kind": "incident_opened", "facts": {"component": "api"}})
    notice_id = _call("publish_notice", {"draft_id": opened["output"]["draft_id"]})["output"][
        "notice_id"
    ]
    resolved = _call("draft_notice", {"kind": "incident_resolved", "facts": {"component": "api"}})
    _call("publish_notice", {"draft_id": resolved["output"]["draft_id"]})
    assert _call("draft_sns_post", {"notice_id": notice_id})["outcome"] == "notice_not_active"


@pytest.mark.parametrize(
    "input", [{}, {"notice_id": "ntc_1", "coupon": {"campaign_id": "c", "unit_value": 1}}]
)
def test_exactly_one_source(input: dict) -> None:
    assert _call("draft_sns_post", input)["outcome"] == "invalid_input"


def test_coupon_draft_lists_all_constraints() -> None:
    out = _call("draft_sns_post", {"coupon": _coupon()})["output"]
    assert out["source_kind"] == "coupon"
    assert "2,000원 쿠폰" in out["caption"]
    assert len(out["constraints"]) == len(sns.CONSTRAINT_LABELS)
    assert all(c["text"] in out["caption"] for c in out["constraints"])
    assert _check(out)["output"]["verdict"] == "approved"


def test_coupon_without_constraints_is_rejected() -> None:
    result = _call("draft_sns_post", {"coupon": _coupon(refund_handling=True, target=True)})
    assert (result["status"], result["outcome"]) == ("completed", "rejected_constraint_missing")
    assert set(result["output"]["missing_constraints"]) == {"refund_handling", "target"}
    none = _call("draft_sns_post", {"coupon": {"campaign_id": "c", "unit_value": 1}})
    assert len(none["output"]["missing_constraints"]) == len(sns.CONSTRAINT_LABELS)


def test_check_approves_the_unchanged_draft() -> None:
    result = _check(_draft_notice_post())
    out = result["output"]
    assert result["outcome"] is None
    assert out["verdict"] == "approved"
    assert (
        out["ai_label_missing_count"],
        out["text_mismatch_count"],
        out["constraint_missing_count"],
    ) == (0, 0, 0)


@pytest.mark.parametrize("flag", [False, None])
def test_check_rejects_missing_ai_flag(flag: bool | None) -> None:
    result = _check(_draft_notice_post(), is_ai_generated=flag)
    assert result["outcome"] == "rejected_by_check"
    assert result["output"]["ai_label_missing_count"] == 1
    assert result["output"]["verdict"] == "rejected"


def test_check_rejects_missing_label_in_caption() -> None:
    draft = _draft_notice_post()
    caption = draft["caption"].replace(sns.AI_LABEL_TEXT, "")
    out = _check(draft, caption=caption)["output"]
    assert out["ai_label_missing_count"] == 1
    assert out["verdict"] == "rejected"


def test_check_rejects_changed_or_missing_notice_text() -> None:
    draft = _draft_notice_post()
    out = _check(draft, caption=draft["caption"].replace(draft["required_text"][0], "다른 문구"))[
        "output"
    ]
    assert out["text_mismatch_count"] == 2  # 초안과 다름 + 공지 문구 없음
    assert out["verdict"] == "rejected"
    edited = _check(draft, caption=draft["caption"] + " 추가")["output"]
    assert (edited["text_mismatch_count"], edited["verdict"]) == (1, "rejected")


def test_check_rejects_missing_coupon_constraint() -> None:
    draft = _call("draft_sns_post", {"coupon": _coupon()})["output"]
    caption = draft["caption"].replace(draft["constraints"][0]["text"], "")
    out = _check(draft, caption=caption)["output"]
    assert out["constraint_missing_count"] == 1
    assert out["verdict"] == "rejected"


def test_check_rejects_participation_condition_and_foreign_numbers() -> None:
    draft = _draft_notice_post()
    gated = _check(draft, caption=draft["caption"] + "\n팔로우하면 쿠폰")["output"]
    assert gated["forbidden_phrase_count"] >= 1
    assert gated["verdict"] == "rejected"
    impact = _check(draft, caption=draft["caption"] + "\n피해 1,234건")["output"]
    assert impact["unverified_number_count"] >= 1
    assert impact["verdict"] == "rejected"


def test_check_rejects_too_long_caption() -> None:
    draft = _draft_notice_post()
    out = _check(draft, caption=draft["caption"] + "가" * sns.CAPTION_MAX)["output"]
    assert out["caption_too_long"] is True
    assert out["verdict"] == "rejected"


def test_check_unknown_draft_and_strict_flag() -> None:
    assert (
        _call("check_sns_post", {"post_draft_id": "spd_x", "caption": "c"})["outcome"]
        == "draft_not_found"
    )
    draft = _draft_notice_post()
    bad = _check(draft, is_ai_generated="true")
    assert bad["outcome"] == "invalid_input"

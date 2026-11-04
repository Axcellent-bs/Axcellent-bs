from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas.planning import CampaignBriefInput, CampaignBriefOutput
from marketing_agent import briefs, handlers
from marketing_agent.main import app, load_card

CTX = "ctx_brief_0001"
PLANNING = "mock:planning"
client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()


def _call(skill: str, input: dict, token: str = PLANNING) -> dict:
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


def _brief(proposal_id: str = "PROP-1", **over) -> dict:
    base = {
        "schema_version": "1",
        "proposal_id": proposal_id,
        "insight_id": "INS-7",
        "title": "독서의 달 기획전",
        "idea": "11월 독서 키워드 상품을 묶어 배너로 알린다",
        "why": "독서 검색이 작년 11월 평균의 1.6배였다",
        "facts": [
            {
                "label": "독서 검색 작년 11월",
                "value": "평균의 1.6배",
                "source": "trend_insight_brief",
                "link": "https://example.test/trend/1",
            }
        ],
        "target": {
            "label": "캠페인 기간 주문 증분",
            "value": "+10% 이상",
            "measure": "campaign_effect",
        },
        "products": ["prd_1"],
        "format": "banner",
        "approved_by": "예진",
        "note": None,
        "body_md": "# 기획안\n본문",
    }
    return base | over


def _receive(**kwargs) -> dict:
    return _call("receive_campaign_brief", _brief(**kwargs))


def test_first_receipt_is_accepted_with_deterministic_campaign_id() -> None:
    out = _receive()
    assert out["status"] == "completed"
    parsed = CampaignBriefOutput.model_validate(out["output"])
    assert (parsed.outcome, parsed.campaign_id, parsed.reason_code) == (
        "accepted",
        "cmp_PROP-1",
        None,
    )
    handlers.reset_state()  # 저장소가 비어도(다시 시작) 같은 제안은 같은 id
    assert _receive()["output"]["campaign_id"] == "cmp_PROP-1"


def test_same_proposal_again_is_duplicate_and_keeps_stored_brief() -> None:
    _receive()
    stored = _call("get_campaign_brief", {"proposal_id": "PROP-1"})["output"]["item"]
    again = _receive(title="바뀐 제목", target=None)["output"]
    assert (again["outcome"], again["campaign_id"]) == ("duplicate", "cmp_PROP-1")
    assert _call("get_campaign_brief", {"proposal_id": "PROP-1"})["output"]["item"] == stored
    assert _call("list_campaign_briefs", {})["output"]["total"] == 1


def test_different_proposals_get_different_campaign_ids() -> None:
    ids = {_receive(proposal_id=f"PROP-{n}")["output"]["campaign_id"] for n in (1, 2, 3)}
    assert ids == {"cmp_PROP-1", "cmp_PROP-2", "cmp_PROP-3"}


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"title": ""}, "empty_title"),
        ({"title": "   "}, "empty_title"),
        ({"idea": ""}, "empty_idea"),
        ({"why": "\n"}, "empty_why"),
        ({"proposal_id": ""}, "invalid_proposal_id"),
        ({"proposal_id": "PROP 1/2"}, "invalid_proposal_id"),
    ],
)
def test_unacceptable_briefs_are_rejected_with_reason_code(over: dict, code: str) -> None:
    out = _receive(**over)
    assert out["status"] == "completed"
    assert out["output"]["outcome"] == "rejected"
    assert out["output"]["reason_code"] == code
    assert out["output"]["campaign_id"] is None
    assert _call("list_campaign_briefs", {})["output"]["total"] == 0  # 보관하지 않는다


def test_unsupported_schema_version_is_rejected_by_marketing_rules() -> None:
    brief = CampaignBriefInput.model_construct(**_brief(schema_version="2"))
    assert briefs.rejection_reason(brief) == "unsupported_schema_version"
    # 계약 모델이 먼저 막으므로 A2A로는 invalid_input이다
    out = _receive(schema_version="2")
    assert (out["status"], out["outcome"]) == ("failed", "invalid_input")


def test_invalid_input_fails() -> None:
    out = _call("receive_campaign_brief", {"proposal_id": "PROP-1"})
    assert (out["status"], out["outcome"]) == ("failed", "invalid_input")
    assert _call("list_campaign_briefs", {})["output"]["items"] == []


def test_numbers_are_kept_as_received() -> None:
    odd = {"label": "x", "value": "0배", "source": "s", "link": None}
    assert _receive(facts=[odd], target=None)["output"]["outcome"] == "accepted"
    item = _call("get_campaign_brief", {"proposal_id": "PROP-1"})["output"]["item"]
    assert item["brief"]["facts"] == [odd] and item["brief"]["target"] is None


def test_requester_restriction_follows_the_card() -> None:
    card = {s.id: s for s in load_card().skills}
    assert card["receive_campaign_brief"].grade == "A"
    assert card["receive_campaign_brief"].allowed_requesters == ["planning"]
    for caller in ("orchestrator", "data", "finance", "support", "dashboard-api"):
        denied = _call("receive_campaign_brief", _brief(), token=f"mock:{caller}")
        assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL"), caller
    assert _call("list_campaign_briefs", {})["output"]["total"] == 0
    for skill in ("list_campaign_briefs", "get_campaign_brief"):
        assert card[skill].grade == "read"
        assert card[skill].allowed_requesters == ["planning", "orchestrator", "dashboard-api"]
        assert _call(skill, {}, token="mock:hr")["outcome"] == "FORBIDDEN_SKILL"


def test_read_skills_return_stored_briefs_verbatim_newest_first() -> None:
    sent = [_brief(proposal_id=f"PROP-{n}", title=f"제목 {n}") for n in (1, 2, 3)]
    for body in sent:
        _call("receive_campaign_brief", body)
    listing = _call("list_campaign_briefs", {})["output"]
    assert listing["total"] == 3 and listing["unknown_reason"] is None
    assert [i["proposal_id"] for i in listing["items"]] == ["PROP-3", "PROP-2", "PROP-1"]
    for item in listing["items"]:
        assert item["status"] == "received" and item["received_at"]
        assert item["campaign_id"] == f"cmp_{item['proposal_id']}"
        want = next(b for b in sent if b["proposal_id"] == item["proposal_id"])
        assert item["brief"] == CampaignBriefInput.model_validate(want).model_dump(mode="json")
    assert len(_call("list_campaign_briefs", {"limit": 2})["output"]["items"]) == 2
    one = _call("get_campaign_brief", {"campaign_id": "cmp_PROP-2"})["output"]
    assert one["found"] and one["item"] == listing["items"][1]
    by_proposal = _call("get_campaign_brief", {"proposal_id": "PROP-2"})["output"]
    assert by_proposal["item"] == one["item"]


def test_empty_and_missing_are_explained() -> None:
    listing = _call("list_campaign_briefs", {})["output"]
    assert listing["items"] == [] and listing["total"] == 0 and listing["unknown_reason"]
    missing = _call("get_campaign_brief", {"proposal_id": "PROP-9"})["output"]
    assert missing["found"] is False and missing["item"] is None and missing["unknown_reason"]
    assert _call("get_campaign_brief", {})["outcome"] == "invalid_input"


def test_reset_state_clears_briefs() -> None:
    _receive()
    handlers.reset_state()
    assert _call("list_campaign_briefs", {})["output"]["total"] == 0
    assert _receive()["output"]["outcome"] == "accepted"

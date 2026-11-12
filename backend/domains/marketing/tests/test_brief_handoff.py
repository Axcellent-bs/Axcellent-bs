"""받은 기획안을 SNS·미디어로 넘기는 skill `handoff_brief_to_sns` (목 모드, 외부 호출 없음)."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import ExecuteDecision
from marketing_agent import briefs, handlers, sns
from marketing_agent.main import app, load_card

CTX = "ctx_handoff_0001"
client = TestClient(app)
MEDIA_ENV = (
    "MARKETING_IMAGE_PROVIDER",
    "MARKETING_IMAGE_MODEL",
    "MARKETING_VIDEO_PROVIDER",
    "MARKETING_VIDEO_MODEL",
)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in MEDIA_ENV:
        monkeypatch.delenv(name, raising=False)
    events.reset_mock_bus()
    handlers.reset_state()
    gated: list[str] = []

    async def fake_execute(action, params, context, evidence):  # noqa: ANN001
        gated.append(action)
        return ExecuteDecision(execution_id="exe_h1", grade="A", status="executed")

    monkeypatch.setattr(handlers, "execute", fake_execute)

    async def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("넘기기가 생성·게시를 불렀다")

    monkeypatch.setattr(handlers, "generate_media_flow", forbidden)
    monkeypatch.setattr(handlers, "publish_sns", forbidden)
    return gated


def _call(skill: str, input: dict, token: str = "mock:dashboard-api") -> dict:
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


def _receive(proposal_id: str = "PROP-1", **over) -> None:
    brief = {
        "schema_version": "1",
        "proposal_id": proposal_id,
        "insight_id": "INS-7",
        "title": "독서의 달 기획전",
        "idea": "11월 독서 키워드 상품을 묶어 배너로 알린다",
        "why": "독서 검색이 작년 11월 평균의 1.6배였다",
        "facts": [
            {"label": "독서 검색 작년 11월", "value": "평균의 1.6배", "source": "t", "link": None}
        ],
        "target": {"label": "주문 증분", "value": "+10% 이상", "measure": None},
        "products": [],
        "format": "banner",
        "approved_by": "예진",
        "note": None,
        "body_md": None,
    } | over
    out = _call("receive_campaign_brief", brief, token="mock:planning")
    assert out["output"]["outcome"] == "accepted"


def _handoff(proposal_id: str = "PROP-1", **extra) -> dict:
    return _call("handoff_brief_to_sns", {"proposal_id": proposal_id, **extra})


def test_handoff_creates_draft_and_media_request_without_generating(_mock_mode) -> None:  # noqa: ANN001
    _receive()
    out = _handoff()
    assert out["status"] == "completed" and out["outcome"] == "handed"
    state = out["output"]["sns_handoff"]
    assert state["status"] == "handed" and state["handed_at"]
    assert state["draft_state"] == "caption_only"
    media = state["media_request"]
    assert media["media_kind"] == "image" and media["scene_template_id"] == "cat_bookstore_image"
    assert media["auto_generate"] is False and media["fields"] == {}
    assert media["missing_fields"] == ["season", "color_theme", "category"]
    assert media["next_steps"] == ["generate_media", "check_sns_post", "publish_sns"]
    draft = handlers.STATE.sns.get(state["draft_id"])
    assert draft is not None and draft.source_kind == "brief" and draft.source_ref == "PROP-1"
    # 받은 그대로: 숫자를 새로 만들지 않는다
    assert "평균의 1.6배" in draft.caption and "+10%" not in draft.caption
    assert sns.AI_LABEL_TEXT in draft.caption and draft.is_ai_generated is True
    assert _mock_mode == [
        "receive_campaign_brief"
    ]  # 넘기기는 안전 관문을 부르지 않았다(받을 때 한 번뿐)


def test_drafted_caption_passes_existing_check_but_is_not_publishable() -> None:
    _receive()
    draft_id = _handoff()["output"]["sns_handoff"]["draft_id"]
    caption = handlers.STATE.sns.get(draft_id).caption
    check = _call(
        "check_sns_post",
        {"post_draft_id": draft_id, "caption": caption, "is_ai_generated": True},
        token="mock:orchestrator",
    )["output"]
    assert check["unverified_number_count"] == 0 and check["verdict"] == "approved"
    assert check["publishable"] is False  # 미디어가 없어 게시할 수 없다


def test_extra_number_in_caption_is_flagged_by_product_number_rule() -> None:
    _receive()
    draft_id = _handoff()["output"]["sns_handoff"]["draft_id"]
    caption = handlers.STATE.sns.get(draft_id).caption + " 단 3일만"
    check = _call(
        "check_sns_post",
        {"post_draft_id": draft_id, "caption": caption, "is_ai_generated": True},
        token="mock:orchestrator",
    )["output"]
    assert check["unverified_number_count"] >= 1 and check["verdict"] == "rejected"


def test_handoff_is_idempotent() -> None:
    _receive()
    first = _handoff()["output"]
    again = _handoff(media_kind="video")  # 두 번째 요청이 달라도 보관본 그대로
    assert again["outcome"] == "already_handed"
    assert again["output"]["sns_handoff"] == first["sns_handoff"]
    by_campaign = _call("handoff_brief_to_sns", {"campaign_id": "cmp_PROP-1"})
    assert by_campaign["outcome"] == "already_handed"
    assert by_campaign["output"]["sns_handoff"]["draft_id"] == first["sns_handoff"]["draft_id"]


def test_state_is_included_in_list_and_get() -> None:
    _receive()
    _receive("PROP-2")
    _handoff("PROP-1")
    listing = _call("list_campaign_briefs", {})["output"]["items"]
    by_id = {i["proposal_id"]: i["sns_handoff"] for i in listing}
    assert by_id["PROP-1"]["status"] == "handed"
    assert by_id["PROP-2"] == {
        "status": "not_handed",
        "draft_id": None,
        "media_request": None,
        "reason_code": None,
        "handed_at": None,
        "draft_state": None,
    }
    got = _call("get_campaign_brief", {"proposal_id": "PROP-1"})["output"]["item"]
    assert got["sns_handoff"]["draft_id"] == by_id["PROP-1"]["draft_id"]


def test_unknown_brief_fails_cleanly() -> None:
    out = _handoff("PROP-404")
    assert (out["status"], out["outcome"]) == ("failed", "brief_not_found")
    assert _call("handoff_brief_to_sns", {})["outcome"] == "invalid_input"


def test_price_table_missing_fails_closed_and_can_retry(monkeypatch) -> None:  # noqa: ANN001
    _receive()
    monkeypatch.setenv("MARKETING_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("MARKETING_IMAGE_MODEL", "no-such-model")
    out = _handoff()
    assert out["status"] == "completed" and out["outcome"] == "failed"
    state = out["output"]["sns_handoff"]
    assert state["status"] == "failed" and state["reason_code"] == "price_not_found"
    assert state["draft_id"] is None
    assert handlers.STATE.sns._conn.execute("SELECT COUNT(*) FROM sns_drafts").fetchone()[0] == 0
    listed = _call("list_campaign_briefs", {})["output"]["items"][0]["sns_handoff"]
    assert listed["status"] == "failed" and listed["reason_code"] == "price_not_found"
    monkeypatch.setenv("MARKETING_IMAGE_MODEL", "")
    assert _handoff()["output"]["sns_handoff"]["reason_code"] == "model_not_configured"
    monkeypatch.delenv("MARKETING_IMAGE_PROVIDER")  # 목 모드로 돌아오면 다시 넘길 수 있다
    monkeypatch.delenv("MARKETING_IMAGE_MODEL")
    assert _handoff()["outcome"] == "handed"


def test_forbidden_phrase_in_brief_is_failed_not_drafted() -> None:
    _receive(idea="댓글 남기면 추첨")
    out = _handoff()
    assert out["output"]["outcome"] == "failed"
    assert out["output"]["sns_handoff"]["reason_code"] == "forbidden_phrase"


def test_state_survives_restart(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(briefs.DB_ENV, str(tmp_path / "briefs.db"))
    monkeypatch.setenv(sns.DRAFT_DB_ENV, str(tmp_path / "drafts.db"))
    handlers.reset_state()
    _receive()
    first = _handoff()["output"]["sns_handoff"]
    handlers.reset_state()  # 서비스 재시작
    item = _call("get_campaign_brief", {"proposal_id": "PROP-1"})["output"]["item"]
    assert item["sns_handoff"] == first
    again = _handoff()
    assert again["outcome"] == "already_handed"
    assert again["output"]["sns_handoff"] == first


def test_missing_draft_is_restored_with_same_id(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(briefs.DB_ENV, str(tmp_path / "briefs.db"))
    handlers.reset_state()  # 초안 저장소는 메모리: 재시작하면 초안이 사라진다
    _receive()
    first = _handoff()["output"]["sns_handoff"]
    handlers.reset_state()
    seen = _call("get_campaign_brief", {"proposal_id": "PROP-1"})["output"]["item"]
    assert seen["sns_handoff"]["draft_state"] == "missing"
    again = _handoff()["output"]["sns_handoff"]
    assert again["draft_id"] == first["draft_id"] and again["draft_state"] == "caption_only"


def test_old_db_without_handoff_table_still_opens(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(briefs._SCHEMA)
    conn.commit()
    conn.close()
    store = briefs.BriefStore(str(path))
    assert store.handoff("PROP-1") is None


def test_card_restricts_requesters() -> None:
    card = {s.id: s for s in load_card().skills}
    assert card["handoff_brief_to_sns"].allowed_requesters == ["dashboard-api"]
    _receive()
    for caller in ("orchestrator", "planning", "shop-api"):
        denied = _call("handoff_brief_to_sns", {"proposal_id": "PROP-1"}, token=f"mock:{caller}")
        assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL"), caller

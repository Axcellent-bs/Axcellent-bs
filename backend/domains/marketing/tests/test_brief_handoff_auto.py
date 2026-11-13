"""`handoff_brief_to_sns`의 자동 추천(종류·필드)과 관리자 덮어쓰기 (목 모드, 외부 호출 없음)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import ExecuteDecision
from marketing_agent import handlers
from marketing_agent.main import app

CTX = "ctx_handoff_auto_0001"
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


def _receive(proposal_id: str = "PROP-5", **over) -> None:  # noqa: ANN003
    brief = {
        "schema_version": "1",
        "proposal_id": proposal_id,
        "title": "10대 그림책 릴스",
        "idea": "10대에게 인스타그램 릴스로 그림책 신간을 알린다",
        "why": "12월 그림책 검색이 늘었다",
        "facts": [],
        "target": None,
        "products": [],
        "format": "숏폼 15초",
        "approved_by": "예진",
        "note": None,
        "body_md": None,
    } | over
    out = _call("receive_campaign_brief", brief, token="mock:planning")
    assert out["output"]["outcome"] == "accepted"


def _handoff(proposal_id: str = "PROP-5", **extra) -> dict:  # noqa: ANN003
    return _call("handoff_brief_to_sns", {"proposal_id": proposal_id, **extra})


def _media(out: dict) -> dict:
    return out["output"]["sns_handoff"]["media_request"]


def test_auto_picks_kind_and_fills_fields_with_reasons(_mock_mode) -> None:  # noqa: ANN001
    _receive()
    out = _handoff(media_kind="auto")
    assert out["outcome"] == "handed"
    media = _media(out)
    assert media["media_kind"] == "video" and media["scene_template_id"] == "cat_bookstore_video"
    assert media["kind_source"] == "auto" and media["recommended_media_kind"] == "video"
    assert media["recommendation_rule"] == "format_video"
    assert "숏폼 15초" in media["recommendation_reason"]
    assert media["fields"] == {
        "season": "winter",
        "color_theme": "warm cream",
        "category": "그림책",
    }
    assert media["missing_fields"] == []
    assert media["field_sources"] == {
        "season": "brief",
        "color_theme": "default",
        "category": "brief",
    }
    assert set(media["field_reasons"]) == {"season", "color_theme", "category"}
    assert media["field_options"]["season"] == ["spring", "summer", "autumn", "winter"]
    assert media["field_options"]["category"] is None and media["field_max_len"] == {"category": 30}
    assert media["auto_generate"] is False
    draft = handlers.STATE.sns.get(out["output"]["sns_handoff"]["draft_id"])
    assert draft is not None and draft.media_kind == "reel"
    assert _mock_mode == ["receive_campaign_brief"]  # 생성·게시·새 관문 호출 없음


def test_auto_leaves_ungrounded_field_undecided() -> None:
    _receive(
        title="독서의 달 기획전",
        idea="독서 키워드 상품을 묶어 알린다",
        why="검색이 늘었다",
        format=None,
    )
    media = _media(_handoff(media_kind="auto"))
    assert media["media_kind"] == "image" and media["recommendation_rule"] == "default_image"
    assert media["recommendation_confidence"] == "low"
    assert media["missing_fields"] == ["category"] and "category" not in media["fields"]
    assert "미정" in media["field_reasons"]["category"]
    assert media["field_sources"]["season"] == "default"


def test_legacy_call_without_autofill_is_unchanged() -> None:
    _receive()
    media = _media(_handoff())
    assert media["media_kind"] == "image" and media["fields"] == {}
    assert media["missing_fields"] == ["season", "color_theme", "category"]
    assert media["kind_source"] is None and media["recommended_media_kind"] is None


def test_admin_kind_and_fields_win_over_recommendation() -> None:
    _receive()
    out = _handoff(media_kind="image", fields={"season": "summer"}, autofill=True)
    media = _media(out)
    assert media["media_kind"] == "image" and media["kind_source"] == "admin"
    assert media["recommended_media_kind"] == "video"  # 추천은 그대로 보인다
    assert media["fields"]["season"] == "summer" and media["field_sources"]["season"] == "admin"
    assert media["fields"]["category"] == "그림책" and media["field_sources"]["category"] == "brief"


def test_carousel_can_be_chosen_and_uses_image_template() -> None:
    _receive()
    out = _handoff(media_kind="carousel", autofill=True)
    media = _media(out)
    assert out["outcome"] == "handed" and media["media_kind"] == "carousel"
    assert media["scene_template_id"] == "cat_bookstore_image"
    draft = handlers.STATE.sns.get(out["output"]["sns_handoff"]["draft_id"])
    assert draft is not None and draft.media_kind == "image"  # 장이 없어 캡션 초안만


def test_revise_overrides_after_handoff_and_can_return_to_auto() -> None:
    _receive()
    first = _handoff(media_kind="auto")["output"]["sns_handoff"]
    same = _handoff(media_kind="image", fields={"season": "summer"})  # revise 없음 → 그대로
    assert same["outcome"] == "already_handed"
    assert same["output"]["sns_handoff"] == first

    revised = _handoff(
        media_kind="image", fields={"category": "에세이"}, autofill=True, revise=True
    )
    assert revised["outcome"] == "revised"
    state = revised["output"]["sns_handoff"]
    media = state["media_request"]
    assert media["media_kind"] == "image" and media["kind_source"] == "admin"
    assert media["fields"]["category"] == "에세이" and media["field_sources"]["category"] == "admin"
    assert media["fields"]["season"] == "winter"  # 안 바꾼 필드는 추천 그대로
    assert state["handed_at"] == first["handed_at"]
    assert state["draft_id"] != first["draft_id"]  # 영상 → 이미지라 초안이 다르다
    assert handlers.STATE.sns.get(state["draft_id"]).media_kind == "image"
    listed = _call("get_campaign_brief", {"proposal_id": "PROP-5"})["output"]["item"]
    assert listed["sns_handoff"] == state  # 저장됐다

    back = _handoff(media_kind="auto", revise=True)
    media = _media(back)
    assert back["outcome"] == "revised" and media["media_kind"] == "video"
    assert media["kind_source"] == "auto" and media["fields"]["category"] == "그림책"
    assert back["output"]["sns_handoff"]["draft_id"] == first["draft_id"]


def test_revise_keeps_draft_when_only_fields_change() -> None:
    _receive()
    first = _handoff(media_kind="auto")["output"]["sns_handoff"]
    out = _handoff(media_kind="video", fields={"color_theme": "deep blue"}, revise=True)
    assert out["outcome"] == "revised"
    assert out["output"]["sns_handoff"]["draft_id"] == first["draft_id"]
    assert _media(out)["fields"]["color_theme"] == "deep blue"


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"season": "monsoon"}, "value_not_allowed"),
        ({"category": "고객 문의 010-1234-5678"}, "pii"),
        ({"category": "제목 크게"}, "text_request"),
        ({"category": "디즈니"}, "real_person_or_brand"),
        ({"bgm_mood": "calm"}, "field_not_allowed"),
        ({"category": "가" * 31}, "free_text"),
    ],
)
def test_invalid_admin_field_is_rejected_and_nothing_changes(fields: dict, code: str) -> None:
    _receive()
    first = _handoff(media_kind="auto")["output"]["sns_handoff"]
    out = _handoff(media_kind="auto", fields=fields, revise=True)
    assert out["status"] == "completed" and out["outcome"] == "revise_rejected"
    assert out["output"]["reason_code"] == "invalid_field"
    assert out["output"]["field"] == next(iter(fields)) and out["output"]["field_code"] == code
    assert next(iter(fields.values())) not in str(out["output"])  # 값은 되돌려주지 않는다
    assert out["output"]["sns_handoff"] == first
    stored = _call("get_campaign_brief", {"proposal_id": "PROP-5"})["output"]["item"]
    assert stored["sns_handoff"] == first


def test_invalid_admin_field_on_first_handoff_fails_and_can_retry() -> None:
    _receive()
    out = _handoff(media_kind="auto", fields={"season": "monsoon"})
    assert out["outcome"] == "failed"
    assert out["output"]["sns_handoff"]["reason_code"] == "invalid_field"
    assert out["output"]["field"] == "season"
    assert _handoff(media_kind="auto")["outcome"] == "handed"


def test_auto_falls_back_to_image_when_video_path_is_closed(monkeypatch) -> None:  # noqa: ANN001
    _receive()
    monkeypatch.setenv("MARKETING_VIDEO_PROVIDER", "gemini")
    monkeypatch.setenv("MARKETING_VIDEO_MODEL", "")  # 영상 모델이 서버에 없다
    out = _handoff(media_kind="auto")
    assert out["outcome"] == "handed"
    media = _media(out)
    assert media["media_kind"] == "image" and media["kind_source"] == "auto_fallback"
    assert media["recommended_media_kind"] == "video"
    assert media["recommendation_blocked"] == "model_not_configured"
    assert media["fields"]["category"] == "그림책"

    # 관리자가 영상을 직접 고르면 물러나지 않고 막힌다(fail-closed). 보관본은 그대로
    before = out["output"]["sns_handoff"]
    denied = _handoff(media_kind="video", autofill=True, revise=True)
    assert denied["outcome"] == "revise_rejected"
    assert denied["output"]["reason_code"] == "model_not_configured"
    assert denied["output"]["sns_handoff"] == before


def test_admin_video_on_first_handoff_stays_fail_closed(monkeypatch) -> None:  # noqa: ANN001
    _receive()
    monkeypatch.setenv("MARKETING_VIDEO_PROVIDER", "gemini")
    monkeypatch.setenv("MARKETING_VIDEO_MODEL", "")
    out = _handoff(media_kind="video", autofill=True)
    assert out["outcome"] == "failed"
    assert out["output"]["sns_handoff"]["reason_code"] == "model_not_configured"
    assert out["output"]["sns_handoff"]["draft_id"] is None


def test_views_carry_recommendation_before_and_after_handoff() -> None:
    _receive()
    _receive("PROP-6", title="기획전", idea="신간을 알린다", why="검색이 늘었다", format=None)
    items = {i["proposal_id"]: i for i in _call("list_campaign_briefs", {})["output"]["items"]}
    rec = items["PROP-5"]["media_recommendation"]
    assert (
        rec["media_kind"] == "video" and rec["rule"] == "format_video" and rec["method"] == "rule"
    )
    assert rec["fields"]["category"] == "그림책"
    assert items["PROP-5"]["sns_handoff"]["status"] == "not_handed"  # 추천만으로는 넘기지 않는다
    other = items["PROP-6"]["media_recommendation"]
    assert other["media_kind"] == "image" and other["confidence"] == "low"
    notes = {n["field"]: n for n in other["field_notes"]}
    assert notes["category"]["value"] is None and notes["category"]["source"] == "undecided"
    _handoff(media_kind="image", autofill=True)
    got = _call("get_campaign_brief", {"proposal_id": "PROP-5"})["output"]["item"]
    assert got["media_recommendation"] == rec  # 관리자 선택과 상관없이 추천은 같다
    assert got["sns_handoff"]["media_request"]["media_kind"] == "image"


def test_legacy_handed_record_can_be_revised_to_auto() -> None:
    _receive()
    old = _handoff()["output"]["sns_handoff"]  # 옛 방식: 필드 미정 3개
    assert len(old["media_request"]["missing_fields"]) == 3
    out = _handoff(media_kind="auto", revise=True)
    assert out["outcome"] == "revised"
    assert _media(out)["media_kind"] == "video" and _media(out)["missing_fields"] == []
    assert out["output"]["sns_handoff"]["handed_at"] == old["handed_at"]


def test_input_validation() -> None:
    _receive()
    assert _handoff(media_kind="audio")["outcome"] == "invalid_input"
    assert _handoff(fields={f"f{i}": "a" for i in range(9)})["outcome"] == "invalid_input"

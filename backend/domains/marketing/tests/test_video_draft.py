"""prepare_video_draft_from_plan: 총괄이 영상 제작안(video_plan)으로 초안·장면 템플릿을 받는다."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, ExecuteDecision
from marketing_agent import handlers, video_draft
from marketing_agent.main import app
from marketing_agent.media import skill
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.prompts import TEMPLATES_PATH

CTX = "ctx_vdraft_0001"
client = TestClient(app)

PLAN = {
    "concept": "봄에 어울리는 그림책 소개 영상",
    "duration_s": 15,
    "aspect": "9:16",
    "script": "고양이가 서점을 거닐며 그림책을 꺼낸다. 마지막에 책을 펼친다.",
    # 11/22: 장면 프롬프트가 있으면 plan_story_video 경로다(test_plan_driven_video.py).
    # 이 파일은 장면이 없을 때의 예전 템플릿(cat_bookstore_video) 경로를 본다
    "scenes": [],
    "style": "flat illustration",
    "negative": "No text",
    "written_by": "rule",
    "some_future_field": {"x": 1},  # 알 수 없는 키를 받아야 한다
}
SECRET = "sk-live-ABCDEF1234567890"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "media"))
    for name in ("MARKETING_VIDEO_PROVIDER", "MARKETING_VIDEO_MODEL", "MARKETING_MEDIA_KEY_OWNER"):
        monkeypatch.delenv(name, raising=False)


def _call(input_: dict, token: str = "mock:orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "prepare_video_draft_from_plan",
        "input": input_,
    }
    return client.post("/a2a", json=body).json()


def _plan(**over) -> dict:
    return PLAN | over


def _ok(**over) -> dict:
    body = _call({"proposal_id": "PROP-1", "video_plan": _plan()} | over)
    assert body["status"] == "completed", body
    return body["output"]


def test_orchestrator_gets_draft_and_template_without_hints() -> None:
    out = _ok()
    assert out["scene_template_id"] == "cat_bookstore_video"
    assert out["media_kind"] == "video" and out["ai_generated"] is True
    assert out["post_draft_id"].startswith("spd_")
    # 힌트가 없으면 기획 글에서 규칙으로 찾은 추천값을 쓴다
    assert out["fields"]["season"] == "spring" and out["fields"]["category"] == "그림책"
    assert set(out["field_sources"].values()) == {"recommended"}
    assert out["rejected_hints"] == []
    assert out["duration_adjusted"] is None and out["aspect_adjusted"] is None
    draft = handlers.STATE.sns.get(out["post_draft_id"])
    assert draft is not None and draft.media_kind == "reel" and draft.source_ref == "PROP-1"
    assert "AI로 만들었습니다" in draft.caption and draft.caption_source["kind"] == "video_plan"
    # 같은 입력이면 같은 초안 id(멱등)
    assert _ok()["post_draft_id"] == out["post_draft_id"]


def test_media_hints_win_when_valid_and_are_tracked() -> None:
    hints = {"season": "winter", "color_theme": "deep blue", "category": "에세이"}
    out = _ok(video_plan=_plan(media_hints=hints))
    assert out["fields"] == hints
    assert out["field_sources"] == {k: "media_hints" for k in hints}


def test_rejected_or_empty_hints_fall_back_to_recommendation_names_only() -> None:
    hints = {"season": "monsoon", "color_theme": "", "category": f"문의 {SECRET} 010-1234-5678"}
    body = _call({"proposal_id": "PROP-1", "video_plan": _plan(media_hints=hints)})
    out = body["output"]
    assert body["status"] == "completed"
    assert out["rejected_hints"] == ["season", "category"]  # 이름만(빈 값은 걸린 것이 아니다)
    assert out["field_sources"]["season"] == "recommended"
    assert out["field_sources"]["color_theme"] == "recommended"
    dumped = json.dumps(body, ensure_ascii=False)
    assert "monsoon" not in dumped and SECRET not in dumped and "010-1234" not in dumped


def test_explicit_fields_override_hints_and_invalid_field_is_rejected() -> None:
    out = _ok(video_plan=_plan(media_hints={"season": "winter"}), fields={"season": "summer"})
    assert out["fields"]["season"] == "summer" and out["field_sources"]["season"] == "admin"
    bad = _call(
        {
            "proposal_id": "PROP-1",
            "video_plan": _plan(),
            "fields": {"category": SECRET + " 0101234"},
        }
    )
    assert bad["outcome"] == "invalid_field" and bad["output"]["field"] == "category"
    assert SECRET not in json.dumps(bad, ensure_ascii=False)


def test_missing_template_fields_are_not_invented() -> None:
    plan = _plan(concept="영상 하나", script="고양이가 걷는다.")  # 분류 말이 없다
    body = _call({"proposal_id": "PROP-2", "video_plan": plan})
    assert body["status"] == "failed" and body["outcome"] == "template_fields_missing"
    assert body["output"]["missing_fields"] == ["category"]
    assert body["output"]["scene_template_id"] == "cat_bookstore_video"
    assert handlers.STATE.sns.list_by_source("brief", None)[1] == 0  # 초안을 남기지 않는다
    ok = _call({"proposal_id": "PROP-2", "video_plan": plan, "fields": {"category": "그림책"}})
    assert ok["status"] == "completed"


def test_duration_and_aspect_are_reported_not_silently_changed() -> None:
    out = _ok(video_plan=_plan(duration_s=30, aspect="16:9"))
    assert out["duration_s"] == 15
    assert out["duration_adjusted"] == {"requested": 30, "used": 15}
    assert out["aspect_adjusted"] == {"requested": "16:9", "used": "9:16"}
    assert out["generate_media_input"]["duration_s"] == 15


def test_other_requesters_are_refused() -> None:
    for token in ("mock:support", "mock:planning", "mock:finance"):
        body = _call({"proposal_id": "PROP-1", "video_plan": _plan()}, token=token)
        assert body["status"] == "rejected" and "post_draft_id" not in json.dumps(body), token
        assert "post_draft_id" not in json.dumps(body)


@pytest.mark.parametrize(
    "input_",
    [
        {"video_plan": PLAN},  # proposal_id 없음
        {"proposal_id": "PROP-1"},  # video_plan 없음
        {"proposal_id": "bad id!", "video_plan": PLAN},
        {"proposal_id": "PROP-1", "video_plan": {**PLAN, "concept": ""}},
        {"proposal_id": "PROP-1", "video_plan": {**PLAN, "duration_s": "15"}},
        {"proposal_id": "PROP-1", "video_plan": PLAN, "extra": 1},
        {"proposal_id": "PROP-1", "video_plan": "plan"},
    ],
)
def test_malformed_input_is_rejected(input_: dict) -> None:
    body = _call(input_)
    assert body["status"] == "failed" and body["outcome"] == "invalid_input"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("연락은 010-1234-5678 로 주세요", "pii_in_plan"),
        ("이 영상에 댓글 남기면 선물", "forbidden_phrase"),
        ("압도적으로 최고의 책", "forbidden_claim"),
    ],
)
def test_unsafe_plan_text_is_refused_without_echo(text: str, code: str) -> None:
    body = _call({"proposal_id": "PROP-1", "video_plan": _plan(script=text)})
    assert body["outcome"] == code
    assert text not in json.dumps(body, ensure_ascii=False)


def test_no_secret_or_plan_text_in_logs_or_response(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    plan = _plan(media_hints={"season": SECRET}, some_future_field={"token": SECRET})
    body = _call({"proposal_id": "PROP-1", "video_plan": plan})
    assert SECRET not in json.dumps(body, ensure_ascii=False)
    assert SECRET not in caplog.text


# ---- 템플릿 결정 ----


def _two_video_templates(tmp_path: Path) -> Path:
    text = TEMPLATES_PATH.read_text(encoding="utf-8")
    start = text.index("[template.cat_bookstore_video]")
    end = text.index("# ---- 오디오", start)
    block = text[start:end]
    path = tmp_path / "templates.toml"
    path.write_text(
        text + "\n" + block.replace("cat_bookstore_video", "cat_bookstore_video_b"),
        encoding="utf-8",
    )
    return path


def _request(**input_) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "prepare_video_draft_from_plan",
            "input": input_,
        }
    )


def test_mapped_template_is_unambiguous_even_with_several_video_templates(tmp_path: Path) -> None:
    path = _two_video_templates(tmp_path)
    result = video_draft.prepare_video_draft(
        _request(proposal_id="PROP-1", video_plan=PLAN),
        handlers.STATE.sns,
        templates_path=path,
    )
    assert result.output["scene_template_id"] == "cat_bookstore_video"


def test_several_candidates_without_mapping_are_refused_with_candidates(tmp_path: Path) -> None:
    path = _two_video_templates(tmp_path)
    result = video_draft.prepare_video_draft(
        _request(proposal_id="PROP-1", video_plan=PLAN),
        handlers.STATE.sns,
        templates_path=path,
        scene_mapping={"video": "no_such_template"},
    )
    assert result.outcome == "scene_template_ambiguous"
    assert result.output["candidates"] == ["cat_bookstore_video", "cat_bookstore_video_b"]
    assert handlers.STATE.sns.list_by_source("brief", None)[1] == 0


# ---- generate_media 와 이어 붙이기 ----


class Gate:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append({"action": action, "params": params})
        return ExecuteDecision(execution_id="exe_v1", grade="B", status="executed")


@pytest.mark.asyncio
@pytest.mark.parametrize("hints", [None, {"season": "autumn", "color_theme": "warm cream"}])
async def test_generate_media_accepts_the_skill_output(
    monkeypatch: pytest.MonkeyPatch, hints: dict | None
) -> None:
    gate = Gate()
    monkeypatch.setattr(skill, "execute", gate)
    plan = _plan(media_hints=hints) if hints else PLAN
    prepared = _ok(video_plan=plan)
    media_input = prepared["generate_media_input"]
    request = AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": media_input,
        }
    )
    result = await skill.generate_media(
        request, ledger=MediaLedger(), from_decision=handlers._from_decision
    )
    assert result.outcome == "generated" and result.output is not None
    assert result.output["post_draft_id"] == prepared["post_draft_id"]
    assert result.output["scene_template_id"] == prepared["scene_template_id"]
    assert result.output["provider"] == "mock" and result.output["ai_generated"] is True
    assert [s["action"] for s in gate.seen] == ["generate_media"]


def test_reprepare_keeps_an_already_attached_video() -> None:
    first = _ok()
    store = handlers.STATE.sns
    draft = store.get(first["post_draft_id"])
    store.update_meta(
        [draft.model_copy(update={"asset_ref": "t/c/m/labeled.mp4", "asset_sha256": "a" * 64})]
    )
    again = _ok()
    assert again["post_draft_id"] == first["post_draft_id"]
    kept = store.get(first["post_draft_id"])
    assert kept.asset_ref == "t/c/m/labeled.mp4" and kept.asset_sha256 == "a" * 64


def test_concept_describing_sharing_is_not_engagement_bait() -> None:
    # 11/24 PROP-22: 콘셉트의 "공유할 … 밈"은 설명 문장이라 영상 초안 준비가 막히지 않는다
    concept = "이십대가 웃으며 공유할 책 읽는 일상 밈"
    body = _call({"proposal_id": "PROP-22", "video_plan": _plan(concept=concept)})
    assert body["status"] == "completed", body

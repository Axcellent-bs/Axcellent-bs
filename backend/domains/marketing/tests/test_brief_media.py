"""`prepare_brief_media`: 넘긴 기획안의 요청 초안으로 미디어 초안 만들기 + 예상 비용 조회.

외부 호출은 없다(공급자는 목, 관문은 가짜). 연결·거절·멱등 시험은 `prepare_sns_post_from_media`를
가짜로 바꿔 ffmpeg 없이 돈다. 실제 변환까지 가는 시험은 도구가 없으면 건너뛴다.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from marketing_agent import brief_media, handlers, sns_label, sns_media, sns_prepare
from marketing_agent.main import app
from marketing_agent.media import skill
from marketing_agent.sns import SnsPostDraft

CTX = "ctx_brief_media_0001"
client = TestClient(app)
FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]
ANY_FONT = next((f for f in FONTS if Path(f).is_file()), None)
needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe") and ANY_FONT),
    reason="ffmpeg·ffprobe 또는 글꼴이 없어 실제 변환 시험을 건너뜀",
)
MEDIA_ENV = (
    "MARKETING_IMAGE_PROVIDER",
    "MARKETING_IMAGE_MODEL",
    "MARKETING_VIDEO_PROVIDER",
    "MARKETING_VIDEO_MODEL",
    "MARKETING_AUDIO_MODE",
    "MARKETING_FX_USD_KRW",
    "MARKETING_FX_AS_OF",
    "MARKETING_FX_MAX_AGE_DAYS",
    "MARKETING_COST_SAFETY_MARGIN_PCT",
    "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM",
    "MARKETING_MEDIA_MONTHLY_BUDGET_KRW",
    sns_label.CONFIG_ENV,
    sns_label.FONT_ENV,
)


class Gate:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.status = "executed"

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append(action)
        extra = {}
        if self.status == "held":
            extra = {"review_id": "rev_9", "hold_reason": "over_threshold"}
        return ExecuteDecision(execution_id="exe_b1", grade="B", status=self.status, **extra)


@pytest.fixture(autouse=True)
def gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Gate:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    for name in MEDIA_ENV:
        monkeypatch.delenv(name, raising=False)
    if ANY_FONT:
        monkeypatch.setenv(sns_label.FONT_ENV, ANY_FONT)
    events.reset_mock_bus()
    handlers.reset_state()
    fake = Gate()
    monkeypatch.setattr(skill, "execute", fake)
    monkeypatch.setattr(handlers, "execute", fake)

    async def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("미디어 만들기가 게시를 불렀다")

    monkeypatch.setattr(handlers, "publish_sns", forbidden)
    return fake


def _call(skill_id: str, input: dict, token: str = "mock:dashboard-api", ctx: str = CTX) -> dict:
    body = {
        "meta": {
            "context_id": ctx,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill_id,
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


NO_CATEGORY = {
    "title": "10대 신간 릴스",
    "idea": "10대에게 인스타그램 릴스로 신간을 알린다",
    "why": "12월 검색이 늘었다",
}  # 책 분류 근거가 없어 category가 미정으로 남는다


def _handed(
    kind: str = "auto",
    proposal_id: str = "PROP-5",
    brief: dict | None = None,
    **extra,  # noqa: ANN003
) -> dict:
    _receive(proposal_id, **(brief or {}))
    out = _call("handoff_brief_to_sns", {"proposal_id": proposal_id, "media_kind": kind, **extra})
    assert out["outcome"] == "handed", out
    return out["output"]["sns_handoff"]


def _make(proposal_id: str = "PROP-5", ctx: str = CTX) -> dict:
    return _call("prepare_brief_media", {"proposal_id": proposal_id}, ctx=ctx)


def _view(proposal_id: str = "PROP-5") -> dict:
    out = _call("get_campaign_brief", {"proposal_id": proposal_id})
    return out["output"]["item"]["sns_handoff"]


class FakePrepare:
    """`prepare_sns_post_from_media` 자리. 받은 요청을 기록하고 준비된 초안을 흉내 낸다."""

    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []
        self.stage = "draft_ready"

    async def __call__(self, request: AgentRequest, **deps) -> AgentResult:  # noqa: ANN003
        self.requests.append(request)
        reel = request.input["media_kind"] == "video"
        draft = SnsPostDraft(
            post_draft_id="spd_fake0001",
            source_kind="brief",
            source_ref=request.input["proposal_id"],
            platform="instagram",
            media_kind="reel" if reel else "image",
            caption="캡션",
            hashtags=[],
            caption_sha256="c" * 64,
            required_text=[],
            context_id=request.meta.context_id,
            asset_ref="t/ctx/med/labeled.mp4" if reel else "t/ctx/med/labeled.jpg",
            asset_sha256="a" * 64,
        )
        deps["store"].save(draft)
        ready = self.stage == "draft_ready"
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome=None if ready else "rejected_by_check",
            output={
                "stage": self.stage,
                "post_draft_id": draft.post_draft_id,
                "media_kind": draft.media_kind,
                "asset_ref": draft.asset_ref,
                "asset_sha256": draft.asset_sha256,
                "generation": {"cost_krw": 1000, "total_cost_krw": 1000},
                "label": {"text": "AI 생성 영상"},
                "check": {"verdict": "approved" if ready else "rejected", "publishable": ready},
                "publish_input": {"post_draft_id": draft.post_draft_id},
            },
        )


@pytest.fixture
def fake_prepare(monkeypatch: pytest.MonkeyPatch) -> FakePrepare:
    fake = FakePrepare()
    original = brief_media.prepare_brief_media

    async def wired(request, **deps):  # noqa: ANN001, ANN003
        return await original(request, **deps, prepare=fake)

    monkeypatch.setattr(brief_media, "prepare_brief_media", wired)
    return fake


# --- 연결: 보관된 요청 초안 그대로 넘긴다 ----------------------------------------------


def test_video_request_goes_to_prepare_with_stored_kind_and_fields(
    fake_prepare: FakePrepare, gate: Gate
) -> None:
    stored = _handed(fields={"category": "그림책"})["media_request"]
    assert stored["media_kind"] == "video" and not stored["missing_fields"]
    out = _make()
    assert out["status"] == "completed" and out["output"]["stage"] == "draft_ready", out
    sent = fake_prepare.requests[0]
    assert sent.skill == "prepare_sns_post_from_media"
    assert sent.input == {
        "proposal_id": "PROP-5",
        "media_ref": stored["media_ref"],
        "media_kind": "video",
        "scene_template_id": stored["scene_template_id"],
        "fields": stored["fields"],
        "resolution": stored["resolution"],
        "duration_s": stored["duration_s"],
    }
    assert sent.meta.context_id == "ctx_brief_PROP-5"  # 호출자와 무관하게 같은 장부 키
    assert out["meta"]["context_id"] == CTX  # 결과는 받은 요청의 메타로 돌려준다
    assert "publish_sns" not in gate.seen


def test_image_request_sends_quality_not_duration(fake_prepare: FakePrepare) -> None:
    stored = _handed("image", fields={"category": "에세이"})["media_request"]
    assert _make()["output"]["stage"] == "draft_ready"
    sent = fake_prepare.requests[0].input
    assert sent["media_kind"] == "image" and sent["quality"] == stored["quality"]
    assert "duration_s" not in sent


def test_result_is_recorded_and_shown_in_the_view(fake_prepare: FakePrepare) -> None:
    _handed(fields={"category": "그림책"})
    assert _view()["prepared"] is None
    out = _make()
    prepared = _view()["prepared"]
    assert prepared == out["output"]["sns_handoff"]["prepared"]
    assert prepared["stage"] == "draft_ready" and prepared["post_draft_id"] == "spd_fake0001"
    assert prepared["media_kind"] == "reel" and prepared["cost_krw"] == 1000
    assert prepared["label_text"] == "AI 생성 영상" and prepared["check"]["publishable"] is True
    assert "request_sha" not in prepared
    assert _view()["draft_state"] == "caption_only"  # 넘길 때 만든 캡션 초안은 그대로다


def test_second_click_does_not_generate_again(fake_prepare: FakePrepare) -> None:
    _handed(fields={"category": "그림책"})
    _make()
    again = _make(ctx="ctx_other_caller")
    assert again["outcome"] == "already_prepared" and again["output"]["stage"] == "draft_ready"
    assert len(fake_prepare.requests) == 1


def test_changed_request_hides_old_media_and_makes_new(fake_prepare: FakePrepare) -> None:
    _handed(fields={"category": "그림책"})
    _make()
    change = {"media_kind": "auto", "fields": {"category": "에세이"}, "revise": True}
    revised = _call("handoff_brief_to_sns", {"proposal_id": "PROP-5"} | change)
    assert revised["outcome"] == "revised"
    assert _view()["prepared"] is None  # 옛 필드로 만든 미디어는 보여 주지 않는다
    assert _make()["outcome"] is None and len(fake_prepare.requests) == 2
    assert fake_prepare.requests[1].input["fields"]["category"] == "에세이"


def test_check_rejected_is_recorded_but_not_ready(fake_prepare: FakePrepare) -> None:
    fake_prepare.stage = "check_rejected"
    _handed(fields={"category": "그림책"})
    out = _make()
    assert out["outcome"] == "rejected_by_check"
    assert _view()["prepared"]["stage"] == "check_rejected"
    _make()
    assert len(fake_prepare.requests) == 2  # 점검에 걸린 초안은 "이미 준비됨"으로 치지 않는다


# --- 비용 전에 막는 것 -----------------------------------------------------------------


def test_refusals_happen_before_any_generation(fake_prepare: FakePrepare, gate: Gate) -> None:
    assert _make("PROP-none")["outcome"] == "brief_not_found"
    assert _call("prepare_brief_media", {"proposal_id": "a b"})["outcome"] == "invalid_input"
    _receive("PROP-6")
    assert _make("PROP-6")["outcome"] == "not_handed"
    _handed(brief=NO_CATEGORY)
    assert _view()["media_request"]["missing_fields"] == ["category"]
    assert _make()["outcome"] == "fields_missing"
    _handed("carousel", "PROP-7", fields={"category": "그림책"})
    assert _make("PROP-7")["outcome"] == "carousel_items_undecided"
    assert fake_prepare.requests == [] and "generate_media" not in gate.seen


def test_bgm_mode_video_is_refused_before_cost(
    fake_prepare: FakePrepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    _handed(fields={"category": "그림책"})
    monkeypatch.setenv("MARKETING_AUDIO_MODE", "bgm_overlay")
    assert _make()["outcome"] == "bgm_fields_required"
    assert _view()["media_estimate"]["reason_code"] == "bgm_fields_required"
    assert fake_prepare.requests == []


def test_only_dashboard_api_may_call() -> None:
    out = _call("prepare_brief_media", {"proposal_id": "PROP-5"}, token="mock:planning")
    assert out["status"] == "rejected"


# --- 실제 흐름(목 공급자): 안전 관문·장부 ------------------------------------------------


def test_held_by_gate_returns_review_and_records_nothing(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sns_prepare, "preflight_tools", lambda: None)
    _handed(fields={"category": "그림책"})
    gate.status = "held"
    out = _make()
    assert out["status"] == "input-required" and out["outcome"] == "held", out
    assert out["output"]["review_id"] == "rev_9" and out["output"]["stage"] == "generate_media"
    assert "generate_media" in gate.seen
    assert _view()["prepared"] is None


@needs_ffmpeg
def test_image_end_to_end_with_mock_provider(gate: Gate) -> None:
    _handed("image", fields={"category": "그림책"})
    before = _view()["media_estimate"]
    assert before["status"] == "ok" and before["settled"] is False
    out = _make()
    assert out["status"] == "completed", out
    result = out["output"]
    assert result["stage"] in ("draft_ready", "check_rejected")
    assert result["label"]["labeled_file"] == "labeled.jpg"
    assert "10대 그림책 릴스" in result["caption"]
    assert "generate_media" in gate.seen and "publish_sns" not in gate.seen
    after = _view()
    assert after["prepared"]["post_draft_id"] == result["post_draft_id"]
    assert after["media_estimate"]["settled"] is True  # 다시 눌러도 비용이 나지 않는다
    assert after["media_estimate"]["basis"] == "ledger"


# --- 예상 비용(조회 때 계산) -------------------------------------------------------------


def test_estimate_for_mock_provider_reads_fixed_cost_and_month_total() -> None:
    estimate = _handed(fields={"category": "그림책"})["media_estimate"]
    assert estimate == {
        "status": "ok",
        "reason_code": None,
        "media_kind": "video",
        "cost_krw": skill.MOCK_COST_KRW["video"],
        "settled": False,
        "basis": "mock",
        "per_item_limit_krw": None,
        "monthly_budget_krw": None,
        "month_total_krw": 0,
    }
    assert _view()["media_estimate"] == estimate


def test_estimate_reads_price_table_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    _handed(fields={"category": "그림책"})
    settings = {
        "MARKETING_VIDEO_PROVIDER": "gemini",
        "MARKETING_VIDEO_MODEL": "gemini-omni-1.2-flash",
        "MARKETING_FX_USD_KRW": "1400",
        "MARKETING_FX_AS_OF": datetime.now(UTC).date().isoformat(),
        "MARKETING_FX_MAX_AGE_DAYS": "7",
        "MARKETING_COST_SAFETY_MARGIN_PCT": "5",
        "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM": "2000",
        "MARKETING_MEDIA_MONTHLY_BUDGET_KRW": "30000",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    estimate = _view()["media_estimate"]
    assert estimate["status"] == "ok" and estimate["basis"] == "price_table", estimate
    assert 0 < estimate["cost_krw"] <= 2000
    assert estimate["per_item_limit_krw"] == 2000 and estimate["monthly_budget_krw"] == 30000

    monkeypatch.setenv("MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM", "100")
    over = _view()["media_estimate"]
    assert over["status"] == "blocked" and over["reason_code"] == "cost_over_single"
    assert over["cost_krw"] == estimate["cost_krw"]

    monkeypatch.setenv("MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM", "2000")
    monkeypatch.setenv("MARKETING_MEDIA_MONTHLY_BUDGET_KRW", "500")
    assert _view()["media_estimate"]["reason_code"] == "budget_exceeded"

    monkeypatch.setenv("MARKETING_VIDEO_MODEL", "no-such-model")
    unknown = _view()["media_estimate"]
    assert unknown["reason_code"] == "price_not_found" and unknown["cost_krw"] is None


def test_estimate_is_blocked_for_missing_fields_and_carousel() -> None:
    assert _handed(brief=NO_CATEGORY)["media_estimate"]["reason_code"] == "fields_missing"
    carousel = _handed("carousel", "PROP-7", fields={"category": "그림책"})["media_estimate"]
    assert carousel["status"] == "blocked"
    assert carousel["reason_code"] == "carousel_items_undecided" and carousel["cost_krw"] is None
    _receive("PROP-8")
    assert _view("PROP-8")["media_estimate"] is None  # 넘기지 않은 기획안에는 없다

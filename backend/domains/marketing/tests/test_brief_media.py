"""`prepare_brief_media`: 넘긴 기획안의 요청 초안으로 미디어 초안 만들기 + 예상 사용량 조회.

외부 호출은 없다(공급자는 목, 관문은 가짜). 연결·거절·멱등 시험은 `prepare_sns_post_from_media`를
가짜로 바꿔 ffmpeg 없이 돈다. 실제 변환까지 가는 시험은 도구가 없으면 건너뛴다.
"""

import hashlib
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
    "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY",
    "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY",
    "MARKETING_QUOTA_AUDIO_COUNT_MONTHLY",
    "MARKETING_MEDIA_KEY_OWNER",
    sns_label.CONFIG_ENV,
    sns_label.FONT_ENV,
)


VIDEO_USAGE = {"unit": "seconds", "amount": 15, "input_tokens": None, "output_tokens": None}
BGM_USAGE = {"unit": "tracks", "amount": 1, "input_tokens": None, "output_tokens": None}


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
        self.asset_sha = "a" * 64

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
            caption_sha256=hashlib.sha256("캡션".encode()).hexdigest(),
            required_text=[],
            context_id=request.meta.context_id,
            asset_ref="t/ctx/med/labeled.mp4" if reel else "t/ctx/med/labeled.jpg",
            asset_sha256=self.asset_sha,
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
                "generation": {"usage": VIDEO_USAGE},
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
        # 영상은 기획안의 구조화 필드(plan_*)가 같이 간다. 요청 초안(stored)은 그대로다
        "fields": stored["fields"] | sent.input["fields"],
        "resolution": stored["resolution"],
        "duration_s": stored["duration_s"],
    }
    plan = {k: v for k, v in sent.input["fields"].items() if k.startswith("plan_")}
    assert set(plan) == {"plan_idea", "plan_why"}  # 이 기획안에는 이 두 칸만 있다
    assert "body_md" not in plan and "title" not in plan
    assert sent.meta.context_id == "ctx_brief_PROP-5"  # 호출자와 무관하게 같은 장부 키
    assert out["meta"]["context_id"] == CTX  # 결과는 받은 요청의 메타로 돌려준다
    assert "publish_sns" not in gate.seen


def test_image_request_gets_no_plan_fields(
    fake_prepare: FakePrepare,
) -> None:
    _handed("image", fields={"category": "에세이"})
    _make()
    assert not any(k.startswith("plan_") for k in fake_prepare.requests[0].input["fields"])


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
    assert prepared["media_kind"] == "reel" and prepared["usage"] == VIDEO_USAGE
    assert "cost_krw" not in prepared  # 원화는 더 기록하지 않는다
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


# --- 예상 사용량(조회 때 계산) -----------------------------------------------------------


def test_estimate_for_mock_provider_reads_usage_and_month_usage() -> None:
    estimate = _handed(fields={"category": "그림책"})["media_estimate"]
    video = estimate.pop("video")  # 길이 입력용 정보(상한은 설정값, 기본 15초)
    assert video["max_s"] == 15 and video["absolute_max_s"] == 90 and video["current_s"] == 15
    assert video["warn_duration_s"] == 15  # 기본 30이지만 운영 상한(15)을 넘지 않는다
    assert video["clips_by_duration"]["15"] == 1
    assert "cost_by_duration" not in video  # 길이별 원화 표는 없다
    assert estimate == {
        "status": "ok",
        "reason_code": None,
        "media_kind": "video",
        "usage": VIDEO_USAGE,
        "settled": False,
        "basis": "mock",
        "quota": {
            "unit": "seconds",
            "used": 0,
            "limit": None,
            "remaining_pct": None,
            "applies": True,
        },
    }
    assert {k: v for k, v in _view()["media_estimate"].items() if k != "video"} == estimate


def test_estimate_reads_usage_and_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    _handed(fields={"category": "그림책"})
    settings = {
        "MARKETING_VIDEO_PROVIDER": "gemini",
        "MARKETING_VIDEO_MODEL": "gemini-omni-1.2-flash",
        "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY": "40",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    estimate = _view()["media_estimate"]
    assert estimate["status"] == "ok" and estimate["basis"] == "estimate", estimate
    assert estimate["usage"]["unit"] == "seconds" and estimate["usage"]["amount"] == 15
    assert estimate["quota"] == {
        "unit": "seconds",
        "used": 0,
        "limit": 40,
        "remaining_pct": 100,
        "applies": True,
    }

    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "10")
    over = _view()["media_estimate"]
    assert over["status"] == "blocked" and over["reason_code"] == "quota_exceeded"
    assert over["usage"]["amount"] == 15  # 막혀도 예상 사용량은 보여 준다
    # 예산에 걸려도 다른 길이를 고를 수 있게 길이별 클립 수 표는 준다
    assert over["video"]["clips_by_duration"]["15"] == 1
    assert over["video"]["max_s"] == 15 and "16" not in over["video"]["clips_by_duration"]

    monkeypatch.setenv("MARKETING_MEDIA_KEY_OWNER", "customer")  # 고객사 키는 예산이 없다
    free = _view()["media_estimate"]
    assert free["status"] == "ok" and free["quota"]["applies"] is False
    assert free["quota"]["limit"] is None and free["quota"]["remaining_pct"] is None
    monkeypatch.delenv("MARKETING_MEDIA_KEY_OWNER")
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "40")

    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "90")
    video90 = _view()["media_estimate"]["video"]
    # 경고창용 정보: 경고 길이(기본 30)와 길이별 클립 수
    assert video90["warn_duration_s"] == 30
    clips = video90["clips_by_duration"]
    assert clips["15"] == 1 and clips["90"] == 7
    assert clips["30"] >= 2 and clips["90"] > clips["30"]
    monkeypatch.setenv("MARKETING_VIDEO_WARN_DURATION_S", "45")
    assert _view()["media_estimate"]["video"]["warn_duration_s"] == 45
    monkeypatch.setenv("MARKETING_VIDEO_WARN_DURATION_S", "100")  # 상한 밖 -> 기본값
    assert _view()["media_estimate"]["video"]["warn_duration_s"] == 30
    monkeypatch.delenv("MARKETING_VIDEO_WARN_DURATION_S")
    monkeypatch.delenv("MARKETING_VIDEO_MAX_DURATION_S")

    monkeypatch.delenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY")  # 우리 키인데 예산이 없다
    unset = _view()["media_estimate"]
    assert unset["reason_code"] == "quota_not_configured" and unset["usage"] is None

    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "40")
    monkeypatch.setenv("MARKETING_VIDEO_MODEL", "")
    missing = _view()["media_estimate"]
    assert missing["reason_code"] == "model_not_configured" and missing["usage"] is None


def test_estimate_is_blocked_for_missing_fields_and_carousel() -> None:
    assert _handed(brief=NO_CATEGORY)["media_estimate"]["reason_code"] == "fields_missing"
    carousel = _handed("carousel", "PROP-7", fields={"category": "그림책"})["media_estimate"]
    assert carousel["status"] == "blocked"
    assert carousel["reason_code"] == "carousel_items_undecided" and carousel["usage"] is None
    _receive("PROP-8")
    assert _view("PROP-8")["media_estimate"] is None  # 넘기지 않은 기획안에는 없다


# --- 사람이 고른 영상 길이 ----------------------------------------------------------------


def test_chosen_duration_is_saved_in_the_request_and_sent_to_generation(
    fake_prepare: FakePrepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    _handed(fields={"category": "그림책"})
    out = _call("prepare_brief_media", {"proposal_id": "PROP-5", "duration_s": 45})
    assert out["output"]["stage"] == "draft_ready", out
    assert fake_prepare.requests[0].input["duration_s"] == 45
    assert _view()["media_request"]["duration_s"] == 45  # 이후 비용 조회·해시도 이 길이를 따른다


def test_confirmed_long_is_only_an_audit_note_and_never_a_gate(
    fake_prepare: FakePrepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    _handed(fields={"category": "그림책"})
    # 확인했다는 주장과 무관하게 상한 가드는 같다: 넘으면 거절, 안이면 만든다
    over = _call(
        "prepare_brief_media", {"proposal_id": "PROP-5", "duration_s": 61, "confirmed_long": True}
    )
    assert (
        over["outcome"] in ("duration_over_limit", "invalid_input") and fake_prepare.requests == []
    )
    out = _call(
        "prepare_brief_media", {"proposal_id": "PROP-5", "duration_s": 45, "confirmed_long": True}
    )
    assert out["output"]["stage"] == "draft_ready", out
    assert (
        "confirmed_long" not in fake_prepare.requests[0].input
    )  # 생성 입력(비용 키)에 안 들어간다
    assert _view()["prepared"]["confirmed_long"] is True
    bad = _call("prepare_brief_media", {"proposal_id": "PROP-5", "confirmed_long": "yes"})
    assert bad["outcome"] == "invalid_input"


def test_duration_over_the_setting_is_refused_before_generation(
    fake_prepare: FakePrepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    _handed(fields={"category": "그림책"})
    out = _call("prepare_brief_media", {"proposal_id": "PROP-5", "duration_s": 16})  # 기본 상한 15
    assert out["outcome"] == "duration_over_limit" and out["output"]["limit_s"] == 15
    assert fake_prepare.requests == []
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "90")
    over = _call("prepare_brief_media", {"proposal_id": "PROP-5", "duration_s": 91})
    assert over["outcome"] == "invalid_input" and fake_prepare.requests == []  # 절대 상한 90초


def test_duration_is_video_only(fake_prepare: FakePrepare) -> None:
    _handed("image", fields={"category": "에세이"})
    out = _call("prepare_brief_media", {"proposal_id": "PROP-5", "duration_s": 5})
    assert out["outcome"] == "invalid_input" and fake_prepare.requests == []


def test_invalid_max_setting_blocks_the_estimate_and_the_make(
    fake_prepare: FakePrepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "120")
    estimate = _handed(fields={"category": "그림책"})["media_estimate"]
    assert (
        estimate["status"] == "blocked" and estimate["reason_code"] == "video_max_duration_invalid"
    )
    assert _make()["outcome"] == "video_max_duration_invalid" and fake_prepare.requests == []


# --- 영상 소리 선택: 이 영상 그대로 사용 / 배경음악 따로 만들기 ---------------------------------


class FakeAudioPrepare(FakePrepare):
    """소리가 든 영상을 만든 것처럼 흉내 낸다. BGM 호출(`bgm_overlay`)이면 BGM 증빙을 더한다."""

    def __init__(self) -> None:
        super().__init__()
        self.settings: list = []
        self.bgm_outcome: str | None = None  # 값이 있으면 BGM 단계가 그 결과로 막힌다
        self.first_audio_mode = "source"
        self.first_has_audio = True

    async def __call__(self, request: AgentRequest, **deps) -> AgentResult:  # noqa: ANN003
        self.settings.append(deps.get("settings"))
        bgm = "bgm_scene_template_id" in request.input
        if bgm and self.bgm_outcome:
            self.requests.append(request)
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.INPUT_REQUIRED,
                reason_code="human_input",
                outcome=self.bgm_outcome,
                output={"stage": "generate_media", "review_id": "rev_9"},
            )
        if bgm:
            self.asset_sha = "b" * 64  # BGM을 입히면 파일이 바뀐다(초안도 새 파일로 다시 만든다)
        result = await super().__call__(request, **deps)
        output = dict(result.output or {})
        generation = {
            "usage": VIDEO_USAGE,
            "audio_mode": "bgm_overlay" if bgm else self.first_audio_mode,
            "has_audio": True if bgm else self.first_has_audio,
            "muted": False,
        }
        if bgm:
            generation["bgm"] = {
                "provider": "mock",
                "model_id": "mock-media",
                "usage": BGM_USAGE,
                "execution_id": "exe_bgm",
                "prompt_sha256": "p" * 64,
                "media_id": "med_bgm",
            }
        output["generation"] = generation
        return result.model_copy(update={"output": output})


@pytest.fixture
def audio_prepare(monkeypatch: pytest.MonkeyPatch) -> FakeAudioPrepare:
    fake = FakeAudioPrepare()
    original_prepare = brief_media.prepare_brief_media
    original_choose = brief_media.choose_brief_audio

    async def wired(request, **deps):  # noqa: ANN001, ANN003
        return await original_prepare(request, **deps, prepare=fake)

    async def wired_choose(request, **deps):  # noqa: ANN001, ANN003
        return await original_choose(request, **deps, prepare=fake)

    monkeypatch.setattr(brief_media, "prepare_brief_media", wired)
    monkeypatch.setattr(brief_media, "choose_brief_audio", wired_choose)
    return fake


def _choose(choice: str, **extra) -> dict:  # noqa: ANN003
    return _call("choose_brief_audio", {"proposal_id": "PROP-5", "choice": choice} | extra)


def _ready_video() -> dict:
    _handed(fields={"category": "그림책"})
    out = _make()
    assert out["output"]["stage"] == "draft_ready", out
    return out["output"]["sns_handoff"]


def test_provider_audio_video_waits_for_a_decision_and_shows_bgm_usage(
    audio_prepare: FakeAudioPrepare,
) -> None:
    state = _ready_video()
    audio = state["prepared"]["audio"]
    assert audio == {
        "mode": "source",
        "has_source_audio": True,
        "note": None,
        "needs_decision": True,
        "decision": None,
    }
    assert state["media_estimate"]["bgm"]["usage"] == BGM_USAGE
    assert "cost_krw" not in state["media_estimate"]["bgm"]
    # 듣고 정하기 전에는 게시 요청을 만들 수 없다
    assert state["publish"]["reason_code"] == "audio_decision_required"
    assert state["publish"]["can_request"] is False


def test_keep_records_the_decision_once_and_unlocks_publish_request(
    audio_prepare: FakeAudioPrepare, gate: Gate
) -> None:
    _ready_video()
    out = _choose("keep", confirmed_listened=True)
    assert out["outcome"] == "audio_kept", out
    decision = out["output"]["audio_decision"]
    assert decision["choice"] == "keep" and decision["had_source_audio"] is True
    assert decision["confirmed_listened"] is True and decision["decided_at"].endswith("Z")
    assert len(audio_prepare.requests) == 1  # 아무것도 다시 만들지 않는다
    assert "generate_media" not in gate.seen  # 생성 관문(BGM 포함)을 부르지 않는다
    state = _view()
    assert state["prepared"]["audio"]["decision"] == decision
    assert state["publish"]["reason_code"] is None and state["publish"]["can_request"] is True
    assert state["media_estimate"].get("bgm") is None  # 정한 뒤에는 BGM 사용량 안내가 없다
    again = _choose("bgm")
    assert again["outcome"] == "audio_already_decided" and len(audio_prepare.requests) == 1


def test_bgm_choice_reruns_with_overlay_settings_and_records_audit(
    audio_prepare: FakeAudioPrepare,
) -> None:
    _ready_video()
    out = _choose("bgm", bgm_mood="warm")
    assert out["status"] == "completed" and out["output"]["stage"] == "draft_ready", out
    sent, settings = audio_prepare.requests[1], audio_prepare.settings[1]
    assert settings.audio_mode == "bgm_overlay"  # 이때만 BGM 경로를 연다
    assert sent.input["bgm_scene_template_id"] == brief_media.BGM_TEMPLATE
    assert sent.input["bgm_fields"] == {"bgm_mood": "warm", "bgm_genre": "lo-fi"}
    assert audio_prepare.requests[0].input["media_ref"] == sent.input["media_ref"]  # 영상은 재생
    prepared = _view()["prepared"]
    decision = prepared["audio"]["decision"]
    assert decision["choice"] == "bgm" and decision["bgm_usage"] == BGM_USAGE
    assert decision["bgm_execution_id"] == "exe_bgm" and decision["bgm_prompt_sha256"] == "p" * 64
    assert decision["previous_asset_sha256"] == "a" * 64 and prepared["asset_sha256"] == "b" * 64
    assert prepared["usage"] == VIDEO_USAGE and prepared["audio"]["needs_decision"] is False
    assert _view()["publish"]["reason_code"] is None  # 결정이 있으니 게시 요청 가능


def test_bgm_held_by_the_gate_records_no_decision(audio_prepare: FakeAudioPrepare) -> None:
    _ready_video()
    audio_prepare.bgm_outcome = "bgm_held"
    out = _choose("bgm")
    assert out["outcome"] == "bgm_held" and out["output"]["review_id"] == "rev_9"
    assert _view()["prepared"]["audio"]["decision"] is None  # 영상은 그대로, 다시 고를 수 있다
    audio_prepare.bgm_outcome = None
    assert _choose("bgm")["output"]["stage"] == "draft_ready"


def test_choice_is_refused_after_a_publish_request(
    audio_prepare: FakeAudioPrepare, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready_video()
    monkeypatch.setattr(handlers, "_publish_status", lambda record: "held")
    out = _choose("keep")
    assert out["outcome"] == "publish_already_requested"
    assert _view()["prepared"]["audio"]["decision"] is None
    assert len(audio_prepare.requests) == 1


def test_choice_needs_a_prepared_video(audio_prepare: FakeAudioPrepare) -> None:
    _handed(fields={"category": "그림책"})
    assert _choose("keep")["outcome"] == "not_prepared"
    bad = _call("choose_brief_audio", {"proposal_id": "PROP-5", "choice": "mix"})
    assert bad["outcome"] == "invalid_input"


def test_image_has_no_audio_choice(fake_prepare: FakePrepare) -> None:
    _handed("image", fields={"category": "에세이"})
    _make()
    assert _view()["prepared"]["audio"] is None
    assert _choose("keep")["outcome"] == "not_video"


def test_silent_video_needs_no_decision_but_can_still_get_bgm(
    audio_prepare: FakeAudioPrepare,
) -> None:
    """소리 없는 공급자·모델(또는 이어 붙인 영상)은 게시 조건이 아니고, BGM 선택은 열려 있다."""
    audio_prepare.first_has_audio = False
    state = _ready_video()
    audio = state["prepared"]["audio"]
    assert audio["needs_decision"] is False and audio["has_source_audio"] is False
    assert state["publish"]["reason_code"] is None
    assert _choose("bgm")["output"]["stage"] == "draft_ready"


def test_legacy_off_mode_video_cannot_use_bgm_choice(audio_prepare: FakeAudioPrepare) -> None:
    """off 모드 영상은 장부 키가 달라 BGM을 따로 만들면 영상을 다시 만든다. 막는다."""
    audio_prepare.first_audio_mode = "off"
    audio_prepare.first_has_audio = False
    _ready_video()
    audio = _view()["prepared"]["audio"]
    assert audio["mode"] == "off" and audio["needs_decision"] is False
    out = _choose("bgm")
    assert (
        out["outcome"] == "bgm_unavailable" and out["output"]["reason"] == "audio_mode_not_source"
    )
    assert len(audio_prepare.requests) == 1

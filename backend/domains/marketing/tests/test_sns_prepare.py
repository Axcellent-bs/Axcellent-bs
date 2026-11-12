"""prepare_sns_post_from_media: 생성 → 규격 변환 → AI 라벨 → 초안·점검 연결.

네트워크 호출은 없다(공급자는 목, 관문은 가짜). ffmpeg·ffprobe·글꼴이 있어야 도는 시험은
도구가 없으면 사유를 밝히고 건너뛴다. 계산·거절·관문 통과 시험은 도구 없이도 돈다.
"""

import dataclasses
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import ExecuteDecision
from marketing_agent import handlers, sns, sns_label, sns_media, sns_prepare
from marketing_agent.main import app, load_card
from marketing_agent.media import registry, skill
from marketing_agent.media.base import MediaRequest, MediaResult
from marketing_agent.media.mock import MODEL_ID

CTX = "ctx_prep_0001"
client = TestClient(app)
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
ANY_FONT = next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)
needs_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and ANY_FONT), reason="ffmpeg·ffprobe 또는 글꼴이 없어 실제 변환 시험을 건너뜀"
)
FIELDS = {"season": "spring", "color_theme": "soft pastel", "category": "그림책"}


class Gate:
    def __init__(self, **fields: str) -> None:
        self.fields = {"execution_id": "exe_p1", "grade": "B", "status": "executed"} | fields
        self.seen: list[str] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append(action)
        return ExecuteDecision(**self.fields)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    for name in (
        "MARKETING_IMAGE_PROVIDER",
        "MARKETING_VIDEO_PROVIDER",
        "MARKETING_AUDIO_MODE",
        sns_label.CONFIG_ENV,
        sns_label.FONT_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    if ANY_FONT:
        monkeypatch.setenv(sns_label.FONT_ENV, ANY_FONT)
    events.reset_mock_bus()
    handlers.reset_state()


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Gate:
    fake = Gate()
    monkeypatch.setattr(skill, "execute", fake)
    return fake


@pytest.fixture
def no_tools_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sns_prepare, "preflight_tools", lambda: None)


def _coupon() -> dict:
    return {
        "campaign_id": "cmp_1",
        "unit_value": 2000,
        "constraints": [
            {"code": code, "text": f"{label} 안내 {i}"}
            for i, (code, label) in enumerate(sns.CONSTRAINT_LABELS.items(), 1)
        ],
    }


def _call(input: dict) -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        },
        "skill": "prepare_sns_post_from_media",
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _image_input(**over) -> dict:
    return {
        "coupon": _coupon(),
        "media_kind": "image",
        "scene_template_id": "cat_bookstore_image",
        "fields": FIELDS,
    } | over


def _video_input(**over) -> dict:
    return _image_input(media_kind="video", scene_template_id="cat_bookstore_video") | over


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)  # noqa: S603, S607


class _SourceProvider:
    """목 공급자 대신 실제로 열리는 파일을 쓴다(합성 이미지·영상)."""

    name = "mock"

    def __init__(self, kind: str, args: list[str], suffix: str) -> None:
        self.kind, self.args, self.suffix = kind, args, suffix

    def generate(self, request: MediaRequest) -> MediaResult:
        path = request.output_dir / f"asset.{self.suffix}"
        _ffmpeg(*self.args, str(path))
        data = path.read_bytes()
        return MediaResult(
            asset_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            provider="mock",
            model_id=MODEL_ID,
            usage=None,
            generated_at=datetime.now(UTC),
            provider_job_id="mock_job",
        )


def _use_source(monkeypatch: pytest.MonkeyPatch, provider: _SourceProvider) -> None:
    monkeypatch.setitem(registry.ADAPTERS, "mock", lambda: provider)


# --- 규격 계산 (도구 없이) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected", "cropped"),
    [
        ((1, 1), (320, 320), False),  # 목 PNG(1x1)는 너비 하한으로 키운다
        ((1024, 1024), (1024, 1024), False),  # 규격 안이면 그대로
        ((768, 1376), (768, 960), True),  # Gemini 9:16 이미지 -> 4:5로 자른다
        ((3000, 3000), (1440, 1440), False),  # 너비 상한
        ((2000, 500), (955, 500), True),  # 너무 가로로 긴 것 -> 1.91:1
        ((200, 1000), (320, 400), True),
    ],
)
def test_plan_image_fits_instagram_spec(size, expected, cropped) -> None:
    plan = sns_prepare.plan_image(*size)
    assert plan.size == expected
    assert (plan.crop is not None) is cropped
    width, height = plan.size
    assert sns_media.IMAGE_WIDTH_MIN <= width <= sns_media.IMAGE_WIDTH_MAX
    assert sns_media.IMAGE_ASPECT_MIN <= width / height <= sns_media.IMAGE_ASPECT_MAX


def _probe(width=720, height=1280, fps="24/1", duration="8.0") -> dict:
    return {
        "format": {"duration": duration},
        "streams": [
            {
                "codec_type": "video",
                "width": width,
                "height": height,
                "avg_frame_rate": fps,
                "r_frame_rate": fps,
            }
        ],
    }


def test_plan_video_only_reencodes_when_needed() -> None:
    assert not sns_prepare.plan_video(_probe()).needs_reencode  # Omni 720x1280 24fps는 그대로
    wide = sns_prepare.plan_video(_probe(width=3840, height=2160))
    assert "width_over_limit" in wide.reasons and "scale=1920:-2" in wide.filters
    slow = sns_prepare.plan_video(_probe(fps="15/1"))
    assert slow.reasons == ("fps_out_of_range",)
    for bad in ("2.0", "1000"):  # 길이는 고칠 수 없다
        with pytest.raises(sns_prepare.PrepareError) as error:
            sns_prepare.plan_video(_probe(duration=bad))
        assert error.value.code == "video_duration_out_of_range"


# --- 입력·생성 전 거절 (비용이 나지 않는다) ---------------------------------------------


def test_exactly_one_source_and_min_duration(gate: Gate) -> None:
    both = _call(_image_input(notice_id="ntc_x"))
    assert both["outcome"] == "invalid_input"
    neither = _call({"scene_template_id": "cat_bookstore_image", "fields": FIELDS})
    assert neither["outcome"] == "invalid_input"
    short = _call(_video_input(duration_s=2))  # 릴스 최소 3초 미만은 생성 전에 거절
    assert short["outcome"] == "invalid_input"
    assert gate.seen == []


def test_unknown_notice_stops_before_generation(gate: Gate, no_tools_check: None) -> None:
    out = _call(_image_input(coupon=None, notice_id="ntc_none"))
    assert out["outcome"] == "notice_not_found"
    assert gate.seen == []


def test_missing_constraints_stop_before_generation(gate: Gate, no_tools_check: None) -> None:
    coupon = _coupon()
    coupon["constraints"] = coupon["constraints"][:3]
    out = _call(_image_input(coupon=coupon))
    assert out["outcome"] == "rejected_constraint_missing"
    assert gate.seen == []


def test_media_dir_not_configured_stops_before_generation(
    gate: Gate, no_tools_check: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(sns_media.MEDIA_DIR_ENV)
    out = _call(_image_input())
    assert out["outcome"] == "media_dir_not_configured" and gate.seen == []


def test_missing_tools_stop_before_generation(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sns_prepare, "preflight_tools", lambda: "ffmpeg_not_found")
    out = _call(_image_input())
    assert out["outcome"] == "post_tools_unavailable"
    assert out["output"]["reason"] == "ffmpeg_not_found" and gate.seen == []


# --- 안전 관문은 우회하지 않는다 ---------------------------------------------------------


@pytest.mark.parametrize("status", ["held", "rejected"])
def test_gate_block_is_passed_through_and_nothing_is_labeled(
    status: str, monkeypatch: pytest.MonkeyPatch, no_tools_check: None
) -> None:
    fake = Gate(
        status=status,
        **({"review_id": "rev_1", "hold_reason": "over_threshold"} if status == "held" else {}),
    )
    monkeypatch.setattr(skill, "execute", fake)
    called: list[str] = []
    monkeypatch.setattr(
        sns_prepare, "label_media", lambda *a, **k: called.append("label") or pytest.fail("label")
    )
    out = _call(_image_input())
    assert out["outcome"] in ("held", "execute_rejected")
    assert out["output"]["stage"] == "generate_media"
    assert fake.seen == ["generate_media"] and called == []
    assert not list(sns_media.media_root().rglob("labeled.*"))  # type: ignore[union-attr]


def test_cost_cap_still_applies(monkeypatch: pytest.MonkeyPatch, no_tools_check: None) -> None:
    """실제 공급자 이름이 설정돼 있는데 상한이 비어 있으면 공급자를 부르지 않는다(fail-closed)."""
    fake = Gate()
    monkeypatch.setattr(skill, "execute", fake)
    monkeypatch.setenv("MARKETING_IMAGE_PROVIDER", "gemini")
    monkeypatch.setenv("MARKETING_IMAGE_MODEL", "some-model")
    out = _call(_image_input())
    assert out["status"] == "failed" and out["output"]["stage"] == "generate_media"
    assert fake.seen == []


# --- 끝에서 끝까지 (ffmpeg) -----------------------------------------------------------


@needs_ffmpeg
def test_mock_image_end_to_end_is_publishable_and_never_published(gate: Gate) -> None:
    out = _call(_image_input())
    assert out["outcome"] is None and out["status"] == "completed", out
    result = out["output"]
    assert result["stage"] == "draft_ready"
    ref = result["asset_ref"]
    assert ref.endswith("labeled.jpg") and not Path(ref).is_absolute()
    asset = sns_media.resolve_asset(ref)
    assert sns_media.check_image(asset).problems == []
    assert sns_media.sha256_file(asset) == result["asset_sha256"]
    assert sns_media.read_machine_mark(asset, "image")["ai_generated"] == "true"
    check = result["check"]
    assert check["verdict"] == "approved" and check["publishable"] is True
    assert (check["ai_label_missing_count"], check["text_mismatch_count"]) == (0, 0)
    assert result["conversion"]["reencoded"] is True  # 1x1 PNG를 320 너비로 키웠다
    assert sns.ai_label_text("image") in result["caption"]
    assert result["publish_input"]["asset_sha256"] == result["asset_sha256"]
    assert gate.seen == ["generate_media"]  # publish_sns 관문은 부르지 않았다
    assert "사람 승인" in result["next_step"]
    assert str(sns_media.media_root()) not in json.dumps(out)  # 절대 경로가 결과에 없다


@needs_ffmpeg
def test_second_call_replays_generation_and_reuses_label(gate: Gate) -> None:
    first = _call(_image_input())["output"]
    second = _call(_image_input())["output"]
    assert second["generation"]["replayed"] is True and second["label"]["reused"] is True
    assert second["post_draft_id"] == first["post_draft_id"]
    assert second["asset_sha256"] == first["asset_sha256"]


@needs_ffmpeg
def test_tall_image_is_cropped_to_feed_ratio(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_source(
        monkeypatch,
        _SourceProvider(
            "image",
            ["-f", "lavfi", "-i", "testsrc=size=768x1376:rate=1", "-frames:v", "1"],
            "png",
        ),
    )
    result = _call(_image_input())["output"]
    assert result["stage"] == "draft_ready", result["check"]["problems"]
    assert result["conversion"]["cropped"] is True
    assert result["conversion"]["output_size"] == [768, 960]
    dims = sns_media.jpeg_size(sns_media.resolve_asset(result["asset_ref"]))
    assert dims == (768, 960)


@needs_ffmpeg
def test_video_end_to_end_becomes_labeled_reel(gate: Gate, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_source(
        monkeypatch,
        _SourceProvider(
            "video",
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=270x480:rate=30:duration=3.5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
            ],  # fmt: skip
            "mp4",
        ),
    )
    out = _call(_video_input())
    result = out["output"]
    assert result["stage"] == "draft_ready", result.get("check", result)
    assert result["media_kind"] == "reel" and result["asset_ref"].endswith("labeled.mp4")
    assert result["conversion"]["reencoded"] is False
    assert result["publish_input"]["share_to_feed"] is True
    check = result["check"]
    assert check["publishable"] is True and check["machine_mark_present"] is True
    assert sns.ai_label_text("reel") in result["caption"]


@needs_ffmpeg
def test_video_with_out_of_range_fps_is_normalized_first(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_source(
        monkeypatch,
        _SourceProvider(
            "video",
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=270x480:rate=15:duration=4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
            ],  # fmt: skip
            "mp4",
        ),
    )
    result = _call(_video_input())["output"]
    assert result["conversion"]["reasons"] == ["fps_out_of_range"]
    assert result["stage"] == "draft_ready", result["check"]["problems"]
    assert result["check"]["media_details"]["fps"] >= sns_media.REEL_FPS_MIN


@needs_ffmpeg
def test_unfixable_video_is_rejected_after_generation_without_a_draft(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_source(
        monkeypatch,
        _SourceProvider(
            "video",
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=270x480:rate=30:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
            ],  # fmt: skip
            "mp4",
        ),
    )
    out = _call(_video_input())
    assert out["outcome"] == "convert_failed"
    assert out["output"]["reason"] == "video_duration_out_of_range"
    assert handlers.STATE.sns._conn.execute("SELECT COUNT(*) FROM sns_drafts").fetchone()[0] == 0


@needs_ffmpeg
def test_mock_placeholder_video_fails_cleanly_without_leaking_paths(gate: Gate) -> None:
    out = _call(_video_input())  # 목 영상은 유효한 mp4가 아니다
    assert out["outcome"] == "convert_failed"
    assert str(sns_media.media_root()) not in json.dumps(out)


# --- 서버 연결 --------------------------------------------------------------------


def test_card_and_acl() -> None:
    skills = {s.id: s for s in load_card().skills}
    entry = skills["prepare_sns_post_from_media"]
    assert (entry.grade, entry.verifier) == ("B", "safety")
    assert "prepare_sns_post_from_media" in handlers.HANDLERS
    assert skills["publish_sns"].grade == "C"  # 게시는 여전히 사람 승인(C)


# --- 글꼴 선택(번들 Pretendard) ---------------------------------------------------


def test_font_order_env_then_bundled_then_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = sns_label.load_config()
    monkeypatch.delenv(sns_label.FONT_ENV, raising=False)
    bundled = sns_label.find_font(config)
    assert bundled.endswith("PretendardStd-Bold.ttf") and Path(bundled).is_file()
    system = tmp_path / "sys.ttf"  # 시스템 후보가 있어도 번들이 먼저다
    system.write_bytes(b"x")
    with_system = dataclasses.replace(config, font_candidates=(str(system),))
    assert sns_label.find_font(with_system) == bundled
    monkeypatch.setenv(sns_label.FONT_ENV, str(system))  # 환경변수가 번들보다 먼저다
    assert sns_label.find_font(config) == str(system)
    monkeypatch.delenv(sns_label.FONT_ENV)  # 번들을 끄면 시스템 후보
    assert sns_label.find_font(dataclasses.replace(with_system, font_bundled="")) == str(system)


def test_no_font_anywhere_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sns_label.FONT_ENV, raising=False)
    config = dataclasses.replace(sns_label.load_config(), font_bundled="", font_candidates=())
    with pytest.raises(sns_label.LabelError) as error:
        sns_label.find_font(config)
    assert error.value.code == "font_not_found"
    outside = dataclasses.replace(config, font_bundled="../pyproject.toml")
    with pytest.raises(sns_label.LabelError):  # 패키지 밖 경로는 번들로 쓰지 않는다
        sns_label.find_font(outside)


def test_bundled_font_matches_recorded_hash_and_has_license() -> None:
    folder = Path(sns_label.__file__).parent / "assets" / "fonts"
    digest = hashlib.sha256((folder / "PretendardStd-Bold.ttf").read_bytes()).hexdigest()
    assert digest.startswith("d19618a699fa0ebb")
    assert "SIL Open Font License" in (folder / "PretendardStd-OFL.txt").read_text("utf-8")

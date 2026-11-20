"""영상 길이 상한 설정·이어 붙이기·전체 비용 가드·safety 정책 일치 (mock 전송, 유료 호출 없음)."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, ExecuteDecision
from ax_safety.rules import load_thresholds
from marketing_agent import handlers
from marketing_agent.media import registry, skill, video_chain
from marketing_agent.media.base import MediaRequest, MediaResult, Usage
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.gemini_video import CAPS as GEMINI_CAPS
from marketing_agent.media.gemini_video import FILES_ENDPOINT, GeminiVideoError, GeminiVideoProvider
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.video_limits import (
    ABSOLUTE_MAX_DURATION_S,
    VideoCaps,
    VideoLimitError,
    parse_max_duration,
    parse_warn_duration,
    plan_clips,
)

MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
KEY = "test-key-SECRET-123"
FIELDS = {"season": "spring", "color_theme": "soft pastel", "category": "그림책"}


# ---- 길이 상한 설정 ----


@pytest.mark.parametrize(
    ("raw", "seconds", "problem"),
    [
        (None, 15, None),
        ("", 15, None),
        ("15", 15, None),
        (" 40 ", 40, None),
        ("1", 1, None),
        ("90", 90, None),
        ("91", 15, "video_max_duration_invalid"),  # 코드 절대 상한(90초)을 넘는 설정은 거부
        ("120", 15, "video_max_duration_invalid"),
        ("0", 15, "video_max_duration_invalid"),
        ("-5", 15, "video_max_duration_invalid"),
        ("abc", 15, "video_max_duration_invalid"),
        ("1.5", 15, "video_max_duration_invalid"),
    ],
)
def test_max_duration_setting_is_one_to_ninety_default_fifteen(
    raw: str | None, seconds: int, problem: str | None
) -> None:
    parsed = parse_max_duration(raw)
    assert (parsed.seconds, parsed.problem) == (seconds, problem)


def test_settings_read_the_env_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKETING_VIDEO_MAX_DURATION_S", raising=False)
    assert MediaSettings.from_env().video_max_duration_s == 15
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    assert MediaSettings.from_env().video_max_duration_s == 60
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "91")
    assert MediaSettings.from_env().video_max_duration_problem == "video_max_duration_invalid"


@pytest.mark.parametrize(
    ("raw", "max_s", "seconds", "problem"),
    [
        (None, 90, 30, None),  # 기본 30
        ("", 90, 30, None),
        (None, 15, 15, None),  # 상한이 30보다 작으면 기본값도 상한을 넘지 않는다
        ("20", 90, 20, None),
        (" 45 ", 90, 45, None),
        ("1", 90, 1, None),
        ("90", 90, 90, None),  # 상한과 같은 값은 허용(사실상 경고 없음)
        ("0", 90, 30, "video_warn_duration_invalid"),
        ("91", 90, 30, "video_warn_duration_invalid"),
        ("16", 15, 15, "video_warn_duration_invalid"),  # 운영 상한 초과
        ("abc", 60, 30, "video_warn_duration_invalid"),
        ("-3", 60, 30, "video_warn_duration_invalid"),
        ("1.5", 60, 30, "video_warn_duration_invalid"),
    ],
)
def test_warn_duration_setting_is_one_to_max_default_thirty(
    raw: str | None, max_s: int, seconds: int, problem: str | None
) -> None:
    parsed = parse_warn_duration(raw, max_s)
    assert (parsed.seconds, parsed.problem) == (seconds, problem)


def test_settings_read_the_warn_env_and_log_only_the_reason_code(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    monkeypatch.delenv("MARKETING_VIDEO_WARN_DURATION_S", raising=False)
    assert MediaSettings.from_env().video_warn_duration_s == 30
    monkeypatch.setenv("MARKETING_VIDEO_WARN_DURATION_S", "20")
    assert MediaSettings.from_env().video_warn_duration_s == 20
    monkeypatch.setenv("MARKETING_VIDEO_WARN_DURATION_S", "secret-77")
    with caplog.at_level("WARNING"):
        assert MediaSettings.from_env().video_warn_duration_s == 30
    assert "video_warn_duration_invalid" in caplog.text and "secret-77" not in caplog.text


def test_safety_policy_duration_limit_equals_the_code_absolute_limit() -> None:
    """설정이 정책 상한을 넘을 수 없다: 정책 video_duration 값 = 코드 절대 상한."""
    rule = next(r for r in load_thresholds().rules if r.key == "video_duration")
    assert rule.action == "generate_media" and rule.op == ">"
    assert rule.value == ABSOLUTE_MAX_DURATION_S == 90


# ---- 클립 계획 ----


def test_single_clip_when_it_fits() -> None:
    plan = plan_clips(15, GEMINI_CAPS)
    assert [(c.mode, c.duration_s) for c in plan] == [("first", 15)]
    assert [c.duration_s for c in plan_clips(3, GEMINI_CAPS)] == [3]


def test_extension_is_used_up_to_the_thirty_second_total() -> None:
    plan = plan_clips(30, GEMINI_CAPS)
    assert [c.mode for c in plan] == ["first", "extend", "extend"]
    assert sum(c.duration_s for c in plan) == 30
    assert plan[0].duration_s == 15
    assert all(3 <= c.duration_s <= 10 for c in plan[1:])


def test_beyond_thirty_seconds_continues_with_last_frame_image_clips() -> None:
    plan = plan_clips(90, GEMINI_CAPS)
    assert [c.mode for c in plan] == [
        "first",
        "extend",
        "extend",
        "image",
        "image",
        "image",
        "image",
    ]
    assert sum(c.duration_s for c in plan) == 90
    assert all(3 <= c.duration_s <= 15 for c in plan)


@pytest.mark.parametrize("total", list(range(1, ABSOLUTE_MAX_DURATION_S + 1)))
def test_every_length_gets_a_valid_plan_for_gemini_caps(total: int) -> None:
    if total < GEMINI_CAPS.min_s:
        with pytest.raises(VideoLimitError, match="duration_under_min"):
            plan_clips(total, GEMINI_CAPS)
        return
    plan = plan_clips(total, GEMINI_CAPS)
    assert sum(c.duration_s for c in plan) == total
    for clip in plan:
        lo, hi = (3, 10) if clip.mode == "extend" else (3, 15)
        assert lo <= clip.duration_s <= hi
    assert sum(c.duration_s for c in plan if c.mode != "image") <= 30


def test_provider_without_extension_or_image_input_cannot_chain() -> None:
    caps = VideoCaps(min_s=3, max_s=15)
    assert [c.duration_s for c in plan_clips(15, caps)] == [15]
    with pytest.raises(VideoLimitError, match="long_video_unsupported"):
        plan_clips(16, caps)


def test_image_only_provider_chains_with_last_frames() -> None:
    caps = VideoCaps(min_s=3, max_s=15, image_start=True)
    plan = plan_clips(40, caps)
    assert [c.mode for c in plan] == ["first", "image", "image"]
    assert sum(c.duration_s for c in plan) == 40


def test_over_absolute_limit_is_rejected_by_the_planner() -> None:
    with pytest.raises(VideoLimitError, match="duration_over_limit"):
        plan_clips(ABSOLUTE_MAX_DURATION_S + 1, GEMINI_CAPS)


def test_registered_providers_report_their_clip_limits() -> None:
    gemini = registry.get_adapter("gemini")
    assert gemini is not None and gemini.video_caps.max_s == 15  # type: ignore[attr-defined]
    for name in ("kling", "seedance"):
        adapter = registry.get_adapter(name)
        caps = adapter.video_caps  # type: ignore[attr-defined]
        assert not caps.extension and not caps.image_start  # 연장·시작 이미지 미구현: 긴 영상 거절


# ---- Gemini 요청 모양(연장·이미지→영상) ----


def _body(job: str = "v_job") -> dict:
    return {
        "id": job,
        "status": "completed",
        "model": "m",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "video",
                        "mime_type": "video/mp4",
                        "data": base64.b64encode(MP4).decode(),
                    }
                ],
            }
        ],
        "usage": {"total_input_tokens": 10, "total_output_tokens": 1000},
    }


def _gemini_request(tmp_path: Path, duration: int, cont) -> MediaRequest:  # noqa: ANN001
    return MediaRequest(
        media_kind="video",
        prompt="a cat on a book",
        prompt_sha256="b" * 64,
        resolution="720p",
        quality=None,
        duration_s=duration,
        remove_audio=True,
        model="m",
        output_dir=tmp_path,
        continuation=cont,
    )


def _public(host: str, port: int, **_kw: object) -> list[tuple]:
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def test_gemini_extension_request_uses_previous_interaction_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marketing_agent.media.base import Continuation

    monkeypatch.setenv("MARKETING_VIDEO_API_KEY", KEY)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_body("v_ext"))

    provider = GeminiVideoProvider(transport=httpx.MockTransport(handler), resolver=_public)
    cont = Continuation("extend", previous_job_id="v_prev")
    result = provider.generate(_gemini_request(tmp_path, 7, cont))
    body = json.loads(seen[0].content)
    assert body["previous_interaction_id"] == "v_prev"
    assert body["input"].startswith("Continue the scene.")
    assert body["response_format"] == {"type": "video", "duration": "7s", "delivery": "uri"}
    assert body["store"] is True and KEY not in seen[0].content.decode()
    assert result.provider_job_id == "v_ext"
    for bad in (2, 11):  # 연장 회당 3~10초
        with pytest.raises(GeminiVideoError, match="unsupported_duration"):
            provider.generate(_gemini_request(tmp_path, bad, cont))
    assert len(seen) == 1


def test_gemini_image_start_request_sends_the_frame_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marketing_agent.media.base import Continuation

    monkeypatch.setenv("MARKETING_VIDEO_API_KEY", KEY)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8jpeg")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_body())

    provider = GeminiVideoProvider(transport=httpx.MockTransport(handler), resolver=_public)
    provider.generate(_gemini_request(tmp_path, 15, Continuation("image", image_path=frame)))
    body = json.loads(seen[0].content)
    assert "previous_interaction_id" not in body
    image, text = body["input"]
    assert image == {
        "type": "image",
        "data": base64.b64encode(b"\xff\xd8jpeg").decode(),
        "mime_type": "image/jpeg",
    }
    assert text == {"type": "text", "text": "a cat on a book"}
    assert body["response_format"]["duration"] == "15s"
    assert FILES_ENDPOINT  # 모듈 상수 사용(린트)


# ---- 이어 붙이기 실행(가짜 공급자·가짜 ffmpeg) ----


class FakeVideo:
    """클립마다 파일을 쓰고 받은 요청을 모은다."""

    name = "fakev"
    video_caps = GEMINI_CAPS

    def __init__(self) -> None:
        self.calls: list[MediaRequest] = []
        self.fail_at: int | None = None

    def generate(self, request: MediaRequest) -> MediaResult:
        index = len(self.calls)
        self.calls.append(request)
        if self.fail_at == index:
            raise RuntimeError("provider down")
        data = MP4 + f"clip{index}".encode()
        path = request.output_dir / "asset.mp4"
        path.write_bytes(data)
        return MediaResult(
            asset_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            provider="fakev",
            model_id="fm",
            usage=Usage(input_tokens=10, output_tokens=1000),
            generated_at=datetime.now(UTC),
            provider_job_id=f"job{index}",
        )


class FakeFfmpeg:
    """concat·마지막 프레임 추출 인자를 흉내 내 파일을 쓴다. 호출 인자를 모은다."""

    CONCAT_OUT = MP4 + b"+concat"

    def __init__(self, *, audio: bool = False, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.audio = audio
        self.fail = fail

    def __call__(self, args: list[str], **_kw: object) -> SimpleNamespace:
        self.calls.append(args)
        out = Path(args[-1])
        if "concat" in args:
            listed = (out.parent / "_concat.txt").read_text(encoding="utf-8").splitlines()
            assert all(line.startswith("file '") for line in listed)
            self.listed = len(listed)
            data = self.CONCAT_OUT + (b"....hdlr" + b"\x00" * 8 + b"soun" if self.audio else b"")
            out.write_bytes(data)
        else:
            out.write_bytes(b"\xff\xd8frame")
        return SimpleNamespace(returncode=1 if self.fail in args[1:] else 0, stderr=b"")


def _media_request(tmp_path: Path, total: int, *, remove_audio: bool = True) -> MediaRequest:
    return MediaRequest(
        media_kind="video",
        prompt="p",
        prompt_sha256="a" * 64,
        resolution="720p",
        quality=None,
        duration_s=total,
        remove_audio=remove_audio,
        model="fm",
        output_dir=tmp_path,
    )


def test_chain_generates_clips_in_order_and_concats_with_ffmpeg(tmp_path: Path) -> None:
    adapter, ffmpeg = FakeVideo(), FakeFfmpeg()
    clips = plan_clips(45, GEMINI_CAPS)
    result = video_chain.generate_chain(
        adapter, _media_request(tmp_path, 45), clips, ffmpeg="ffmpeg", run=ffmpeg
    )
    assert [r.duration_s for r in adapter.calls] == [c.duration_s for c in clips]
    modes = [r.continuation.mode if r.continuation else None for r in adapter.calls]
    assert modes == [None, "extend", "extend", "image"]
    assert adapter.calls[1].continuation.previous_job_id == "job0"  # type: ignore[union-attr]
    assert adapter.calls[2].continuation.previous_job_id == "job1"  # type: ignore[union-attr]
    frame = adapter.calls[3].continuation.image_path  # type: ignore[union-attr]
    assert frame is not None and frame.name == "frame_3.jpg"
    # 합친 파일: 무음(-an)·복사로 합치고 sha256·사용량·클립 수를 남긴다
    concat_args = next(a for a in ffmpeg.calls if "concat" in a)
    assert (
        "-an" in concat_args
        and ["-c", "copy"] == concat_args[concat_args.index("-c") : concat_args.index("-c") + 2]
    )
    assert ffmpeg.listed == 4
    assert result.asset_path == tmp_path / "asset.mp4"
    assert result.sha256 == hashlib.sha256(FakeFfmpeg.CONCAT_OUT).hexdigest()
    assert result.clip_count == 4
    assert (result.usage.input_tokens, result.usage.output_tokens) == (40, 4000)  # type: ignore[union-attr]
    assert result.provider_job_id == "job0,job1,job2,job3"
    assert not (tmp_path / "_clips").exists()  # 중간 파일은 지운다


def test_chain_stops_and_cleans_up_when_a_clip_fails(tmp_path: Path) -> None:
    adapter, ffmpeg = FakeVideo(), FakeFfmpeg()
    adapter.fail_at = 2
    with pytest.raises(RuntimeError):
        video_chain.generate_chain(
            adapter,
            _media_request(tmp_path, 30),
            plan_clips(30, GEMINI_CAPS),
            ffmpeg="ffmpeg",
            run=ffmpeg,
        )
    assert len(adapter.calls) == 3 and not (tmp_path / "asset.mp4").exists()
    assert not (tmp_path / "_clips").exists()


def test_chain_rejects_audio_left_in_the_muted_result(tmp_path: Path) -> None:
    with pytest.raises(video_chain.VideoChainError, match="audio_track_present"):
        video_chain.generate_chain(
            FakeVideo(),
            _media_request(tmp_path, 30),
            plan_clips(30, GEMINI_CAPS),
            ffmpeg="ffmpeg",
            run=FakeFfmpeg(audio=True),
        )
    assert not (tmp_path / "asset.mp4").exists()


def test_chain_reports_ffmpeg_failures_as_codes(tmp_path: Path) -> None:
    with pytest.raises(video_chain.VideoChainError, match="last_frame_failed"):
        video_chain.generate_chain(
            FakeVideo(),
            _media_request(tmp_path, 40),
            plan_clips(40, GEMINI_CAPS),
            ffmpeg="ffmpeg",
            run=FakeFfmpeg(fail="-sseof"),
        )


# ---- 스킬 수준: 길이 거부·전체 사용량 가드·이어 붙이기 ----

PRICES = """
version = 1

[[price]]
provider = "fakev"
model = "fm"
kind = "video"
resolution = "720p"
per_second_usd = 0.1
max_duration_s = 15
source = "https://example.test/pricing"
checked_on = "2026-11-19"
"""


class Gate:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append({"action": action, "params": params})
        return ExecuteDecision(execution_id="exe_1", grade="B", status="executed")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    for name in (
        "MARKETING_VIDEO_MAX_DURATION_S",
        "MARKETING_MEDIA_LEDGER_DB",
    ):
        monkeypatch.delenv(name, raising=False)
    values = {
        "MARKETING_MEDIA_DIR": str(tmp_path / "media"),
        "MARKETING_VIDEO_PROVIDER": "fakev",
        "MARKETING_VIDEO_MODEL": "fm",
        "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY": "100000",
        "MARKETING_FFMPEG_BIN": sys.executable,  # 존재 확인만 통과시킨다(실제로는 가짜 ffmpeg)
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    adapter = FakeVideo()
    monkeypatch.setitem(registry.ADAPTERS, "fakev", lambda: adapter)
    gate = Gate()
    monkeypatch.setattr(skill, "execute", gate)
    ffmpeg = FakeFfmpeg()
    real = video_chain.generate_chain

    def chain(adapter_, request, clips):  # noqa: ANN001
        return real(adapter_, request, clips, ffmpeg="ffmpeg", run=ffmpeg)

    monkeypatch.setattr(skill, "generate_chain", chain)
    prices = tmp_path / "prices.toml"
    prices.write_text(PRICES, encoding="utf-8")
    return SimpleNamespace(adapter=adapter, gate=gate, ffmpeg=ffmpeg, prices=prices)


def _request(**over) -> AgentRequest:  # noqa: ANN003
    input_ = {
        "post_draft_id": "DRAFT-1",
        "media_kind": "video",
        "scene_template_id": "cat_bookstore_video",
        "fields": FIELDS,
    } | over
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": "ctx_chain_1",
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": input_,
        }
    )


async def _run(env: SimpleNamespace, ledger: MediaLedger | None = None, **over):  # noqa: ANN003, ANN202
    return await skill.generate_media(
        _request(**over),
        ledger=ledger or MediaLedger(),
        from_decision=handlers._from_decision,
        prices_path=env.prices,
    )


@pytest.mark.asyncio
async def test_single_clip_at_or_under_fifteen_seconds_is_not_chained(env) -> None:  # noqa: ANN001
    result = await _run(env, duration_s=15)
    assert result.outcome == "generated" and result.output is not None
    assert result.output["clip_count"] == 1 and result.output["duration_s"] == 15
    assert len(env.adapter.calls) == 1 and env.adapter.calls[0].continuation is None
    assert env.ffmpeg.calls == []
    assert env.gate.seen[0]["params"]["clip_count"] == 1


@pytest.mark.asyncio
async def test_default_length_is_the_template_length_capped_by_the_setting(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "10")
    result = await _run(env)  # 템플릿 기본 15초 → 설정 상한 10초로 맞춘다
    assert result.output is not None and result.output["duration_s"] == 10


@pytest.mark.asyncio
async def test_longer_than_a_clip_is_chained_and_usage_is_the_whole_length(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    result = await _run(env, duration_s=40)
    assert result.outcome == "generated" and result.output is not None
    out = result.output
    clips = plan_clips(40, GEMINI_CAPS)
    assert out["clip_count"] == len(clips) == 4 and out["duration_s"] == 40
    assert [c.duration_s for c in env.adapter.calls] == [c.duration_s for c in clips]
    # 사용량은 클립 수가 아니라 클립 길이의 합(초). 원화는 계산하지 않는다
    assert out["cost_est_krw"] == 0 and out["usage"]["unit"] == "seconds"
    assert out["usage"]["amount"] == 40
    params = env.gate.seen[0]["params"]
    assert (params["usage_amount"], params["duration_s"], params["clip_count"]) == (40, 40, 4)
    assert "cost_krw" not in params
    assert out["muted"] is True and out["ai_generated"] is True
    asset = Path(out["asset_ref"])
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == out["sha256"]
    assert out["sha256"] == hashlib.sha256(FakeFfmpeg.CONCAT_OUT).hexdigest()


@pytest.mark.asyncio
async def test_over_the_setting_is_rejected_before_any_call(env) -> None:  # noqa: ANN001
    result = await _run(env, duration_s=16)  # 기본 상한 15초
    assert result.outcome == "duration_over_limit"
    assert result.output["limit_s"] == 15  # type: ignore[index]
    assert env.adapter.calls == [] and env.gate.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["91", "120", "0", "abc"])
async def test_invalid_or_over_absolute_setting_blocks_video(
    env,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", raw)
    result = await _run(env, duration_s=10)
    assert result.outcome == "video_max_duration_invalid"
    assert env.adapter.calls == [] and env.gate.seen == []


@pytest.mark.asyncio
async def test_request_over_ninety_seconds_is_invalid_input(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "90")
    result = await _run(env, duration_s=91)
    assert result.outcome == "invalid_input" and env.adapter.calls == []


@pytest.mark.asyncio
async def test_whole_length_is_checked_against_the_monthly_quota_before_calls(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "50")
    ledger = MediaLedger()
    ok = await _run(env, ledger, duration_s=15)  # 15초: 예산 안
    assert ok.outcome == "generated"
    env.adapter.calls.clear()
    # 클립 하나는 예산 안이어도 전체 40초 + 이미 쓴 15초 > 50초
    over = await _run(env, ledger, duration_s=40)
    assert over.outcome == "quota_exceeded"
    assert over.output == {  # type: ignore[comparison-overlap]
        "media_kind": "video",
        "unit": "seconds",
        "requested": 40,
        "month_used": 15,
        "limit": 50,
        "remaining_pct": 70,
    }
    assert env.adapter.calls == []


@pytest.mark.asyncio
async def test_long_video_alone_over_the_quota_makes_no_call_and_no_gate_request(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "39")
    result = await _run(env, duration_s=40)
    assert result.outcome == "quota_exceeded"
    assert env.adapter.calls == [] and env.gate.seen == []


@pytest.mark.asyncio
async def test_provider_that_cannot_chain_rejects_long_requests_before_any_call(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    monkeypatch.setattr(FakeVideo, "video_caps", VideoCaps(min_s=3, max_s=15))
    result = await _run(env, duration_s=20)
    assert result.outcome == "long_video_unsupported"
    assert env.adapter.calls == [] and env.gate.seen == []


@pytest.mark.asyncio
async def test_plan_cost_matches_generation_usage_and_gives_a_clip_table(
    env,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_MAX_DURATION_S", "60")
    plan = skill.plan_cost(
        _request(duration_s=40),
        ledger=MediaLedger(),
        settings=MediaSettings.from_env(),
        prices_path=env.prices,
        allow_video=True,
        with_table=True,
    )
    assert isinstance(plan, skill.CostPlan)
    assert (plan.usage.unit, plan.usage.amount) == ("seconds", 40)
    assert plan.clips_by_duration is not None and plan.clips_by_duration[40] == 4
    assert plan.clips_by_duration[15] == 1
    assert 2 not in plan.clips_by_duration  # 3초 미만은 못 만든다
    assert max(plan.clips_by_duration) == 60

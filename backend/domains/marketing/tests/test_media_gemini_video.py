"""Gemini 영상 어댑터: 가짜 전송·시계로 요청·폴링·파싱·키 미노출·거부를 검증한다(네트워크 없음)."""

import base64
import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from marketing_agent.media import pricing, registry
from marketing_agent.media.base import MediaRequest
from marketing_agent.media.gemini_image import GeminiImageProvider
from marketing_agent.media.gemini_video import (
    ENDPOINT,
    FILES_ENDPOINT,
    MAX_DURATION_S,
    GeminiVideoError,
    GeminiVideoProvider,
    has_audio_track,
    strip_audio,
)

KEY = "test-key-SECRET-123"
MODEL = "test-model-id"
MUTED_MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
AUDIO_MP4 = MUTED_MP4 + b"....hdlr\x00\x00\x00\x00\x00\x00\x00\x00soun-fake"
FILE_URI = f"{FILES_ENDPOINT}/abc123:download?alt=media"


def _request(
    tmp_path: Path,
    *,
    resolution: str = "720p",
    duration_s: int | None = 8,
    model: str = MODEL,
    remove_audio: bool = True,
) -> MediaRequest:
    return MediaRequest(
        media_kind="video",
        prompt="a cat on a book",
        prompt_sha256="b" * 64,
        resolution=resolution,
        quality=None,
        duration_s=duration_s,
        remove_audio=remove_audio,
        model=model,
        output_dir=tmp_path,
    )


def _body(block: dict[str, object], **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "v1_job",
        "status": "completed",
        "model": MODEL,
        "steps": [
            {"type": "thought", "content": []},
            {"type": "model_output", "content": [block]},
        ],
        "usage": {"total_input_tokens": 9, "total_output_tokens": 46336},
    }
    body.update(extra)
    return body


def _inline(data: bytes = MUTED_MP4) -> dict[str, object]:
    return {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(data).decode()}


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    clock: _Clock | None = None,
    *,
    max_wait: float = 20.0,
) -> GeminiVideoProvider:
    clock = clock or _Clock()
    return GeminiVideoProvider(
        transport=httpx.MockTransport(handler),
        poll_interval_s=5.0,
        poll_max_wait_s=max_wait,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_API_KEY", KEY)


def test_inline_request_shape_and_result(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_body(_inline()))

    result = _provider(handler).generate(_request(tmp_path, duration_s=6))
    assert len(seen) == 1
    req = seen[0]
    assert str(req.url) == ENDPOINT and KEY not in str(req.url)
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["model"] == MODEL and body["input"] == "a cat on a book"
    assert body["background"] is False and body["stream"] is False
    assert body["response_format"] == {
        "type": "video",
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "duration": "6s",
        "delivery": "uri",
    }
    assert KEY not in req.content.decode()
    assert result.asset_path.name == "asset.mp4"
    assert result.asset_path.read_bytes() == MUTED_MP4
    assert result.sha256 == hashlib.sha256(MUTED_MP4).hexdigest()
    assert result.provider == "gemini" and result.provider_job_id == "v1_job"
    assert result.model_id == MODEL
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (9, 46336)
    assert KEY not in repr(result)


def test_uri_delivery_polls_until_active_then_downloads(tmp_path: Path) -> None:
    clock = _Clock()
    states = iter(["PROCESSING", "PROCESSING", "ACTIVE"])
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "POST":
            return httpx.Response(200, json=_body({"type": "video", "uri": FILE_URI}))
        if req.url.path.endswith(":download"):
            return httpx.Response(200, content=MUTED_MP4)
        assert req.url.path == "/v1beta/files/abc123"
        return httpx.Response(200, json={"state": next(states)})

    result = _provider(handler, clock).generate(_request(tmp_path))
    assert clock.sleeps == [5.0, 5.0]
    assert result.asset_path.read_bytes() == MUTED_MP4
    assert all(r.headers["x-goog-api-key"] == KEY for r in seen)
    assert all(KEY not in str(r.url) for r in seen)


def test_poll_timeout_fails_with_bounded_wait(tmp_path: Path) -> None:
    clock = _Clock()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json=_body({"type": "video", "uri": FILE_URI}))
        return httpx.Response(200, json={"state": "PROCESSING"})

    with pytest.raises(GeminiVideoError, match="poll_timeout"):
        _provider(handler, clock, max_wait=12.0).generate(_request(tmp_path))
    assert sum(clock.sleeps) <= 12.0
    assert list(tmp_path.iterdir()) == []


def test_file_failed_state_raises(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json=_body({"type": "video", "uri": FILE_URI}))
        return httpx.Response(200, json={"state": "FAILED"})

    with pytest.raises(GeminiVideoError, match="file_state_FAILED"):
        _provider(handler).generate(_request(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_download_redirect_does_not_send_key_to_other_host(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "POST":
            return httpx.Response(200, json=_body({"type": "video", "uri": FILE_URI}))
        if req.url.path == "/v1beta/files/abc123":
            return httpx.Response(200, json={"state": "ACTIVE"})
        if req.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/v.mp4"})
        return httpx.Response(200, content=MUTED_MP4)

    result = _provider(handler).generate(_request(tmp_path))
    other = [r for r in seen if r.url.host == "cdn.example.com"]
    assert len(other) == 1 and "x-goog-api-key" not in other[0].headers
    assert result.asset_path.read_bytes() == MUTED_MP4


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example.com/v1beta/files/abc123:download?alt=media",
        "http://generativelanguage.googleapis.com/v1beta/files/abc123:download",
        f"{FILES_ENDPOINT}/../other",
    ],
)
def test_unexpected_uri_rejected(tmp_path: Path, uri: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method != "POST":
            raise AssertionError("키를 보내면 안 된다")
        return httpx.Response(200, json=_body({"type": "video", "uri": uri}))

    with pytest.raises(GeminiVideoError, match="unexpected_video_uri"):
        _provider(handler).generate(_request(tmp_path))


def test_job_failure_status_raises(tmp_path: Path) -> None:
    body = _body(_inline(), status="failed")
    with pytest.raises(GeminiVideoError, match="status_failed"):
        _provider(lambda req: httpx.Response(200, json=body)).generate(_request(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_audio_track_is_stripped_when_muted_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemini는 오디오를 항상 넣어 보낸다. 무음 모드는 실패시키지 않고 ffmpeg로 제거한다."""
    from marketing_agent.media import gemini_video

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body(_inline(AUDIO_MP4)))

    seen: list[list[str]] = []

    def fake_strip(data: bytes, work_dir: Path, **_: object) -> bytes:
        assert data == AUDIO_MP4
        return MUTED_MP4

    monkeypatch.setattr(gemini_video, "strip_audio", fake_strip)
    assert has_audio_track(AUDIO_MP4) and not has_audio_track(MUTED_MP4)
    result = _provider(handler).generate(_request(tmp_path))
    assert result.audio_stripped is True
    assert result.asset_path.read_bytes() == MUTED_MP4 and not seen
    muted = _provider(lambda r: httpx.Response(200, json=_body(_inline(MUTED_MP4))))
    assert muted.generate(_request(tmp_path)).audio_stripped is False
    kept = _provider(handler).generate(_request(tmp_path, remove_audio=False))
    assert kept.asset_path.read_bytes() == AUDIO_MP4 and kept.audio_stripped is False


def test_audio_strip_failure_keeps_rejecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_FFMPEG_BIN", str(tmp_path / "no-such-ffmpeg"))
    handler = lambda req: httpx.Response(200, json=_body(_inline(AUDIO_MP4)))  # noqa: E731
    with pytest.raises(GeminiVideoError, match="ffmpeg_not_found"):
        _provider(handler).generate(_request(tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")
def test_real_ffmpeg_strips_audio_from_synthetic_mp4(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    done = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=1:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(src),
        ],  # fmt: skip
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip("ffmpeg로 합성 mp4를 만들 수 없다")
    data = src.read_bytes()
    assert has_audio_track(data)
    work = tmp_path / "work"
    work.mkdir()
    stripped = strip_audio(data, work)
    assert not has_audio_track(stripped) and b"ftyp" in stripped[:16]
    assert list(work.iterdir()) == []  # 임시 파일을 남기지 않는다


@pytest.mark.parametrize(
    ("resolution", "duration"),
    [
        ("1080p", 8),
        ("360p", 8),
        ("4k", 8),
        ("1024x1024", 8),
        ("720p", 9),
        ("720p", 2),
        ("720p", 0),
        ("720p", None),
    ],
)
def test_unsupported_resolution_or_duration_rejected_before_call(
    tmp_path: Path, resolution: str, duration: int | None
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("호출하면 안 된다")

    with pytest.raises(GeminiVideoError, match="unsupported_"):
        _provider(handler).generate(_request(tmp_path, resolution=resolution, duration_s=duration))


def test_image_request_rejected(tmp_path: Path) -> None:
    request = _request(tmp_path)
    image = MediaRequest(**{**request.__dict__, "media_kind": "image"})
    with pytest.raises(GeminiVideoError, match="unsupported_media_kind"):
        _provider(lambda req: httpx.Response(500)).generate(image)


def test_missing_key_or_model_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("호출하면 안 된다")

    with pytest.raises(GeminiVideoError, match="model_not_configured"):
        _provider(handler).generate(_request(tmp_path, model=""))
    monkeypatch.delenv("MARKETING_VIDEO_API_KEY")
    with pytest.raises(GeminiVideoError, match="api_key_not_configured"):
        _provider(handler).generate(_request(tmp_path))
    monkeypatch.setenv("MARKETING_IMAGE_API_KEY", KEY)  # 이미지 키로는 영상을 부르지 않는다
    with pytest.raises(GeminiVideoError, match="api_key_not_configured"):
        _provider(handler).generate(_request(tmp_path))


def test_http_error_does_not_leak_key_or_body(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"denied for {KEY}")

    with pytest.raises(GeminiVideoError) as info:
        _provider(handler).generate(_request(tmp_path))
    assert str(info.value) == "http_403" and KEY not in repr(info.value)
    assert list(tmp_path.iterdir()) == []


def test_transport_error_does_not_leak_key(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {KEY}", request=req)

    with pytest.raises(GeminiVideoError) as info:
        _provider(handler).generate(_request(tmp_path))
    assert KEY not in str(info.value) and info.value.__cause__ is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["x"]),
        httpx.Response(200, json={"id": "x", "status": "completed", "steps": []}),
        httpx.Response(200, json=_body({"type": "video", "data": "!!notb64"})),
        httpx.Response(200, json=_body({**_inline(), "mime_type": "video/webm"})),
    ],
)
def test_bad_responses_raise(tmp_path: Path, response: httpx.Response) -> None:
    with pytest.raises(GeminiVideoError):
        _provider(lambda req: response).generate(_request(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_usage_missing_is_none(tmp_path: Path) -> None:
    body = _body(_inline())
    del body["usage"]
    result = _provider(lambda req: httpx.Response(200, json=body)).generate(_request(tmp_path))
    assert result.usage is None


def test_supported_request_matches_price_table() -> None:
    entry = pricing.find_entry(
        provider="gemini",
        model="gemini-omni-1.1-flash",
        kind="video",
        resolution="720p",
        quality=None,
    )
    assert entry.max_duration_s == MAX_DURATION_S
    assert entry.output_tokens_per_second == 5792
    assert str(entry.output_usd_per_mtok) == "17.5" and str(entry.input_usd_per_mtok) == "1.5"
    assert entry.checked_on == "2026-11-11"
    with pytest.raises(pricing.PriceNotFound):
        pricing.output_tokens_for(entry, MAX_DURATION_S + 1)


def test_omni_12_price_entry_and_both_models_selectable() -> None:
    new = pricing.find_entry(
        provider="gemini",
        model="gemini-omni-1.2-flash",
        kind="video",
        resolution="720p",
        quality=None,
    )
    old = pricing.find_entry(
        provider="gemini",
        model="gemini-omni-1.1-flash",
        kind="video",
        resolution="720p",
        quality=None,
    )
    assert str(new.output_usd_per_mtok) == "12.0" and str(new.input_usd_per_mtok) == "1.5"
    assert new.output_tokens_per_second == 5792 and new.max_duration_s == MAX_DURATION_S
    assert new.checked_on == "2026-11-11" and new.source.startswith("https://")
    assert str(old.output_usd_per_mtok) == "17.5"  # 1.1 항목은 남겨 둔다
    assert pricing.output_tokens_for(new, 8) == 46_336


@pytest.mark.parametrize("model", ["gemini-omni-1.1-flash", "gemini-omni-1.2-flash"])
def test_either_configured_model_is_sent_as_is(tmp_path: Path, model: str) -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content)["model"])
        return httpx.Response(200, json=_body(_inline()))

    _provider(handler).generate(_request(tmp_path, model=model))
    assert seen == [model]


def test_video_spec_is_single_vertical_format() -> None:
    from marketing_agent.media import gemini_video

    assert gemini_video.ASPECT_RATIO == "9:16"
    assert gemini_video.ALLOWED_RESOLUTIONS == ("720p",)


def test_1080p_rejected_before_call(tmp_path: Path) -> None:
    with pytest.raises(GeminiVideoError, match="unsupported_resolution"):
        _provider(lambda req: pytest.fail("호출하면 안 된다")).generate(
            _request(tmp_path, resolution="1080p")
        )


def test_registry_routes_by_kind(tmp_path: Path) -> None:
    adapter = registry.get_adapter("gemini")
    assert isinstance(adapter, GeminiImageProvider)
    # 영상 요청은 영상 어댑터로 간다: 이미지 키만 있고 영상 키가 없으면 영상 키 오류가 난다
    with pytest.raises(GeminiVideoError, match="model_not_configured"):
        adapter.generate(_request(tmp_path, model=""))

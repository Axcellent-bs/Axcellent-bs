"""Runway 영상 어댑터: 가짜 전송으로만 검증한다(실제 호출·과금 없음)."""

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from marketing_agent.media import pricing, registry
from marketing_agent.media.base import MediaProviderError, MediaRequest
from marketing_agent.media.runway_video import API_HOST, API_VERSION, RunwayVideoProvider

KEY = "runway-SECRET-789"
MUTED_MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
VIDEO_URL = "https://cdn.example.test/out.mp4"
TASK = "11111111-2222-4333-8444-555555555555"


def public_resolver(host: str, port: int, **_kw: object) -> list[tuple]:
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def _request(tmp_path: Path, *, model: str = "veo3.1", duration_s: int | None = 6, **over):
    values = {
        "media_kind": "video",
        "prompt": "a cat on a book",
        "prompt_sha256": "c" * 64,
        "resolution": "720p",
        "quality": None,
        "duration_s": duration_s,
        "remove_audio": True,
        "model": model,
        "output_dir": tmp_path,
    } | over
    return MediaRequest(**values)


def _handler(tasks: list[dict], seen: dict):
    queue = list(tasks)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            seen["version"] = request.headers.get("x-runway-version")
            return httpx.Response(200, json={"id": TASK})
        if str(request.url) == f"{API_HOST}/v1/tasks/{TASK}":
            return httpx.Response(200, json=queue.pop(0) if len(queue) > 1 else queue[0])
        if str(request.url) == VIDEO_URL:
            seen["download_auth"] = request.headers.get("authorization")
            return httpx.Response(200, content=MUTED_MP4)
        return httpx.Response(404)

    return handler


def _adapter(handler) -> RunwayVideoProvider:
    return RunwayVideoProvider(
        httpx.MockTransport(handler),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=public_resolver,
    )


def test_veo_request_poll_and_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    seen: dict = {}
    tasks = [
        {"status": "THROTTLED"},
        {"status": "RUNNING"},
        {"status": "SUCCEEDED", "output": [VIDEO_URL]},
    ]
    result = _adapter(_handler(tasks, seen)).generate(_request(tmp_path))
    assert seen["url"] == f"{API_HOST}/v1/text_to_video"
    assert seen["body"] == {
        "model": "veo3.1",
        "promptText": "a cat on a book",
        "ratio": "720:1280",
        "duration": 6,
        "audio": False,
    }
    assert seen["auth"] == f"Bearer {KEY}" and seen["version"] == API_VERSION
    assert seen["download_auth"] is None  # 결과 URL에는 키를 보내지 않는다
    assert result.provider == "runway" and result.model_id == "veo3.1"
    assert result.usage is None and result.provider_job_id == TASK
    assert result.asset_path.read_bytes() == MUTED_MP4


def test_gen45_has_no_audio_field_and_wide_durations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    seen: dict = {}
    done = [{"status": "SUCCEEDED", "output": [VIDEO_URL]}]
    _adapter(_handler(done, seen)).generate(_request(tmp_path, model="gen4.5", duration_s=5))
    assert "audio" not in seen["body"] and seen["body"]["duration"] == 5


def test_veo_rejects_durations_outside_enum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(500)

    with pytest.raises(MediaProviderError, match="unsupported_duration: 5"):
        _adapter(handler).generate(_request(tmp_path, duration_s=5))
    assert calls == []  # 요청 전에 거부


def test_unknown_model_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    adapter = _adapter(lambda _r: httpx.Response(500))
    with pytest.raises(MediaProviderError, match="invalid_model_id"):
        adapter.generate(_request(tmp_path, model="gen4_turbo"))  # 이미지→영상 전용


def test_keep_audio_mode_still_requests_silent_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """소리를 두는 모드여도 소리 단가가 가격표에 없어 audio=false만 보낸다(무음 영상으로 받는다)."""
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    seen: dict = {}
    tasks = [{"status": "SUCCEEDED", "output": [VIDEO_URL]}]
    _adapter(_handler(tasks, seen)).generate(_request(tmp_path, remove_audio=False))
    assert seen["body"]["audio"] is False


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
def test_failed_states_reject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    body = {"status": status, "failure": "secret text", "failureCode": "SAFETY.INPUT.TEXT"}
    with pytest.raises(MediaProviderError, match=f"status_{status}") as info:
        _adapter(_handler([body], {})).generate(_request(tmp_path))
    assert "secret text" not in str(info.value)  # 사람용 문구는 내지 않는다


def test_missing_output_and_http_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    with pytest.raises(MediaProviderError, match="no_video_in_response"):
        _adapter(_handler([{"status": "SUCCEEDED", "output": []}], {})).generate(_request(tmp_path))
    with pytest.raises(MediaProviderError, match="http_429"):
        _adapter(lambda _r: httpx.Response(429)).generate(_request(tmp_path))


def test_common_rejections_and_key_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MARKETING_RUNWAY_API_KEY", raising=False)
    monkeypatch.setenv("MARKETING_VIDEO_API_KEY", "google-key")  # 다른 공급자 키는 쓰지 않는다
    adapter = _adapter(lambda _r: httpx.Response(500))
    with pytest.raises(MediaProviderError, match="api_key_not_configured"):
        adapter.generate(_request(tmp_path))
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    with pytest.raises(MediaProviderError, match="unsupported_media_kind"):
        adapter.generate(_request(tmp_path, media_kind="image"))
    with pytest.raises(MediaProviderError, match="unsupported_resolution"):
        adapter.generate(_request(tmp_path, resolution="1080p"))
    with pytest.raises(MediaProviderError, match="model_not_configured"):
        adapter.generate(_request(tmp_path, model=""))


def test_download_refuses_private_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_RUNWAY_API_KEY", KEY)
    body = {"status": "SUCCEEDED", "output": ["https://169.254.169.254/a.mp4"]}
    with pytest.raises(MediaProviderError, match="blocked_video_host"):
        _adapter(_handler([body], {})).generate(_request(tmp_path))


def test_registry_and_prices() -> None:
    assert isinstance(registry.get_adapter("runway"), RunwayVideoProvider)
    for model, usd in (("gen4.5", "0.12"), ("veo3.1", "0.20"), ("veo3.1_fast", "0.10")):
        entry = pricing.find_entry(
            provider="runway", model=model, kind="video", resolution="720p", quality=None
        )
        assert entry.per_second_usd is not None
        assert pricing.estimate_usd(entry, prompt="x", duration_s=1) == Decimal(usd)
        with pytest.raises(pricing.PriceNotFound):  # 표의 길이 상한(8초) 초과
            pricing.estimate_usd(entry, prompt="x", duration_s=9)

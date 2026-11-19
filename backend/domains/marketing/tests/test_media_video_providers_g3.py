"""Pika·Luma·Vidu 영상 어댑터: 가짜 전송으로만 검증한다(실제 호출·과금 없음, 키는 가짜 값)."""

import json
from pathlib import Path

import httpx
import pytest

from marketing_agent.media import pricing, registry
from marketing_agent.media.base import MediaProviderError, MediaRequest
from marketing_agent.media.luma_video import LumaVideoProvider
from marketing_agent.media.pika_video import PikaVideoProvider
from marketing_agent.media.vidu_video import ViduVideoProvider

MUTED_MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
VIDEO_URL = "https://cdn.example.test/out.mp4"
KEY = "fake-SECRET-789"


def public_resolver(host: str, port: int, **_kw: object) -> list[tuple]:
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def _request(tmp_path: Path, *, model: str, duration_s: int = 5) -> MediaRequest:
    return MediaRequest(
        media_kind="video",
        prompt="a cat on a book",
        prompt_sha256="c" * 64,
        resolution="720p",
        quality=None,
        duration_s=duration_s,
        remove_audio=True,
        model=model,
        output_dir=tmp_path,
    )


def _provider(cls, post_response: dict, polls: list[dict], seen: dict):
    queue = list(polls)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json=post_response)
        if str(request.url) == VIDEO_URL:
            seen["download_headers"] = dict(request.headers)
            return httpx.Response(200, content=MUTED_MP4)
        seen["poll_url"] = str(request.url)
        return httpx.Response(200, json=queue.pop(0) if len(queue) > 1 else queue[0])

    return cls(
        httpx.MockTransport(handler),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=public_resolver,
    )


def test_pika_25_request_and_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_PIKA_API_KEY", KEY)
    seen: dict = {}
    adapter = _provider(
        PikaVideoProvider,
        {"id": "media_1", "status": "queued"},
        [{"status": "running"}, {"status": "completed", "output": {"video": {"url": VIDEO_URL}}}],
        seen,
    )
    result = adapter.generate(_request(tmp_path, model="pika-2.5"))
    assert seen["url"] == "https://api.dev.pika.art/v1/media/pika/pika-2.5/text-to-video"
    assert seen["body"] == {
        "prompt": "a cat on a book",
        "aspect_ratio": "9:16",
        "duration_s": 5,
        "resolution": "720p",
    }
    assert seen["headers"]["x-api-key"] == KEY and "authorization" not in seen["headers"]
    assert seen["poll_url"].endswith("/v1/media/jobs/media_1")
    assert "x-api-key" not in seen["download_headers"]  # 결과 URL에 키를 보내지 않는다
    assert result.provider == "pika" and result.asset_path.read_bytes() == MUTED_MP4


def test_pika_25_rejects_non_5s_and_bad_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_PIKA_API_KEY", KEY)
    adapter = _provider(PikaVideoProvider, {"id": "x"}, [{"status": "queued"}], {})
    with pytest.raises(MediaProviderError, match="unsupported_duration"):
        adapter.generate(_request(tmp_path, model="pika-2.5", duration_s=8))
    with pytest.raises(MediaProviderError, match="invalid_model_id"):
        adapter.generate(_request(tmp_path, model="../evil"))


def test_pika_h3_flex_body_and_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_PIKA_API_KEY", KEY)
    seen: dict = {}
    adapter = _provider(
        PikaVideoProvider,
        {"id": "m2", "status": "queued"},
        [{"status": "failed", "error": {"code": "content_moderation"}}],
        seen,
    )
    with pytest.raises(MediaProviderError, match="status_content_moderation"):
        adapter.generate(_request(tmp_path, model="pika-h3-flex", duration_s=8))
    assert seen["body"]["duration"] == 8 and seen["body"]["resolution"] == "768p"
    assert "duration_s" not in seen["body"]


def test_pika_submit_rejected_in_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_PIKA_API_KEY", KEY)
    adapter = _provider(
        PikaVideoProvider,
        {"id": "m", "status": "failed", "error": {"code": "invalid_input"}},
        [{"status": "queued"}],
        {},
    )
    with pytest.raises(MediaProviderError, match="rejected_invalid_input"):
        adapter.generate(_request(tmp_path, model="pika-2.5"))


def test_luma_request_and_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_LUMA_API_KEY", KEY)
    seen: dict = {}
    adapter = _provider(
        LumaVideoProvider,
        {"id": "g1", "state": "queued"},
        [{"state": "dreaming"}, {"state": "completed", "output": [{"url": VIDEO_URL}]}],
        seen,
    )
    result = adapter.generate(_request(tmp_path, model="ray-3.2"))
    assert seen["url"] == "https://agents.lumalabs.ai/v1/generations"
    assert seen["body"]["video"] == {"resolution": "720p", "duration": "5s"}
    assert seen["body"]["aspect_ratio"] == "9:16" and seen["body"]["type"] == "video"
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["poll_url"].endswith("/v1/generations/g1")
    assert result.provider == "luma" and result.model_id == "ray-3.2"


def test_luma_failure_and_duration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_LUMA_API_KEY", KEY)
    adapter = _provider(
        LumaVideoProvider,
        {"id": "g"},
        [{"state": "failed", "failure_code": "moderation"}],
        {},
    )
    with pytest.raises(MediaProviderError, match="status_failed_moderation"):
        adapter.generate(_request(tmp_path, model="ray-3.2"))
    with pytest.raises(MediaProviderError, match="unsupported_duration"):
        adapter.generate(_request(tmp_path, model="ray-3.2", duration_s=8))


def test_vidu_request_and_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_VIDU_API_KEY", KEY)
    seen: dict = {}
    adapter = _provider(
        ViduVideoProvider,
        {"task_id": "t1", "state": "created"},
        [
            {"state": "queueing"},
            {"state": "success", "creations": [{"url": VIDEO_URL, "cover_url": "x"}]},
        ],
        seen,
    )
    result = adapter.generate(_request(tmp_path, model="viduq3-turbo", duration_s=6))
    assert seen["url"] == "https://api.vidu.com/ent/v2/text2video"
    assert seen["body"] == {
        "model": "viduq3-turbo",
        "prompt": "a cat on a book",
        "duration": 6,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "audio": False,
    }
    assert seen["headers"]["authorization"] == f"Token {KEY}"  # Bearer가 아니라 Token
    assert seen["poll_url"].endswith("/ent/v2/tasks/t1/creations")
    assert result.provider == "vidu"


def test_vidu_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_VIDU_API_KEY", KEY)
    adapter = _provider(
        ViduVideoProvider, {"task_id": "t"}, [{"state": "failed", "err_code": "E123"}], {}
    )
    with pytest.raises(MediaProviderError, match="status_failed_E123"):
        adapter.generate(_request(tmp_path, model="viduq3-turbo"))


def test_missing_key_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for env in ("MARKETING_PIKA_API_KEY", "MARKETING_LUMA_API_KEY", "MARKETING_VIDU_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    for cls in (PikaVideoProvider, LumaVideoProvider, ViduVideoProvider):
        with pytest.raises(MediaProviderError, match="api_key_not_configured"):
            _provider(cls, {}, [{}], {}).generate(_request(tmp_path, model="m"))


def test_registry_and_prices() -> None:
    assert isinstance(registry.get_adapter("pika"), PikaVideoProvider)
    assert isinstance(registry.get_adapter("luma"), LumaVideoProvider)
    assert isinstance(registry.get_adapter("vidu"), ViduVideoProvider)
    entry = pricing.find_entry(
        provider="pika", model="pika-2.5", kind="video", resolution="720p", quality=None
    )
    assert str(pricing.estimate_usd(entry, prompt="x", duration_s=5)) == "0.20"
    for provider, model in (("luma", "ray-3.2"), ("vidu", "viduq3-turbo")):  # 가격 없음 -> 차단
        with pytest.raises(pricing.PriceNotFound):
            pricing.find_entry(
                provider=provider, model=model, kind="video", resolution="720p", quality=None
            )

"""fal·Replicate·Higgsfield·Wan·PixVerse 영상 어댑터: 가짜 전송으로만 검증한다(실제 호출·과금
없음)."""

import json
from pathlib import Path

import httpx
import pytest

from marketing_agent.media import registry
from marketing_agent.media.base import MediaProviderError, MediaRequest
from marketing_agent.media.fal_video import FalVideoProvider
from marketing_agent.media.higgsfield_video import HiggsfieldVideoProvider
from marketing_agent.media.pixverse_video import PixVerseVideoProvider
from marketing_agent.media.replicate_video import ReplicateVideoProvider
from marketing_agent.media.wan_video import WanVideoProvider

MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
VIDEO_URL = "https://cdn.example.test/out.mp4"
JOB = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"


def public_resolver(host: str, port: int, **_kw: object) -> list[tuple]:
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def _request(tmp_path: Path, model: str, duration_s: int = 5) -> MediaRequest:
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


def _run(cls, handler, tmp_path: Path, model: str, seen: list[httpx.Request]):
    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == VIDEO_URL:
            return httpx.Response(200, content=MP4)
        return handler(request)

    provider = cls(
        httpx.MockTransport(wrapped),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=public_resolver,
    )
    return provider.generate(_request(tmp_path, model))


def test_fal_queue_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_FAL_API_KEY", "fal-SECRET")
    states = ["IN_QUEUE", "COMPLETED"]
    base = "https://queue.fal.run/fal-ai/ltx-video"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            assert url == f"{base}/image-to-video"
            assert request.headers["authorization"] == "Key fal-SECRET"
            assert json.loads(request.content)["aspect_ratio"] == "9:16"
            return httpx.Response(
                200, json={"request_id": JOB, "status_url": "https://evil.test/x"}
            )
        if url == f"{base}/requests/{JOB}/status":  # owner/app 두 칸만
            return httpx.Response(200, json={"status": states.pop(0)})
        if url == f"{base}/requests/{JOB}":
            return httpx.Response(200, json={"video": {"url": VIDEO_URL}})
        return httpx.Response(404)

    seen: list[httpx.Request] = []
    result = _run(FalVideoProvider, handler, tmp_path, "fal-ai/ltx-video/image-to-video", seen)
    assert result.provider == "fal" and result.asset_path.read_bytes() == MP4
    assert all("evil.test" not in str(r.url) for r in seen)
    assert "authorization" not in seen[-1].headers  # 내려받기에는 키를 보내지 않는다


def test_fal_failed_status_and_bad_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_FAL_API_KEY", "fal-SECRET")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": JOB})
        return httpx.Response(200, json={"status": "FAILED"})

    with pytest.raises(MediaProviderError, match="status_FAILED"):
        _run(FalVideoProvider, handler, tmp_path, "fal-ai/ltx-video", seen)
    with pytest.raises(MediaProviderError, match="invalid_model_id"):
        _run(FalVideoProvider, handler, tmp_path, "../x", seen)


def test_replicate_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_REPLICATE_API_KEY", "rep-SECRET")
    states = [{"status": "starting"}, {"status": "succeeded", "output": [VIDEO_URL]}]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            assert url == "https://api.replicate.com/v1/models/acme/video-model/predictions"
            assert request.headers["authorization"] == "Bearer rep-SECRET"
            assert json.loads(request.content)["input"]["prompt"] == "a cat on a book"
            return httpx.Response(201, json={"id": "abcd1234efgh"})
        if url == "https://api.replicate.com/v1/predictions/abcd1234efgh":
            return httpx.Response(200, json=states.pop(0))
        return httpx.Response(404)

    result = _run(ReplicateVideoProvider, handler, tmp_path, "acme/video-model", [])
    assert result.provider == "replicate" and result.asset_path.read_bytes() == MP4


def test_replicate_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_REPLICATE_API_KEY", "rep-SECRET")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "abcd1234efgh"})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})

    with pytest.raises(MediaProviderError, match="status_failed"):
        _run(ReplicateVideoProvider, handler, tmp_path, "acme/video-model", [])


def test_higgsfield_flow_and_terminal_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_API_KEY", "ID1:SEC1")
    states = [
        {"status": "queued"},
        {"status": "in_progress"},
        {"status": "completed", "video": {"url": VIDEO_URL}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            assert url == "https://api.higgsfield.ai/wan/v2.7/reference-to-video"
            assert request.headers["authorization"] == "Key ID1:SEC1"
            return httpx.Response(200, json={"status": "queued", "request_id": JOB})
        assert url == f"https://api.higgsfield.ai/requests/{JOB}/status"
        return httpx.Response(200, json=states.pop(0))

    result = _run(HiggsfieldVideoProvider, handler, tmp_path, "wan/v2.7/reference-to-video", [])
    assert result.provider == "higgsfield" and result.asset_path.read_bytes() == MP4

    for bad in ("nsfw", "failed", "canceled"):
        monkeypatch.setenv("MARKETING_HIGGSFIELD_API_KEY", "ID1:SEC1")

        def failing(request: httpx.Request, bad: str = bad) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"request_id": JOB})
            return httpx.Response(200, json={"status": bad})

        with pytest.raises(MediaProviderError, match=f"status_{bad}"):
            _run(HiggsfieldVideoProvider, failing, tmp_path, "wan/v2.7/reference-to-video", [])


def test_wan_flow_requires_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_WAN_API_KEY", "wan-SECRET")
    monkeypatch.delenv("MARKETING_WAN_WORKSPACE_ID", raising=False)
    with pytest.raises(MediaProviderError, match="workspace_not_configured"):
        _run(WanVideoProvider, lambda r: httpx.Response(404), tmp_path, "wan2.7-t2v", [])

    monkeypatch.setenv("MARKETING_WAN_WORKSPACE_ID", "ws-1")
    host = "https://ws-1.ap-southeast-1.maas.aliyuncs.com"
    states = [
        {"output": {"task_status": "RUNNING"}},
        {"output": {"task_status": "SUCCEEDED", "video_url": VIDEO_URL}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            assert url == f"{host}/api/v1/services/aigc/video-generation/video-synthesis"
            assert request.headers["x-dashscope-async"] == "enable"
            body = json.loads(request.content)
            assert body["parameters"]["ratio"] == "9:16"
            assert body["parameters"]["resolution"] == "720P"
            return httpx.Response(200, json={"output": {"task_id": JOB, "task_status": "PENDING"}})
        assert url == f"{host}/api/v1/tasks/{JOB}"
        return httpx.Response(200, json=states.pop(0))

    result = _run(WanVideoProvider, handler, tmp_path, "wan2.7-t2v", [])
    assert result.provider == "wan" and result.asset_path.read_bytes() == MP4


def test_wan_rejects_bad_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_WAN_API_KEY", "wan-SECRET")
    monkeypatch.setenv("MARKETING_WAN_WORKSPACE_ID", "evil.test/x")
    with pytest.raises(MediaProviderError, match="workspace_not_configured"):
        _run(WanVideoProvider, lambda r: httpx.Response(404), tmp_path, "wan2.7-t2v", [])


def test_pixverse_flow_and_errcode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_PIXVERSE_API_KEY", "pix-SECRET")
    states = [{"status": 5}, {"status": 1, "url": VIDEO_URL}]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers["api-key"] == "pix-SECRET" and request.headers["ai-trace-id"]
        if request.method == "POST":
            assert url == "https://app-api.pixverse.ai/openapi/v2/video/text/generate"
            body = json.loads(request.content)
            assert body["aspect_ratio"] == "9:16" and body["generate_audio_switch"] is False
            return httpx.Response(
                200, json={"ErrCode": 0, "Resp": {"video_id": 123, "credits": 10}}
            )
        assert url == "https://app-api.pixverse.ai/openapi/v2/video/result/123"
        return httpx.Response(200, json={"ErrCode": 0, "Resp": states.pop(0)})

    result = _run(PixVerseVideoProvider, handler, tmp_path, "v5", [])
    assert result.provider == "pixverse" and result.asset_path.read_bytes() == MP4

    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ErrCode": 400017, "ErrMsg": "Invalid parameter"})

    with pytest.raises(MediaProviderError, match="err_400017"):
        _run(PixVerseVideoProvider, rejected, tmp_path, "v5", [])


def test_registry_has_group4_providers() -> None:
    for name, cls in {
        "fal": FalVideoProvider,
        "replicate": ReplicateVideoProvider,
        "higgsfield": HiggsfieldVideoProvider,
        "wan": WanVideoProvider,
        "pixverse": PixVerseVideoProvider,
    }.items():
        assert isinstance(registry.get_adapter(name), cls)


def test_keys_not_in_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_REPLICATE_API_KEY", "rep-SECRET")
    with pytest.raises(MediaProviderError) as info:
        _run(
            ReplicateVideoProvider, lambda r: httpx.Response(401), tmp_path, "acme/video-model", []
        )
    assert "rep-SECRET" not in str(info.value)

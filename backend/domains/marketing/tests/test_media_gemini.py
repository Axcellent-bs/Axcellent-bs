"""Gemini 이미지 어댑터: 가짜 전송으로 요청·응답·키 미노출·거부·오류를 검증한다(네트워크 없음)."""

import base64
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from marketing_agent.media import pricing, registry
from marketing_agent.media.base import MediaRequest
from marketing_agent.media.gemini_image import (
    ASPECT_RATIOS,
    ENDPOINT,
    REQUEST_MIME,
    GeminiImageError,
    GeminiImageProvider,
)

KEY = "test-key-SECRET-123"
MODEL = "test-model-id"
JPG = b"\xff\xd8\xff\xe0fake-image-bytes"
PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def _request(tmp_path: Path, resolution: str = "928x1152", model: str = MODEL) -> MediaRequest:
    return MediaRequest(
        media_kind="image",
        prompt="a cat on a book",
        prompt_sha256="a" * 64,
        resolution=resolution,
        quality=None,
        duration_s=None,
        remove_audio=True,
        model=model,
        output_dir=tmp_path,
    )


def _ok_body(
    mime: str | None = "image/jpeg", data: bytes = JPG, **extra: object
) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "interaction_1",
        "status": "completed",
        "steps": [
            {"type": "thought", "summary": []},
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": "done"},
                    {
                        "type": "image",
                        **({"mime_type": mime} if mime else {}),
                        "data": base64.b64encode(data).decode(),
                    },
                ],
            },
        ],
        "usage": {"total_input_tokens": 12, "total_output_tokens": 1120},
    }
    body.update(extra)
    return body


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> GeminiImageProvider:
    return GeminiImageProvider(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_IMAGE_API_KEY", KEY)


def test_request_shape_and_result(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_ok_body())

    result = _provider(handler).generate(_request(tmp_path))
    req = seen[0]
    assert str(req.url) == ENDPOINT and KEY not in str(req.url)
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["model"] == MODEL
    assert body["input"] == [{"type": "text", "text": "a cat on a book"}]
    fmt = body["response_format"]
    assert fmt["type"] == "image" and fmt["aspect_ratio"] == "4:5" and fmt["image_size"] == "1K"
    assert KEY not in req.content.decode()
    assert fmt["mime_type"] == "image/jpeg"
    assert result.asset_path.name == "asset.jpg"
    assert result.asset_path.read_bytes() == JPG
    assert result.sha256 == hashlib.sha256(JPG).hexdigest()
    assert result.provider == "gemini" and result.provider_job_id == "interaction_1"
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 1120)
    assert KEY not in repr(result)


@pytest.mark.parametrize("resolution", ["1024x1280", "1536x1024", "720p", "1024x1024 "])
def test_unsupported_resolution_rejected_before_call(tmp_path: Path, resolution: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("호출하면 안 된다")

    with pytest.raises(GeminiImageError, match="unsupported_resolution"):
        _provider(handler).generate(_request(tmp_path, resolution))


def test_supported_resolutions_match_price_table() -> None:
    for resolution in ASPECT_RATIOS:
        entry = pricing.find_entry(
            provider="gemini",
            model="gemini-nano-banana-2.1",
            kind="image",
            resolution=resolution,
            quality=None,
        )
        assert entry.output_tokens == 1120


def test_missing_key_or_model_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("호출하면 안 된다")

    with pytest.raises(GeminiImageError, match="model_not_configured"):
        _provider(handler).generate(_request(tmp_path, model=""))
    monkeypatch.delenv("MARKETING_IMAGE_API_KEY")
    with pytest.raises(GeminiImageError, match="api_key_not_configured"):
        _provider(handler).generate(_request(tmp_path))


def test_http_error_does_not_leak_key_or_body(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"denied for {KEY}")

    with pytest.raises(GeminiImageError) as info:
        _provider(handler).generate(_request(tmp_path))
    assert str(info.value) == "http_403" and KEY not in repr(info.value)
    assert list(tmp_path.iterdir()) == []


def test_transport_error_does_not_leak_key(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {KEY}", request=req)

    with pytest.raises(GeminiImageError) as info:
        _provider(handler).generate(_request(tmp_path))
    assert KEY not in str(info.value) and info.value.__cause__ is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["x"]),
        httpx.Response(200, json=_ok_body(status="failed")),
        httpx.Response(200, json={"id": "x", "status": "completed", "steps": []}),
        httpx.Response(
            200,
            json={
                "steps": [
                    {"type": "model_output", "content": [{"type": "image", "data": "!!notb64"}]}
                ]
            },
        ),
    ],
)
def test_bad_responses_raise(tmp_path: Path, response: httpx.Response) -> None:
    with pytest.raises(GeminiImageError):
        _provider(lambda req: response).generate(_request(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_usage_missing_is_none(tmp_path: Path) -> None:
    body = _ok_body()
    del body["usage"]
    result = _provider(lambda req: httpx.Response(200, json=body)).generate(_request(tmp_path))
    assert result.usage is None


def test_registered() -> None:
    assert isinstance(registry.get_adapter("gemini"), GeminiImageProvider)
    assert registry.get_adapter("mock") is not None


def test_request_mime_is_jpeg_only() -> None:
    assert REQUEST_MIME == "image/jpeg"


def test_missing_response_mime_defaults_to_jpeg(tmp_path: Path) -> None:
    result = _provider(lambda req: httpx.Response(200, json=_ok_body(mime=None))).generate(
        _request(tmp_path)
    )
    assert result.asset_path.name == "asset.jpg"


def test_response_block_mime_wins(tmp_path: Path) -> None:
    body = _ok_body(mime="image/png", data=PNG)
    result = _provider(lambda req: httpx.Response(200, json=body)).generate(_request(tmp_path))
    assert result.asset_path.name == "asset.png"
    assert result.sha256 == hashlib.sha256(PNG).hexdigest()


def test_http_400_reports_kind_only_and_drops_body(tmp_path: Path) -> None:
    text = f"The value 'image/png' is not supported for 'response_format.mime_type' {KEY}"

    with pytest.raises(GeminiImageError) as info:
        _provider(lambda req: httpx.Response(400, text=text)).generate(_request(tmp_path))
    assert str(info.value) == "http_400"
    assert "image/png" not in repr(info.value) and KEY not in repr(info.value)
    assert list(tmp_path.iterdir()) == []


def test_thought_tokens_are_not_read_into_usage(tmp_path: Path) -> None:
    """현재 동작을 고정한다: total_thought_tokens는 Usage에 들어가지 않는다([확인 필요])."""
    body = _ok_body(
        usage={
            "total_input_tokens": 17,
            "total_output_tokens": 1440,
            "total_thought_tokens": 1059,
            "total_tokens": 2516,
        }
    )
    result = _provider(lambda req: httpx.Response(200, json=body)).generate(_request(tmp_path))
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (17, 1440)

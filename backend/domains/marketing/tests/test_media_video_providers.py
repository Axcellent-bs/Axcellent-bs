"""Seedance·Kling 영상 어댑터와 공급자 전환: 가짜 전송으로만 검증한다(실제 호출·과금 없음)."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from marketing_agent.media import pricing, registry
from marketing_agent.media.base import MediaProviderError, MediaRequest
from marketing_agent.media.kling_video import API_HOST, KlingVideoProvider
from marketing_agent.media.seedance_video import TASKS_ENDPOINT, SeedanceVideoProvider

SEED_KEY = "seed-SECRET-123"
KLING_KEY = "kling-SECRET-456"
MUTED_MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
VIDEO_URL = "https://cdn.example.test/out.mp4"


def public_resolver(host: str, port: int, **_kw: object) -> list[tuple]:
    """네트워크 없이 모든 이름을 공용 IP로 푼다."""
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def _request(
    tmp_path: Path, *, model: str = "m-1", duration_s: int | None = 5, **over
) -> MediaRequest:
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


def _provider(cls, handler, calls: list[httpx.Request] | None = None):
    def wrapped(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    return cls(
        httpx.MockTransport(wrapped),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=public_resolver,
    )


# --- Seedance ---
def _seedance_handler(states: list[dict], seen: dict):
    queue = list(states)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"id": "task_1"})
        if str(request.url) == f"{TASKS_ENDPOINT}/task_1":
            return httpx.Response(200, json=queue.pop(0) if len(queue) > 1 else queue[0])
        if str(request.url) == VIDEO_URL:
            seen["download_auth"] = request.headers.get("authorization")
            return httpx.Response(200, content=MUTED_MP4)
        return httpx.Response(404)

    return handler


def test_seedance_request_poll_and_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    seen: dict = {}
    done = {
        "id": "task_1",
        "status": "succeeded",
        "model": "m-1-resp",
        "content": {"video_url": VIDEO_URL},
        "usage": {"completion_tokens": 1234},
    }
    adapter = _provider(
        SeedanceVideoProvider, _seedance_handler([{"status": "running"}, done], seen)
    )
    result = adapter.generate(_request(tmp_path))
    assert seen["body"]["ratio"] == "9:16" and seen["body"]["generate_audio"] is False
    assert seen["body"]["model"] == "m-1" and seen["body"]["duration"] == 5
    assert seen["auth"] == f"Bearer {SEED_KEY}"
    assert seen["download_auth"] is None  # 결과 URL에는 키를 보내지 않는다
    assert result.provider == "seedance" and result.model_id == "m-1-resp"
    assert result.usage is not None and result.usage.output_tokens == 1234
    assert result.provider_job_id == "task_1" and result.asset_path.read_bytes() == MUTED_MP4


@pytest.mark.parametrize("status", ["failed", "expired", "cancelled"])
def test_seedance_failed_states_reject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str
) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    body = {"status": status, "error": {"code": "SensitiveContent", "message": "x"}}
    adapter = _provider(SeedanceVideoProvider, _seedance_handler([body], {}))
    with pytest.raises(MediaProviderError, match=f"status_{status}"):
        adapter.generate(_request(tmp_path))


def test_seedance_poll_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    clock = iter(range(0, 10_000, 100))
    adapter = SeedanceVideoProvider(
        httpx.MockTransport(_seedance_handler([{"status": "queued"}], {})),
        poll_interval_s=1,
        poll_max_wait_s=250,
        sleep=lambda _s: None,
        monotonic=lambda: float(next(clock)),
    )
    with pytest.raises(MediaProviderError, match="poll_timeout"):
        adapter.generate(_request(tmp_path))


# --- Kling ---
def _kling_handler(rows: list[dict], seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"code": 0, "data": {"id": "k1", "status": "submitted"}}
            )
        if request.url.path == "/tasks":
            seen["query"] = request.url.query.decode()
            return httpx.Response(200, json={"data": [rows.pop(0) if len(rows) > 1 else rows[0]]})
        if str(request.url) == VIDEO_URL:
            return httpx.Response(200, content=MUTED_MP4)
        return httpx.Response(404)

    return handler


def test_kling_request_poll_and_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_KLING_API_KEY", KLING_KEY)
    seen: dict = {}
    rows = [
        {"status": "processing"},
        {"status": "succeeded", "outputs": [{"type": "video", "url": VIDEO_URL}]},
    ]
    result = _provider(KlingVideoProvider, _kling_handler(rows, seen)).generate(
        _request(tmp_path, model="kling-x")
    )
    assert seen["path"] == "/text-to-video/kling-x"  # 모델은 본문이 아니라 경로
    assert seen["body"]["settings"] == {"aspect_ratio": "9:16", "duration": 5, "resolution": "720p"}
    assert "model" not in seen["body"] and seen["query"] == "task_ids=k1"
    assert seen["auth"] == f"Bearer {KLING_KEY}"
    assert result.provider == "kling" and result.model_id == "kling-x" and result.usage is None
    assert result.asset_path.read_bytes() == MUTED_MP4


def test_kling_failure_and_missing_video(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_KLING_API_KEY", KLING_KEY)
    with pytest.raises(MediaProviderError, match="status_failed"):
        _provider(KlingVideoProvider, _kling_handler([{"status": "failed"}], {})).generate(
            _request(tmp_path)
        )
    with pytest.raises(MediaProviderError, match="no_video_in_response"):
        _provider(
            KlingVideoProvider, _kling_handler([{"status": "succeeded", "outputs": []}], {})
        ).generate(_request(tmp_path))


def test_kling_rejects_bad_model_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_KLING_API_KEY", KLING_KEY)
    with pytest.raises(MediaProviderError, match="invalid_model_id"):
        _provider(KlingVideoProvider, _kling_handler([{}], {})).generate(
            _request(tmp_path, model="../x?y")
        )
    assert API_HOST.startswith("https://")


# --- 공통 거부 ---
@pytest.mark.parametrize("cls", [SeedanceVideoProvider, KlingVideoProvider])
def test_common_rejections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cls: type, request
) -> None:
    for env in ("MARKETING_SEEDANCE_API_KEY", "MARKETING_KLING_API_KEY", "MARKETING_VIDEO_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    adapter = cls(httpx.MockTransport(lambda _r: httpx.Response(500)))
    with pytest.raises(MediaProviderError, match="api_key_not_configured"):
        adapter.generate(_request(tmp_path))
    monkeypatch.setenv(cls.key_env, "k")
    with pytest.raises(MediaProviderError, match="unsupported_media_kind"):
        adapter.generate(_request(tmp_path, media_kind="image"))
    with pytest.raises(MediaProviderError, match="unsupported_resolution"):
        adapter.generate(_request(tmp_path, resolution="1080p"))
    with pytest.raises(MediaProviderError, match="unsupported_duration"):
        adapter.generate(_request(tmp_path, duration_s=1))
    with pytest.raises(MediaProviderError, match="model_not_configured"):
        adapter.generate(_request(tmp_path, model=""))
    with pytest.raises(MediaProviderError, match="http_500"):
        adapter.generate(_request(tmp_path))


def test_gemini_video_key_is_never_sent_to_other_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_API_KEY", "google-key")
    monkeypatch.delenv("MARKETING_SEEDANCE_API_KEY", raising=False)
    monkeypatch.delenv("MARKETING_KLING_API_KEY", raising=False)
    for cls in (SeedanceVideoProvider, KlingVideoProvider):
        with pytest.raises(MediaProviderError, match="api_key_not_configured"):
            cls(httpx.MockTransport(lambda _r: httpx.Response(500))).generate(_request(tmp_path))


def test_download_refuses_non_https(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    body = {"status": "succeeded", "content": {"video_url": "http://cdn.example.test/a.mp4"}}
    adapter = _provider(SeedanceVideoProvider, _seedance_handler([body], {}))
    with pytest.raises(MediaProviderError, match="unexpected_video_uri"):
        adapter.generate(_request(tmp_path))


# --- 레지스트리·가격표 ---
def test_registry_selects_providers_by_name() -> None:
    assert isinstance(registry.get_adapter("seedance"), SeedanceVideoProvider)
    assert isinstance(registry.get_adapter("kling"), KlingVideoProvider)
    assert registry.get_adapter("mock") is not None and registry.get_adapter("gemini") is not None
    assert registry.get_adapter("unknown-ai") is None  # 알 수 없는 공급자는 거절


def test_price_table_kling_per_second_and_seedance_blank() -> None:
    entry = pricing.find_entry(
        provider="kling", model="kling-4.0-flash", kind="video", resolution="720p", quality=None
    )
    assert entry.per_second_usd is not None
    assert str(pricing.estimate_usd(entry, prompt="x", duration_s=5)) == "0.470"
    with pytest.raises(pricing.PriceNotFound):  # 표의 길이 상한(8초) 초과
        pricing.estimate_usd(entry, prompt="x", duration_s=9)
    for model in ("anything", "dreamina-seedance-2-5-260628"):  # 가격 없음 → 차단
        with pytest.raises(pricing.PriceNotFound):
            pricing.find_entry(
                provider="seedance", model=model, kind="video", resolution="720p", quality=None
            )


def test_price_table_rejects_bad_per_second(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text(
        '[[price]]\nprovider="k"\nmodel="m"\nkind="video"\nresolution="720p"\n'
        'per_second_usd = 0\nsource="s"\nchecked_on="2026-11-18"\n',
        encoding="utf-8",
    )
    with pytest.raises(pricing.PriceTableError):
        pricing.load_table(path)


# --- 공용 주소 전용 내려받기(SSRF) ---
def _done_body(url: str) -> dict:
    return {"status": "succeeded", "content": {"video_url": url}}


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data",
        "https://127.0.0.1/a.mp4",
        "https://[::1]/a.mp4",
        "https://[::ffff:127.0.0.1]/a.mp4",
        "https://192.168.0.232/a.mp4",
        "https://2130706433/a.mp4",
    ],
)
def test_download_refuses_private_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    calls: list[httpx.Request] = []
    adapter = _provider(SeedanceVideoProvider, _seedance_handler([_done_body(url)], {}), calls)
    with pytest.raises(MediaProviderError, match="blocked_video_host"):
        adapter.generate(_request(tmp_path))
    assert all(r.url.host != urlsplit(url).hostname for r in calls)


def test_download_refuses_name_resolving_to_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def mixed(host: str, port: int, **_kw: object) -> list[tuple]:
        return [(2, 1, 6, "", ("93.184.216.34", port)), (2, 1, 6, "", ("10.0.0.5", port))]

    adapter = SeedanceVideoProvider(
        httpx.MockTransport(_seedance_handler([_done_body(VIDEO_URL)], {})),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=mixed,
    )
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    with pytest.raises(MediaProviderError, match="blocked_video_host"):
        adapter.generate(_request(tmp_path))


def test_download_redirect_to_private_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    inner = _seedance_handler([_done_body(VIDEO_URL)], {})
    hosts: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hosts.append(req.url.host)
        if req.url.host == "cdn.example.test":
            return httpx.Response(302, headers={"location": "https://169.254.169.254/x"})
        return inner(req)

    with pytest.raises(MediaProviderError, match="blocked_video_host"):
        _provider(SeedanceVideoProvider, handler).generate(_request(tmp_path))
    assert "169.254.169.254" not in hosts


def test_download_too_many_redirects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    inner = _seedance_handler([_done_body(VIDEO_URL)], {})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "cdn.example.test":
            return httpx.Response(302, headers={"location": "/loop"})
        return inner(req)

    with pytest.raises(MediaProviderError, match="too_many_redirects"):
        _provider(SeedanceVideoProvider, handler).generate(_request(tmp_path))


def test_download_content_length_over_cap_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    inner = _seedance_handler([_done_body(VIDEO_URL)], {})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "cdn.example.test":
            return httpx.Response(200, headers={"content-length": str(64 * 1024 * 1024 + 1)})
        return inner(req)

    with pytest.raises(MediaProviderError, match="invalid_video_size"):
        _provider(SeedanceVideoProvider, handler).generate(_request(tmp_path))


def test_real_service_uses_public_only_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marketing_agent.media.safe_fetch import PublicOnlyTransport

    built: list[object] = []

    def spy(*_args, **kwargs):
        built.append(kwargs.get("transport"))
        raise httpx.ConnectError("stop")

    monkeypatch.setenv("MARKETING_SEEDANCE_API_KEY", SEED_KEY)
    monkeypatch.setattr(httpx, "Client", spy)
    with pytest.raises(MediaProviderError):
        SeedanceVideoProvider().generate(_request(tmp_path))
    assert isinstance(built[0], PublicOnlyTransport)

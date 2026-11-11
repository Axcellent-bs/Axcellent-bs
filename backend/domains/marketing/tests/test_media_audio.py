"""Gemini 오디오(BGM) 어댑터·단가표·설정·장부: 가짜 전송으로 검증한다(네트워크 없음)."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from marketing_agent.media import pricing, registry
from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import MediaSettings, ffmpeg_bin, has_api_key
from marketing_agent.media.gemini_audio import (
    INSTRUMENTAL_CLAUSE,
    GeminiAudioError,
    GeminiAudioProvider,
    with_instrumental_clause,
)
from marketing_agent.media.gemini_video import API_REVISION, ENDPOINT
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.pricing import PriceNotFound, PriceTableError
from marketing_agent.media.prompts import PromptRejected, get_template, render_prompt

KEY = "test-key-SECRET-123"
MODEL = "test-audio-model"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 16
MP3_B64 = base64.b64encode(MP3).decode()
BGM_FIELDS = {"bgm_mood": "cozy", "bgm_genre": "lo-fi"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MARKETING_AUDIO_PROVIDER",
        "MARKETING_AUDIO_MODEL",
        "MARKETING_AUDIO_API_KEY",
        "MARKETING_AUDIO_MODE",
        "MARKETING_FFMPEG_BIN",
        "MARKETING_IMAGE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _request(
    tmp_path: Path,
    *,
    model: str = MODEL,
    prompt: str = "a calm lo-fi track",
    kind: str = "audio",
) -> MediaRequest:
    return MediaRequest(
        media_kind=kind,  # type: ignore[arg-type]
        prompt=prompt,
        prompt_sha256="c" * 64,
        resolution="30s",
        quality=None,
        duration_s=None,
        remove_audio=False,
        model=model,
        output_dir=tmp_path,
    )


def _body(block: dict[str, object] | None = None, **extra: object) -> dict[str, object]:
    block = block or {"type": "audio", "mime_type": "audio/mpeg", "data": MP3_B64}
    return {
        "id": "int_1",
        "status": "completed",
        "model": MODEL,
        "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "notes"}, block]},
        ],
    } | extra


def _provider(handler) -> GeminiAudioProvider:
    return GeminiAudioProvider(httpx.MockTransport(handler))


def _ok(seen: list[httpx.Request], body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body or _body())

    return handler


def test_request_shape_and_key_only_in_header(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    seen: list[httpx.Request] = []
    result = _provider(_ok(seen)).generate(_request(tmp_path))
    (req,) = seen
    assert req.method == "POST" and str(req.url) == ENDPOINT
    assert req.headers["x-goog-api-key"] == KEY and req.headers["Api-Revision"] == API_REVISION
    payload = json.loads(req.content)
    assert payload == {
        "model": MODEL,
        "input": with_instrumental_clause("a calm lo-fi track"),
        "response_format": {"type": "audio"},
    }
    assert KEY not in json.dumps(payload)
    assert result.asset_path == tmp_path / "asset.mp3" and result.asset_path.read_bytes() == MP3
    assert result.sha256 == hashlib.sha256(MP3).hexdigest()
    assert (result.provider, result.model_id, result.provider_job_id) == ("gemini", MODEL, "int_1")
    assert result.generated_at.tzinfo is not None and result.usage is None
    assert KEY not in repr(result)


def test_instrumental_clause_is_always_sent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    seen: list[httpx.Request] = []
    _provider(_ok(seen)).generate(_request(tmp_path, prompt="upbeat song with singing"))
    assert INSTRUMENTAL_CLAUSE in json.loads(seen[0].content)["input"]
    assert with_instrumental_clause(with_instrumental_clause("x")).count(INSTRUMENTAL_CLAUSE) == 1
    assert "no vocals" in INSTRUMENTAL_CLAUSE.lower()


def test_template_prompt_is_instrumental_and_checked() -> None:
    assert get_template("cat_bookstore_bgm").kind == "audio"
    assert INSTRUMENTAL_CLAUSE in render_prompt("cat_bookstore_bgm", BGM_FIELDS)
    with pytest.raises(PromptRejected):
        render_prompt("cat_bookstore_bgm", {"bgm_mood": "cozy", "bgm_genre": "Nike jingle"})


def test_usage_is_parsed_when_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    body = _body(usage={"total_input_tokens": 12, "total_output_tokens": 0})
    result = _provider(_ok([], body)).generate(_request(tmp_path))
    assert result.usage is not None and result.usage.input_tokens == 12


def test_missing_key_or_model_is_refused_without_calling(tmp_path: Path, monkeypatch) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(GeminiAudioError, match="api_key_not_configured"):
        _provider(_ok(seen)).generate(_request(tmp_path))
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    with pytest.raises(GeminiAudioError, match="model_not_configured"):
        _provider(_ok(seen)).generate(_request(tmp_path, model=""))
    monkeypatch.delenv("MARKETING_AUDIO_API_KEY")
    monkeypatch.setenv("MARKETING_IMAGE_API_KEY", KEY)  # 이미지 키로는 안 된다
    with pytest.raises(GeminiAudioError, match="api_key_not_configured"):
        _provider(_ok(seen)).generate(_request(tmp_path))
    assert seen == []


def test_wrong_media_kind_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    with pytest.raises(GeminiAudioError, match="unsupported_media_kind"):
        _provider(_ok([])).generate(_request(tmp_path, kind="video"))


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(500, text=f"boom {KEY}"), "http_500"),
        (httpx.Response(200, text="not json"), "invalid_json"),
        (httpx.Response(200, json=_body(status="failed")), "status_failed"),
        (httpx.Response(200, json={"steps": []}), "no_audio_in_response"),
        (
            httpx.Response(
                200, json=_body({"type": "audio", "mime_type": "audio/wav", "data": "AAAA"})
            ),
            "unsupported_audio_mime",
        ),
        (httpx.Response(200, json=_body({"type": "audio", "data": "***"})), "invalid_audio_data"),
        (
            httpx.Response(
                200, json=_body({"type": "audio", "data": base64.b64encode(b"RIFFxxxx").decode()})
            ),
            "invalid_audio",
        ),
    ],
)
def test_bad_responses_fail_without_leaking(tmp_path: Path, monkeypatch, response, code) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    with pytest.raises(GeminiAudioError, match=code) as info:
        _provider(lambda request: response).generate(_request(tmp_path))
    assert KEY not in str(info.value)
    assert not (tmp_path / "asset.mp3").exists()


def test_transport_error_hides_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect {KEY}")

    with pytest.raises(GeminiAudioError) as info:
        _provider(boom).generate(_request(tmp_path))
    assert KEY not in str(info.value) and "request_failed" in str(info.value)


def test_registry_routes_audio_to_audio_adapter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    adapter = registry.get_adapter("gemini")
    assert adapter is not None
    with pytest.raises(GeminiAudioError, match="model_not_configured"):
        adapter.generate(_request(tmp_path, model=""))


# ---- 설정 ----


def test_settings_defaults_and_values(monkeypatch: pytest.MonkeyPatch) -> None:
    s = MediaSettings.from_env()
    assert (s.audio_provider, s.audio_model, s.audio_mode) == ("mock", "", "off")
    assert ffmpeg_bin() == "ffmpeg" and not has_api_key("audio")
    monkeypatch.setenv("MARKETING_AUDIO_PROVIDER", "Gemini")
    monkeypatch.setenv("MARKETING_AUDIO_MODEL", MODEL)
    monkeypatch.setenv("MARKETING_AUDIO_MODE", "bgm_overlay")
    monkeypatch.setenv("MARKETING_FFMPEG_BIN", "/opt/ff/ffmpeg")
    monkeypatch.setenv("MARKETING_AUDIO_API_KEY", KEY)
    s = MediaSettings.from_env()
    assert (s.provider("audio"), s.model("audio"), s.audio_mode) == ("gemini", MODEL, "bgm_overlay")
    assert ffmpeg_bin() == "/opt/ff/ffmpeg" and has_api_key("audio")
    assert KEY not in repr(s)
    monkeypatch.setenv("MARKETING_AUDIO_MODE", "weird")
    assert MediaSettings.from_env().audio_mode == "off"  # 모르는 값은 끈다
    assert MediaSettings.from_env().provider("video") == "mock"  # 영상·이미지 설정은 그대로


# ---- 단가표 ----


def _entry(model: str, resolution: str):
    return pricing.find_entry(
        provider="gemini", model=model, kind="audio", resolution=resolution, quality=None
    )


def test_shipped_price_table_matches_confirmed_lyria_prices() -> None:
    clip = _entry("lyria-3-clip-preview", "30s")
    song = _entry("lyria-3.5", "full-song")
    assert (clip.per_item_usd, song.per_item_usd) == (Decimal("0.04"), Decimal("0.08"))
    for entry in (clip, song):
        assert entry.checked_on == "2026-11-11" and entry.source.startswith("https://")
        assert pricing.estimate_usd(entry, prompt="x" * 500, duration_s=None) == entry.per_item_usd
        assert pricing.usd_for(entry, input_tokens=10**6, output_tokens=10**6) == entry.per_item_usd
    with pytest.raises(PriceNotFound):  # 확인하지 못한 조합은 거부
        _entry("lyria-3-clip-preview", "full-song")
    with pytest.raises(PriceNotFound):
        _entry("lyria-unknown", "30s")
    with pytest.raises(PriceNotFound):  # 같은 모델이라도 영상 종류로는 없다
        pricing.find_entry(
            provider="gemini",
            model="lyria-3-clip-preview",
            kind="video",
            resolution="720p",
            quality=None,
        )


def test_existing_entries_are_unchanged() -> None:
    entry = pricing.find_entry(
        provider="gemini",
        model="gemini-omni-1.2-flash",
        kind="video",
        resolution="720p",
        quality=None,
    )
    assert entry.per_item_usd is None and entry.output_usd_per_mtok == Decimal("12.0")
    assert pricing.output_tokens_for(entry, 8) == 46_336


def test_audio_price_entry_needs_per_item_price(tmp_path: Path) -> None:
    base = (
        '[[price]]\nprovider="gemini"\nmodel="m"\nkind="audio"\nresolution="30s"\n'
        'source="https://x"\nchecked_on="2026-11-11"\n'
    )
    path = tmp_path / "p.toml"
    for extra in ("", "per_item_usd = 0\n", "per_item_usd = -1\n", 'per_item_usd = "abc"\n'):
        path.write_text(base + extra, encoding="utf-8")
        with pytest.raises(PriceTableError):
            pricing.load_table(path)
    path.write_text(base + "per_item_usd = 0.04\n", encoding="utf-8")
    assert pricing.load_table(path)[0].per_item_usd == Decimal("0.04")


def test_audio_cost_estimate_krw_rounds_up() -> None:
    usd = pricing.estimate_usd(_entry("lyria-3-clip-preview", "30s"), prompt="x", duration_s=None)
    assert pricing.krw_estimate(usd, Decimal(1500), Decimal(10)) == 66  # ceil(0.04*1500*1.1)


# ---- 비용 상한(장부) ----


def test_ledger_applies_monthly_cap_to_audio() -> None:
    ledger = MediaLedger(":memory:")
    now = datetime.now(UTC)

    def reserve(key: str):
        return ledger.reserve(
            key=key,
            month="2026-11",
            media_kind="audio",
            provider="gemini",
            model_id=MODEL,
            krw_est=66,
            usd_est=Decimal("0.04"),
            monthly_cap=100,
            now=now,
        )

    assert reserve("a").outcome == "reserved"
    assert reserve("b").outcome == "over_budget"  # 66 + 66 > 100
    assert ledger.month_total("2026-11") == 66

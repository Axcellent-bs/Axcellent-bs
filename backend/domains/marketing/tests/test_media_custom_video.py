"""커스텀 영상 공급자·공급자 목록·선택: 가짜 전송과 가짜 관문으로만 검증한다(실제 호출·과금
없음)."""

from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta, TaskStatus
from marketing_agent import handlers, video_providers
from marketing_agent.media import custom_video, pricing, registry, video_catalog, video_store
from marketing_agent.media.base import MediaProviderError, MediaRequest
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.custom_spec import SpecError, public_view, validate_spec
from marketing_agent.media.custom_video import CustomVideoProvider, dig, render, sign_jwt

SECRET = "sk-custom-SECRET-999"
ACCESS = "ak-custom-ACCESS-111"
MUTED_MP4 = b"\x00\x00\x00\x20ftypisom....moov....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-fake"
VIDEO_URL = "https://cdn.vendor.test/out.mp4"


def public_resolver(host: str, port: int, **_kw: object) -> list[tuple]:
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def spec_raw(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "vendor-a",
        "label": "벤더 A",
        "enabled": True,
        "baseUrl": "https://api.vendor.test",
        "defaultModel": "vid-1",
        "auth": {
            "type": "header",
            "headerName": "Authorization",
            "valueTemplate": "Key {{secret}}",
            "secretRef": "MARKETING_CUSTOM_VENDOR_A_KEY",
        },
        "submit": {
            "path": "/v1/videos",
            "bodyTemplate": {
                "model": "{{model}}",
                "prompt": "{{prompt}}",
                "duration": "{{durationSec}}",
                "aspect_ratio": "{{aspectRatio}}",
                "resolution": "{{resolution}}",
            },
            "taskIdPath": "data.id",
        },
        "poll": {
            "path": "/v1/videos/{{taskId}}",
            "statusPath": "data.status",
            "successValues": ["SUCCEEDED"],
            "failureValues": ["failed", "cancelled"],
            "errorPath": "data.error.code",
        },
        "result": {"videoUrlPath": "data.output[0].url", "allowedHosts": ["cdn.vendor.test"]},
        "limits": {"resolutions": ["720p"], "minDurationSec": 3, "maxDurationSec": 10},
        "pricing": {"perSecondUsd": 0.1, "source": "https://vendor.test/pricing 2026-11-19"},
    }
    raw.update(over)
    return raw


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_PROVIDERS_FILE", str(tmp_path / "vp.json"))
    monkeypatch.setenv("MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS", "api.vendor.test,cdn.vendor.test")
    monkeypatch.setenv("MARKETING_CUSTOM_VENDOR_A_KEY", SECRET)
    monkeypatch.setenv("MARKETING_CUSTOM_VENDOR_A_ACCESS", ACCESS)
    for name in ("MARKETING_VIDEO_PROVIDER", "MARKETING_VIDEO_MODEL"):
        monkeypatch.delenv(name, raising=False)
    custom_video._SUBMITS.clear()  # noqa: SLF001


def _request(tmp_path: Path, **over: Any) -> MediaRequest:
    values = {
        "media_kind": "video",
        "prompt": "a cat on a book",
        "prompt_sha256": "c" * 64,
        "resolution": "720p",
        "quality": None,
        "duration_s": 5,
        "remove_audio": True,
        "model": "vid-1",
        "output_dir": tmp_path,
    } | over
    return MediaRequest(**values)


def _provider(spec_dict: dict, handler, calls: list | None = None) -> CustomVideoProvider:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    return CustomVideoProvider(
        validate_spec(spec_dict),
        httpx.MockTransport(wrapped),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=public_resolver,
    )


def _happy(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        return httpx.Response(200, json={"data": {"id": "job-1"}})
    if request.url.path == "/v1/videos/job-1":
        return httpx.Response(
            200, json={"data": {"status": "SUCCEEDED", "output": [{"url": VIDEO_URL}]}}
        )
    if request.url.host == "cdn.vendor.test":
        return httpx.Response(200, content=MUTED_MP4)
    return httpx.Response(404)


# --- 스펙 검사 ---
def test_valid_spec_and_public_view_has_no_secret() -> None:
    spec = validate_spec(spec_raw())
    view = public_view(spec)
    assert view["keyConfigured"] is True
    assert SECRET not in json.dumps(view)
    assert view["auth"]["secretRef"] == "MARKETING_CUSTOM_VENDOR_A_KEY"


@pytest.mark.parametrize(
    "patch",
    [
        {"baseUrl": "http://api.vendor.test"},
        {"baseUrl": "https://api.vendor.test:8443"},
        {"baseUrl": "https://user:pw@api.vendor.test"},
        {"baseUrl": "https://169.254.169.254"},
        {"baseUrl": "https://localhost"},
        {"baseUrl": "https://evil.test"},  # 허용 목록 밖
        {"baseUrl": "https://api.vendor.test/v1"},
        {"id": "Bad_ID"},
        {"auth": {"secretRef": "MARKETING_SEEDANCE_API_KEY"}},  # 다른 공급자 키 금지
        {"auth": {"secretRef": "DATABASE_URL"}},
        {"extraHeaders": {"Host": "x"}},
        {"extraHeaders": {"X-A": "{{secret}}"}},
        {"result": {"videoUrlPath": "a.b", "allowedHosts": ["evil.test"]}},
        {"pricing": {"perSecondUsd": 0, "source": "x y z"}},
        {"limits": {"resolutions": ["720p"], "maxDurationSec": 999}},
    ],
)
def test_bad_specs_rejected(patch: dict) -> None:
    raw = spec_raw()
    for key, value in patch.items():
        if key == "auth":
            raw["auth"] = {**raw["auth"], **value}
        else:
            raw[key] = value
    with pytest.raises(SpecError):
        validate_spec(raw)


def test_body_template_rules() -> None:
    raw = spec_raw()
    raw["submit"]["bodyTemplate"] = {"p": "{{prompt}}", "x": "{{secret}}"}
    with pytest.raises(SpecError):
        validate_spec(raw)
    raw["submit"]["bodyTemplate"] = {"model": "{{model}}"}  # prompt 없음
    with pytest.raises(SpecError):
        validate_spec(raw)


def test_no_allowlist_means_no_custom_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS")
    with pytest.raises(SpecError):
        validate_spec(spec_raw())


def test_helpers() -> None:
    assert dig({"a": [{"b": 1}]}, "a[0].b") == 1
    assert dig({"a": 1}, "$.a.b") is None
    assert render(
        {"d": "{{durationSec}}", "t": "x {{model}}"}, {"durationSec": 5, "model": "m"}
    ) == {
        "d": 5,
        "t": "x m",
    }


# --- 호출 ---
def test_generate_happy_path_sends_key_only_in_header(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    result = _provider(spec_raw(), _happy, calls).generate(_request(tmp_path))
    assert result.provider == "custom:vendor-a"
    assert result.provider_job_id == "job-1"
    submit = calls[0]
    assert submit.headers["Authorization"] == f"Key {SECRET}"
    body = json.loads(submit.content)
    assert body["duration"] == 5 and body["aspect_ratio"] == "9:16" and body["prompt"]
    assert SECRET not in submit.content.decode()
    assert (tmp_path / "asset.mp4").read_bytes() == MUTED_MP4
    download = calls[-1]
    assert download.url.host == "cdn.vendor.test" and "Authorization" not in download.headers


def test_pending_then_done_and_failure_codes_are_sanitized(tmp_path: Path) -> None:
    states = iter(["QUEUED", "SUCCEEDED"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" or request.url.host == "cdn.vendor.test":
            return _happy(request)
        status = next(states)
        out = [{"url": VIDEO_URL}] if status == "SUCCEEDED" else []
        return httpx.Response(200, json={"data": {"status": status, "output": out}})

    _provider(spec_raw(), handler).generate(_request(tmp_path))

    def failing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _happy(request)
        return httpx.Response(
            200, json={"data": {"status": "Failed", "error": {"code": "bad prompt <b>!!"}}}
        )

    with pytest.raises(MediaProviderError) as error:
        _provider(spec_raw(), failing).generate(_request(tmp_path))
    assert str(error.value) == "status_failed: badpromptb"
    assert SECRET not in str(error.value)


def test_disabled_provider_is_refused_before_any_call(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(MediaProviderError, match="custom_provider_disabled"):
        _provider(spec_raw(enabled=False), _happy, calls).generate(_request(tmp_path))
    assert calls == []


def test_missing_key_and_bad_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MediaProviderError, match="unsupported_duration"):
        _provider(spec_raw(), _happy).generate(_request(tmp_path, duration_s=11))
    with pytest.raises(MediaProviderError, match="unsupported_resolution"):
        _provider(spec_raw(), _happy).generate(_request(tmp_path, resolution="1080p"))
    monkeypatch.delenv("MARKETING_CUSTOM_VENDOR_A_KEY")
    with pytest.raises(MediaProviderError, match="api_key_not_configured"):
        _provider(spec_raw(), _happy).generate(_request(tmp_path))


def test_result_host_outside_allowlist_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _happy(request)
        return httpx.Response(
            200,
            json={"data": {"status": "SUCCEEDED", "output": [{"url": "https://evil.test/x.mp4"}]}},
        )

    with pytest.raises(MediaProviderError, match="result_host_not_allowed"):
        _provider(spec_raw(), handler).generate(_request(tmp_path))


def test_private_result_address_blocked_by_safe_fetch(tmp_path: Path) -> None:
    def private_resolver(host: str, port: int, **_kw: object) -> list[tuple]:
        return [(2, 1, 6, "", ("169.254.169.254", port))]

    provider = CustomVideoProvider(
        validate_spec(spec_raw()),
        httpx.MockTransport(_happy),
        poll_interval_s=0,
        sleep=lambda _s: None,
        resolver=private_resolver,
    )
    with pytest.raises(MediaProviderError, match="blocked_video_host"):
        provider.generate(_request(tmp_path))


def test_response_size_and_poll_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(custom_video, "MAX_RESPONSE_BYTES", 50)
    with pytest.raises(MediaProviderError, match="response_too_large"):
        _provider(spec_raw(), lambda r: httpx.Response(200, content=b"x" * 500)).generate(
            _request(tmp_path)
        )
    monkeypatch.setattr(custom_video, "MAX_RESPONSE_BYTES", 1024 * 1024)
    raw = spec_raw()
    raw["limits"]["maxPolls"] = 3

    def forever(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _happy(request)
        return httpx.Response(200, json={"data": {"status": "running"}})

    with pytest.raises(MediaProviderError, match="poll_limit"):
        _provider(raw, forever).generate(_request(tmp_path))


def test_redirect_is_not_followed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://169.254.169.254/"})

    with pytest.raises(MediaProviderError, match="http_302"):
        _provider(spec_raw(), handler).generate(_request(tmp_path))


def test_rate_limit_per_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(custom_video, "MAX_SUBMITS_PER_HOUR", 2)
    for _ in range(2):
        _provider(spec_raw(), _happy).generate(_request(tmp_path))
    with pytest.raises(MediaProviderError, match="rate_limited"):
        _provider(spec_raw(), _happy).generate(_request(tmp_path))


def test_poll_url_from_response_must_be_same_host(tmp_path: Path) -> None:
    raw = spec_raw()
    raw["submit"]["pollUrlPath"] = "status_url"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "j"}, "status_url": "https://evil.test/s"})

    with pytest.raises(MediaProviderError, match="poll_url_origin_mismatch"):
        _provider(raw, handler).generate(_request(tmp_path))


def test_fal_style_two_step_result_and_auth_on_download(tmp_path: Path) -> None:
    raw = spec_raw()
    raw["baseUrl"] = "https://api.vendor.test"
    raw["poll"] = {
        "path": "/q/requests/{{taskId}}/status",
        "statusPath": "status",
        "successValues": ["COMPLETED"],
        "failureValues": [],
    }
    raw["submit"]["taskIdPath"] = "request_id"
    raw["result"] = {
        "path": "/q/requests/{{taskId}}",
        "videoUrlPath": "video.url",
        "urlNeedsAuth": True,
    }
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": 77})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/q/requests/77":
            return httpx.Response(200, json={"video": {"url": "https://api.vendor.test/f.mp4"}})
        return httpx.Response(200, content=MUTED_MP4)

    _provider(raw, handler).generate(_request(tmp_path))
    assert seen[-1].headers["Authorization"] == f"Key {SECRET}"  # urlNeedsAuth + 같은 호스트


def test_jwt_auth_signs_per_request(tmp_path: Path) -> None:
    raw = spec_raw()
    raw["auth"] = {
        "type": "jwt_hs256",
        "valueTemplate": "Bearer {{secret}}",
        "secretRef": "MARKETING_CUSTOM_VENDOR_A_KEY",
        "accessKeyRef": "MARKETING_CUSTOM_VENDOR_A_ACCESS",
    }
    calls: list[httpx.Request] = []
    _provider(raw, _happy, calls).generate(_request(tmp_path))
    token = calls[0].headers["Authorization"].removeprefix("Bearer ")
    head, claims, _sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(claims + "=="))
    assert payload["iss"] == ACCESS and payload["exp"] - payload["nbf"] == 1805
    assert json.loads(base64.urlsafe_b64decode(head + "=="))["alg"] == "HS256"
    assert SECRET not in token
    assert sign_jwt("a", "s", 1000) == sign_jwt("a", "s", 1000)


# --- 저장소·목록·선택 ---
def test_store_roundtrip_history_and_catalog_masks_keys() -> None:
    spec = validate_spec(spec_raw())
    video_store.save_custom(spec, "재헌")
    rows = {p["id"]: p for p in video_catalog.list_video_providers()["providers"]}
    row = rows["custom:vendor-a"]
    assert row["kind"] == "custom" and row["selectable"] and row["priced"]
    assert row["key_env"] == "MARKETING_CUSTOM_VENDOR_A_KEY" and row["key_configured"] is True
    assert SECRET not in json.dumps(video_catalog.list_video_providers())
    assert video_store.history()[0]["by"] == "재헌"
    assert registry.get_adapter("custom:vendor-a") is not None
    assert registry.get_adapter("custom:nope") is None
    assert video_store.delete_custom("vendor-a", "재헌") is True
    assert registry.get_adapter("custom:vendor-a") is None


def test_catalog_lists_builtin_and_autodiscovered_adapters() -> None:
    rows = {p["id"]: p for p in video_catalog.list_video_providers()["providers"]}
    assert {"mock", "gemini", "seedance", "kling"} <= set(rows)
    assert rows["kling"]["models"] and rows["kling"]["priced"] is True
    assert rows["seedance"]["priced"] is False  # 단가 미확인 = 호출 막힘
    assert all(SECRET not in json.dumps(r) for r in rows.values())


def test_adapter_file_is_autodiscovered() -> None:
    assert registry.ADAPTERS["kling"].__module__.endswith("kling_video")


def test_selection_overrides_env_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_VIDEO_PROVIDER", "gemini")
    monkeypatch.setenv("MARKETING_VIDEO_MODEL", "gemini-omni-1.2-flash")
    assert MediaSettings.from_env().video_provider == "gemini"
    video_store.save_custom(validate_spec(spec_raw()), "a")
    video_store.set_selection("custom:vendor-a", "", "a")
    s = MediaSettings.from_env()
    assert (s.video_provider, s.video_model) == ("custom:vendor-a", "vid-1")  # 스펙 기본 모델
    video_store.delete_custom("vendor-a", "a")  # 선택된 공급자를 지우면 환경변수로 복귀
    assert MediaSettings.from_env().video_provider == "gemini"


def test_custom_price_comes_from_spec() -> None:
    video_store.save_custom(validate_spec(spec_raw()), "a")
    entry = pricing.find_entry(
        provider="custom:vendor-a", model="vid-1", kind="video", resolution="720p", quality=None
    )
    assert pricing.estimate_usd(entry, prompt="x", duration_s=5) == pytest.approx(0.5)
    with pytest.raises(pricing.PriceNotFound):  # 길이 상한 초과
        pricing.estimate_usd(entry, prompt="x", duration_s=11)
    with pytest.raises(pricing.PriceNotFound):
        pricing.find_entry(
            provider="custom:vendor-a", model="m", kind="video", resolution="1080p", quality=None
        )


# --- 스킬(관문) ---
class Gate:
    def __init__(self, status: str = "executed") -> None:
        self.status = status
        self.actions: list[str] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        self.actions.append(action)
        extra = {"hold_reason": HoldReason.OVER_THRESHOLD, "review_id": "rev_1"}
        return ExecuteDecision(
            execution_id="exe_1",
            grade="C" if self.status == "held" else "A",
            status=self.status,
            **(extra if self.status == "held" else {}),
        )


def _req(inp: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_vp",
        token="mock:dashboard-api",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    return AgentRequest(meta=meta, skill="x", input=inp)


@pytest.mark.asyncio
async def test_save_skill_is_gated_and_writes_only_after_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = Gate("held")
    monkeypatch.setattr(video_providers, "execute", gate.execute)
    held = await handlers.save_custom_video_provider_skill(
        _req({"spec": spec_raw(), "updated_by": "재헌"})
    )
    assert held.status != TaskStatus.COMPLETED and gate.actions == ["save_custom_video_provider"]
    assert video_store.get_custom("vendor-a") is None
    gate.status = "executed"
    done = await handlers.save_custom_video_provider_skill(
        _req({"spec": spec_raw(), "updated_by": "재헌"})
    )
    assert done.outcome == "saved" and video_store.get_custom("vendor-a") is not None
    assert SECRET not in json.dumps(done.output)


@pytest.mark.asyncio
async def test_save_skill_rejects_bad_spec_before_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(video_providers, "execute", gate.execute)
    bad = copy.deepcopy(spec_raw())
    bad["baseUrl"] = "https://169.254.169.254"
    res = await handlers.save_custom_video_provider_skill(_req({"spec": bad, "updated_by": "a"}))
    assert res.outcome == "invalid_spec" and gate.actions == []


@pytest.mark.asyncio
async def test_select_and_delete_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(video_providers, "execute", gate.execute)
    video_store.save_custom(validate_spec(spec_raw()), "a")
    ok = await handlers.select_video_provider_skill(
        _req({"provider": "custom:vendor-a", "model": "vid-1", "updated_by": "a"})
    )
    assert ok.outcome == "selected" and ok.output["selected"]["provider"] == "custom:vendor-a"
    unpriced = await handlers.select_video_provider_skill(
        _req({"provider": "seedance", "model": "x", "updated_by": "a"})
    )
    assert unpriced.outcome == "model_not_priced"
    unknown = await handlers.select_video_provider_skill(
        _req({"provider": "nope", "model": "x", "updated_by": "a"})
    )
    assert unknown.outcome == "unknown_provider"
    gone = await handlers.delete_custom_video_provider_skill(
        _req({"id": "vendor-a", "updated_by": "a"})
    )
    assert gone.outcome == "deleted" and video_store.selection() is None
    listed = await handlers.list_video_providers_skill(_req({}))
    assert listed.outcome == "state"

"""Instagram Login 실제 어댑터. 네트워크·진짜 토큰 없이 `httpx.MockTransport`로 요청·응답을 본다."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from marketing_agent.sns_publish import instagram
from marketing_agent.sns_publish.instagram import (
    AdapterUnavailable,
    ContainerRequest,
    IgApiError,
    IgResponseLost,
    classify,
    get_adapter,
)
from marketing_agent.sns_publish.instagram_graph import (
    GraphConfigError,
    GraphInstagramAdapter,
)

TOKEN = "test-token-not-real"  # 시험용 자리표시. 진짜 값이 아니다
USER_ID = "1784140000000000"
MEDIA_BASE = "https://media.example.test/ig"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def form(self, index: int = 0) -> dict[str, str]:
        request = self.requests[index]
        raw = request.content.decode() if request.method == "POST" else request.url.query.decode()
        return {k: v[0] for k, v in parse_qs(raw).items()}


def _json(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _adapter(rec: Recorder, **kwargs: str) -> GraphInstagramAdapter:
    return GraphInstagramAdapter(
        access_token=TOKEN,
        user_id=USER_ID,
        media_base_url=kwargs.pop("media_base_url", MEDIA_BASE),
        client=rec.client(),
        **kwargs,
    )


def _req(kind: str = "image", ref: str = "https://cdn.example.test/a.jpg") -> ContainerRequest:
    return ContainerRequest(
        media_ref=ref,
        media_kind=kind,  # type: ignore[arg-type]
        caption="시연용 가상 서비스입니다 (AI 생성)",
        is_ai_generated=True,
        share_to_feed=True,
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MARKETING_SNS_PROVIDER",
        "MARKETING_IG_ACCESS_TOKEN",
        "MARKETING_IG_USER_ID",
        "MARKETING_IG_MEDIA_BASE_URL",
        "MARKETING_IG_API_BASE_URL",
        "MARKETING_IG_API_VERSION",
        "MARKETING_IG_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)
    instagram.reset_adapter()


# -- 컨테이너 생성
@pytest.mark.asyncio
async def test_image_container_sends_ai_flag_and_token_in_body() -> None:
    rec = Recorder(_json({"id": "17900000000000001"}))
    container = await _adapter(rec).create_container(_req())
    assert container == "17900000000000001"
    request = rec.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://graph.instagram.com/{USER_ID}/media"
    assert TOKEN not in str(request.url)  # 토큰은 본문에만 (URL·로그에 남지 않는다)
    form = rec.form()
    assert form["image_url"] == "https://cdn.example.test/a.jpg"
    assert form["is_ai_generated"] == "true"
    assert form["caption"].startswith("시연용")
    assert form["access_token"] == TOKEN
    assert "media_type" not in form and "video_url" not in form


@pytest.mark.asyncio
async def test_reel_container_uses_reels_and_explicit_share_to_feed() -> None:
    rec = Recorder(_json({"id": "c1"}))
    await _adapter(rec).create_container(_req("reel", "https://cdn.example.test/a.mp4"))
    form = rec.form()
    assert form["media_type"] == "REELS"
    assert form["video_url"] == "https://cdn.example.test/a.mp4"
    assert form["share_to_feed"] == "true"
    assert form["is_ai_generated"] == "true"


@pytest.mark.asyncio
async def test_api_version_is_part_of_the_path_only_when_set() -> None:
    rec = Recorder(_json({"id": "c1"}))
    await _adapter(rec, api_version="v99.0").create_container(_req())
    assert str(rec.requests[0].url) == f"https://graph.instagram.com/v99.0/{USER_ID}/media"


@pytest.mark.asyncio
async def test_local_asset_maps_to_public_base_by_file_name(tmp_path: Path) -> None:
    asset = tmp_path / "post.jpg"
    asset.write_bytes(JPEG)
    rec = Recorder(_json({"id": "c1"}))
    await _adapter(rec).create_container(_req(ref=str(asset)))
    assert rec.form()["image_url"] == f"{MEDIA_BASE}/post.jpg"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref,base",
    [
        ("/data/post.jpg", ""),  # 공개 주소 없음
        ("/data/post.jpg", "http://media.example.test"),  # https가 아님
        ("http://cdn.example.test/a.jpg", MEDIA_BASE),  # 원격이 https가 아님
    ],
)
async def test_unreachable_media_is_rejected_before_any_request(ref: str, base: str) -> None:
    rec = Recorder()
    adapter = _adapter(rec, media_base_url=base)
    with pytest.raises(IgApiError) as caught:
        await adapter.create_container(_req(ref=ref))
    assert classify(caught.value.code, caught.value.subcode)[0] == "ig_media_url_unreachable"
    assert rec.requests == []


@pytest.mark.asyncio
async def test_non_jpeg_local_image_is_rejected_before_any_request(tmp_path: Path) -> None:
    asset = tmp_path / "post.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    rec = Recorder()
    with pytest.raises(IgApiError) as caught:
        await _adapter(rec).create_container(_req(ref=str(asset)))
    assert (caught.value.code, caught.value.subcode) == (352, 2207026)
    assert rec.requests == []


# -- 상태·게시·조회
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["IN_PROGRESS", "FINISHED", "ERROR", "EXPIRED", "PUBLISHED"])
async def test_container_status_maps_known_states(state: str) -> None:
    rec = Recorder(_json({"status_code": state, "id": "c1"}))
    status = await _adapter(rec).get_container_status("c1")
    assert status.status_code == state
    assert rec.requests[0].method == "GET"
    assert rec.form()["fields"] == "status_code"


@pytest.mark.asyncio
async def test_unknown_status_value_fails_closed() -> None:
    rec = Recorder(_json({"status_code": "SOMETHING_NEW"}))
    with pytest.raises(IgApiError) as caught:
        await _adapter(rec).get_container_status("c1")
    assert classify(caught.value.code, caught.value.subcode)[1] == "fail"


@pytest.mark.asyncio
async def test_media_publish_sends_creation_id_once_and_returns_media_id() -> None:
    rec = Recorder(_json({"id": "m1"}))
    media_id = await _adapter(rec).media_publish("c1")
    assert media_id == "m1"
    assert len(rec.requests) == 1
    assert str(rec.requests[0].url) == f"https://graph.instagram.com/{USER_ID}/media_publish"
    assert rec.form()["creation_id"] == "c1"


@pytest.mark.asyncio
async def test_get_media_does_not_ask_for_label_field_and_reports_unknown_label() -> None:
    rec = Recorder(
        _json(
            {
                "id": "m1",
                "permalink": "https://www.instagram.com/p/abc/",
                "timestamp": "2026-11-06T09:00:00+0000",
                "media_product_type": "FEED",
            }
        )
    )
    info = await _adapter(rec).get_media("m1")
    assert "is_ai_generated" not in rec.form()["fields"]
    assert info.permalink == "https://www.instagram.com/p/abc/"
    assert info.media_product_type == "FEED"
    assert info.is_ai_generated is None  # 앱 화면 라벨은 사람이 따로 확인한다


@pytest.mark.asyncio
async def test_delete_is_not_available_on_instagram_login() -> None:
    with pytest.raises(NotImplementedError):
        await _adapter(Recorder()).delete_media("m1")


# -- 오류 분류와 비밀값
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,status,failure,action",
    [
        ({"error": {"code": 190, "message": f"bad {TOKEN}"}}, 400, "ig_token_invalid", "fail"),
        ({"error": {"code": 9, "error_subcode": 2207042}}, 400, "ig_daily_limit", "fail"),
        ({"error": {"code": -2, "error_subcode": 2207003}}, 400, "ig_download_timeout", "retry"),
        (
            {"error": {"code": 24, "error_subcode": 2207006}},
            400,
            "ig_media_not_found",
            "new_container",
        ),
        ({"oops": 1}, 502, "ig_server_error", "retry"),
        ({"oops": 1}, 400, "ig_unknown_error", "fail"),
    ],
)
async def test_error_responses_are_classified_without_leaking_token(
    body: dict, status: int, failure: str, action: str
) -> None:
    rec = Recorder(_json(body, status))
    with pytest.raises(IgApiError) as caught:
        await _adapter(rec).media_publish("c1")
    assert classify(caught.value.code, caught.value.subcode) == (failure, action)
    assert TOKEN not in str(caught.value)  # 서버 메시지는 예외에 옮기지 않는다


@pytest.mark.asyncio
async def test_network_failure_becomes_response_lost_without_exception_chain() -> None:
    rec = Recorder(httpx.ConnectTimeout(f"timeout for https://x/?access_token={TOKEN}"))
    with pytest.raises(IgResponseLost) as caught:
        await _adapter(rec).media_publish("c1")
    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


# -- 설정과 선택
def test_from_env_names_only_missing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", TOKEN)
    with pytest.raises(GraphConfigError) as caught:
        GraphInstagramAdapter.from_env()
    assert "MARKETING_IG_USER_ID" in str(caught.value)
    assert TOKEN not in str(caught.value) and "MARKETING_IG_ACCESS_TOKEN" not in str(caught.value)


def test_get_adapter_selects_instagram_only_when_enabled_and_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert get_adapter().name == "mock"  # 기본은 목
    monkeypatch.setenv("MARKETING_SNS_PROVIDER", "instagram")
    with pytest.raises(AdapterUnavailable) as caught:
        get_adapter()
    assert "MARKETING_IG_ACCESS_TOKEN" in str(caught.value)
    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("MARKETING_IG_USER_ID", USER_ID)
    adapter = get_adapter()
    assert adapter.name == "instagram"
    assert TOKEN not in repr(vars(adapter).get("_root"))


def test_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_SNS_PROVIDER", "tiktok")
    with pytest.raises(AdapterUnavailable):
        get_adapter()

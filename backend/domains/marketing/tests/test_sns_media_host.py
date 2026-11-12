"""S3 호환 미디어 호스트. 네트워크·진짜 키 없이 `httpx.MockTransport`로 요청 모양과 서명을 본다."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from marketing_agent.sns_publish import instagram
from marketing_agent.sns_publish.instagram import IgApiError, classify
from marketing_agent.sns_publish.instagram_graph import GraphConfigError, GraphInstagramAdapter
from marketing_agent.sns_publish.media_host import (
    ACCESS_KEY_ENV,
    BUCKET_ENV,
    SECRET_KEY_ENV,
    VIDEO_MAX_BYTES,
    MediaHostConfigError,
    MediaHostError,
    S3MediaHost,
)

# AWS 문서의 서명 URL 예시 값(공개된 시험용 키). 진짜 키가 아니다
AWS_EXAMPLE_ACCESS = "AKIAIOSFODNN7EXAMPLE"
AWS_EXAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # noqa: S105
AWS_EXAMPLE_SIGNATURE = "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"

ACCESS = "test-access-not-real"
SECRET = "test-secret-not-real"  # noqa: S105
FIXED = datetime(2026, 11, 6, 9, 0, 0, tzinfo=UTC)
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


MP4 = b"\x00\x00\x00\x20ftypisom" + b"0" * 64


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


def _host(rec: Recorder | None = None, **kwargs) -> S3MediaHost:
    return S3MediaHost(
        bucket=kwargs.pop("bucket", "media-bucket"),
        access_key_id=ACCESS,
        secret_access_key=SECRET,
        client=rec.client() if rec else None,
        clock=lambda: FIXED,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MARKETING_SNS_PROVIDER",
        "MARKETING_IG_ACCESS_TOKEN",
        "MARKETING_IG_USER_ID",
        "MARKETING_IG_MEDIA_BASE_URL",
        BUCKET_ENV,
        ACCESS_KEY_ENV,
        SECRET_KEY_ENV,
        "MARKETING_MEDIA_S3_ENDPOINT",
        "MARKETING_MEDIA_S3_REGION",
        "MARKETING_MEDIA_S3_PATH_STYLE",
        "MARKETING_MEDIA_S3_PREFIX",
        "MARKETING_MEDIA_S3_URL_TTL_S",
        "MARKETING_MEDIA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    instagram.reset_adapter()


# -- 서명이 맞는가 (AWS 문서 예시와 같은 입력이면 같은 서명이 나와야 한다)
def test_presigned_get_matches_the_published_aws_example() -> None:
    host = S3MediaHost(
        bucket="examplebucket",
        access_key_id=AWS_EXAMPLE_ACCESS,
        secret_access_key=AWS_EXAMPLE_SECRET,
        region="us-east-1",
        endpoint="https://s3.amazonaws.com",
        path_style=False,
        prefix="",
        url_ttl_s=86400,
        clock=lambda: datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC),
    )
    url = host.presign_get("test.txt")
    parts = urlsplit(url)
    assert parts.netloc == "examplebucket.s3.amazonaws.com"
    assert parts.path == "/test.txt"
    assert parse_qs(parts.query)["X-Amz-Signature"] == [AWS_EXAMPLE_SIGNATURE]


# -- 주소 모양
def test_aws_default_uses_virtual_host_and_r2_style_uses_path() -> None:
    aws = _host().presign_get("ig/a.jpg")
    assert urlsplit(aws).netloc == "media-bucket.s3.us-east-1.amazonaws.com"
    assert urlsplit(aws).path == "/ig/a.jpg"
    r2 = _host(endpoint="https://acct.r2.cloudflarestorage.com", region="auto").presign_get(
        "ig/a.jpg"
    )
    assert urlsplit(r2).netloc == "acct.r2.cloudflarestorage.com"
    assert urlsplit(r2).path == "/media-bucket/ig/a.jpg"


def test_signed_url_is_read_only_expiring_and_ttl_is_capped() -> None:
    query = parse_qs(urlsplit(_host(url_ttl_s=10**9).presign_get("k.jpg")).query)
    assert query["X-Amz-Expires"] == ["86400"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Date"] == ["20261106T090000Z"]


def test_non_https_endpoint_is_rejected() -> None:
    with pytest.raises(MediaHostConfigError):
        _host(endpoint="http://minio.local:9000")


def test_object_key_is_content_addressed_and_hides_file_name() -> None:
    host = _host(prefix="/ig/")
    key = host.object_key(JPEG)
    assert key == f"ig/{hashlib.sha256(JPEG).hexdigest()[:24]}.jpg"
    assert host.object_key(JPEG) == key  # 같은 파일은 같은 이름(올리기가 멱등)


# -- 올리기
@pytest.mark.asyncio
async def test_upload_puts_signed_request_and_returns_signed_get_url() -> None:
    rec = Recorder(httpx.Response(200))
    url = await _host(rec).upload_jpeg(JPEG)
    request = rec.requests[0]
    assert request.method == "PUT"
    assert request.content == JPEG
    assert request.headers["content-type"] == "image/jpeg"
    assert request.headers["x-amz-content-sha256"] == hashlib.sha256(JPEG).hexdigest()
    auth = request.headers["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=" + ACCESS + "/20261106/us-east-1/s3/")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in auth
    assert SECRET not in auth and SECRET not in str(request.url)
    assert url.startswith("https://") and "X-Amz-Signature=" in url
    assert url.split("?")[0] == str(request.url)  # 올린 곳과 같은 주소를 읽기용으로 서명한다


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(403, text=f"denied {SECRET}"), "upload_rejected"),
        (httpx.ConnectTimeout(f"timeout https://x/?sig={SECRET}"), "upload_failed"),
    ],
)
async def test_upload_failures_are_closed_codes_without_leaking(
    response: httpx.Response | Exception, code: str
) -> None:
    rec = Recorder(response)
    with pytest.raises(MediaHostError) as caught:
        await _host(rec).upload_jpeg(JPEG)
    assert caught.value.code == code
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    if code == "upload_failed":  # 네트워크 예외는 주소를 담고 있어 사슬을 끊는다
        assert caught.value.__suppress_context__


# -- 설정
def test_from_env_is_off_without_bucket_and_names_only_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert S3MediaHost.from_env_optional() is None  # 꺼져 있으면 아무것도 하지 않는다
    monkeypatch.setenv(BUCKET_ENV, "media-bucket")
    monkeypatch.setenv(ACCESS_KEY_ENV, ACCESS)
    with pytest.raises(MediaHostConfigError) as caught:
        S3MediaHost.from_env_optional()
    assert SECRET_KEY_ENV in str(caught.value)
    assert ACCESS not in str(caught.value)


def test_from_env_picks_r2_region_and_path_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUCKET_ENV, "media-bucket")
    monkeypatch.setenv(ACCESS_KEY_ENV, ACCESS)
    monkeypatch.setenv(SECRET_KEY_ENV, SECRET)
    monkeypatch.setenv("MARKETING_MEDIA_S3_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    host = S3MediaHost.from_env_optional()
    assert host is not None
    url = host.presign_get("ig/a.jpg")
    assert urlsplit(url).path == "/media-bucket/ig/a.jpg"
    assert "/auto/s3/aws4_request" in parse_qs(urlsplit(url).query)["X-Amz-Credential"][0]


# -- 어댑터 연결
def _adapter(graph: Recorder, host: S3MediaHost | None) -> GraphInstagramAdapter:
    return GraphInstagramAdapter(
        access_token="test-token-not-real",  # noqa: S106
        user_id="1784140000000000",
        client=graph.client(),
        media_host=host,
    )


def _request(ref: str, kind: str = "image") -> instagram.ContainerRequest:
    return instagram.ContainerRequest(
        media_ref=ref,
        media_kind=kind,  # type: ignore[arg-type]
        caption="시연 (AI 생성)",
        is_ai_generated=True,
        share_to_feed=True,
    )


@pytest.fixture
def media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(root))
    return root


@pytest.mark.asyncio
async def test_local_image_is_uploaded_then_signed_url_goes_to_graph(media_dir: Path) -> None:
    (media_dir / "post.jpg").write_bytes(JPEG)
    s3 = Recorder(httpx.Response(200))
    graph = Recorder(httpx.Response(200, json={"id": "c1"}))
    container = await _adapter(graph, _host(s3)).create_container(_request("post.jpg"))
    assert container == "c1"
    assert len(s3.requests) == 1 and len(graph.requests) == 1
    sent = parse_qs(graph.requests[0].content.decode())["image_url"][0]
    assert sent.startswith("https://") and "X-Amz-Signature=" in sent
    assert sent.split("?")[0] == str(s3.requests[0].url)


@pytest.mark.asyncio
async def test_https_ref_skips_the_bucket(media_dir: Path) -> None:
    s3 = Recorder()
    graph = Recorder(httpx.Response(200, json={"id": "c1"}))
    await _adapter(graph, _host(s3)).create_container(_request("https://cdn.example.test/a.jpg"))
    assert s3.requests == []
    assert parse_qs(graph.requests[0].content.decode())["image_url"] == [
        "https://cdn.example.test/a.jpg"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,body,expected",
    [
        ("missing.jpg", None, (9004, 2207052)),  # 폴더 안에 파일이 없다
        ("../outside.jpg", JPEG, (9004, 2207052)),  # 폴더 밖
        ("post.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, (352, 2207026)),  # JPEG가 아니다
    ],
)
async def test_bad_assets_are_rejected_before_any_request(
    media_dir: Path, name: str, body: bytes | None, expected: tuple[int, int]
) -> None:
    if body is not None:
        target = media_dir / name
        target.resolve().parent.mkdir(parents=True, exist_ok=True)
        target.resolve().write_bytes(body)
    s3, graph = Recorder(), Recorder()
    with pytest.raises(IgApiError) as caught:
        await _adapter(graph, _host(s3)).create_container(_request(name))
    assert (caught.value.code, caught.value.subcode) == expected
    assert s3.requests == [] and graph.requests == []


@pytest.mark.asyncio
async def test_upload_failure_never_reaches_graph(media_dir: Path) -> None:
    (media_dir / "post.jpg").write_bytes(JPEG)
    s3 = Recorder(httpx.Response(403))
    graph = Recorder()
    with pytest.raises(IgApiError) as caught:
        await _adapter(graph, _host(s3)).create_container(_request("post.jpg"))
    assert classify(caught.value.code, caught.value.subcode)[0] == "ig_media_url_unreachable"
    assert graph.requests == []


def test_partial_bucket_config_blocks_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", "test-token-not-real")
    monkeypatch.setenv("MARKETING_IG_USER_ID", "1784140000000000")
    monkeypatch.setenv(BUCKET_ENV, "media-bucket")  # 키 없이 버킷만
    with pytest.raises(GraphConfigError) as caught:
        GraphInstagramAdapter.from_env()
    assert ACCESS_KEY_ENV in str(caught.value) and SECRET_KEY_ENV in str(caught.value)


def test_secret_names_live_only_in_media_host() -> None:
    from marketing_agent.sns_publish import media_host

    source = "".join(
        p.read_text(encoding="utf-8")
        for p in Path(media_host.__file__).parent.glob("*.py")
        if p.name != "media_host.py"
    )
    for name in media_host.SECRET_ENV_NAMES:
        assert name not in source  # 값을 읽는 코드가 이 파일 하나뿐이다


# -- 영상(릴스)
@pytest.mark.asyncio
async def test_upload_video_uses_video_content_type_and_unique_media_id_key() -> None:
    rec = Recorder(httpx.Response(200))
    url = await _host(rec).upload_video(MP4, "med_abc123")
    request = rec.requests[0]
    assert request.method == "PUT" and request.content == MP4
    assert request.headers["content-type"] == "video/mp4"
    path = urlsplit(str(request.url)).path
    assert "med_abc123" in path and "labeled" not in path and path.endswith(".mp4")
    assert "/ig/video/" in path
    assert url.split("?")[0] == str(request.url) and "X-Amz-Signature=" in url


def test_video_key_differs_per_media_id_and_strips_unsafe_chars() -> None:
    host = _host()
    assert host.video_object_key(MP4, "med_a") != host.video_object_key(MP4, "med_b")
    key = host.video_object_key(MP4, "../../x")
    assert ".." not in key and key.count("/") == 2  # ig/video/<이름>.mp4


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"x" * (VIDEO_MAX_BYTES + 1)])
async def test_upload_video_rejects_empty_or_oversize_without_request(content: bytes) -> None:
    rec = Recorder()
    with pytest.raises(MediaHostError) as caught:
        await _host(rec).upload_video(content, "med_a")
    assert caught.value.code == "video_size" and rec.requests == []


@pytest.mark.asyncio
async def test_local_video_is_uploaded_and_signed_url_goes_to_graph_as_video_url(
    media_dir: Path,
) -> None:
    folder = media_dir / "t" / "c" / "med_abc123"
    folder.mkdir(parents=True)
    (folder / "labeled.mp4").write_bytes(MP4)
    s3 = Recorder(httpx.Response(200))
    graph = Recorder(httpx.Response(200, json={"id": "c1"}))
    ref = "t/c/med_abc123/labeled.mp4"
    assert await _adapter(graph, _host(s3)).create_container(_request(ref, "video")) == "c1"
    form = parse_qs(graph.requests[0].content.decode())
    assert form["media_type"] == ["REELS"] and "image_url" not in form
    assert form["video_url"][0].split("?")[0] == str(s3.requests[0].url)
    assert "med_abc123" in str(s3.requests[0].url)
    assert s3.requests[0].headers["content-type"] == "video/mp4"


@pytest.mark.asyncio
async def test_video_https_ref_and_no_host_keep_old_paths(media_dir: Path) -> None:
    s3 = Recorder()
    graph = Recorder(httpx.Response(200, json={"id": "c1"}))
    await _adapter(graph, _host(s3)).create_container(
        _request("https://cdn.example.test/a.mp4", "video")
    )
    assert s3.requests == []
    assert parse_qs(graph.requests[0].content.decode())["video_url"] == [
        "https://cdn.example.test/a.mp4"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,body,expected",
    [
        ("missing.mp4", None, (9004, 2207052)),
        ("bad.mp4", b"not-an-mp4-file-at-all", (352, 2207026)),
    ],
)
async def test_bad_video_is_rejected_before_any_request(
    media_dir: Path, name: str, body: bytes | None, expected: tuple[int, int]
) -> None:
    if body is not None:
        (media_dir / name).write_bytes(body)
    s3, graph = Recorder(), Recorder()
    with pytest.raises(IgApiError) as caught:
        await _adapter(graph, _host(s3)).create_container(_request(name, "video"))
    assert (caught.value.code, caught.value.subcode) == expected
    assert s3.requests == [] and graph.requests == []

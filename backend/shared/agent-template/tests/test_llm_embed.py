import json
import logging
import math

import httpx
import pytest
from pydantic import BaseModel

from ax_agent_template.llm import (
    MOCK_EMBED_DIM,
    LLMClient,
    LLMNotConfiguredError,
    LLMSettings,
    MockLLMClient,
)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_embed_model_prefers_department_value() -> None:
    env = {"HR_EMBED_MODEL": "dept-embed", "EMBED_MODEL": "common-embed"}
    assert LLMSettings.from_env("hr", env).embed_model == "dept-embed"
    assert LLMSettings.from_env("memory", env).embed_model == "common-embed"
    assert LLMSettings.from_env("memory", {"EMBED_MODEL": ""}).embed_model is None


@pytest.mark.asyncio
async def test_mock_embed_is_deterministic_fixed_dim_and_normalized() -> None:
    client = MockLLMClient()
    first = await client.embed(["환불 정책 안내", "배송 지연 문의"])
    second = await client.embed(["환불 정책 안내", "배송 지연 문의"])
    assert first == second
    assert all(len(v) == MOCK_EMBED_DIM == 64 for v in first)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0) for v in first)


@pytest.mark.asyncio
async def test_mock_embed_similar_sentences_are_closer() -> None:
    client = MockLLMClient()
    base, similar, unrelated = await client.embed(
        ["환불 정책 변경 안내", "환불 정책 안내 문서", "쿠폰 발급 한도 초과 오류"]
    )
    assert _cosine(base, similar) > _cosine(base, unrelated)


@pytest.mark.asyncio
async def test_real_embed_needs_configuration() -> None:
    with pytest.raises(LLMNotConfiguredError):
        await LLMClient("memory", LLMSettings()).embed(["a"])
    with pytest.raises(LLMNotConfiguredError):
        await LLMClient("memory", LLMSettings(embed_model="m")).embed(["a"])


@pytest.mark.asyncio
async def test_real_embed_keeps_response_order_when_index_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]},
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler)),
    )
    settings = LLMSettings(base_url="http://llm/v1/", api_key="k", embed_model="m")
    vectors = await LLMClient("memory", settings).embed(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.asyncio
async def test_real_embed_calls_openai_compatible_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler)),
    )
    settings = LLMSettings(base_url="http://llm/v1/", api_key="k", embed_model="m")
    vectors = await LLMClient("memory", settings).embed(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["url"] == "http://llm/v1/embeddings"
    assert seen["auth"] == "Bearer k"


# ── 임베딩 전용 주소·키 (<부서>_EMBED_BASE_URL·_API_KEY → EMBED_* → LLM 주소·키) ──

LLM_ONLY_ENV = {
    "LLM_MODEL": "chat-model",
    "LLM_BASE_URL": "http://llm/v1",
    "LLM_API_KEY": "llm-key",
    "EMBED_MODEL": "embed-model",
}


def test_embed_endpoint_falls_back_to_llm_when_not_set() -> None:
    settings = LLMSettings.from_env("data", LLM_ONLY_ENV)
    assert settings.embed_base_url is None
    assert settings.embed_api_key is None
    assert settings.embed_url == "http://llm/v1"
    assert settings.embed_key == "llm-key"
    # 빈 문자열은 없는 값이다
    blank = LLMSettings.from_env("data", LLM_ONLY_ENV | {"EMBED_BASE_URL": "", "EMBED_API_KEY": ""})
    assert (blank.embed_url, blank.embed_key) == ("http://llm/v1", "llm-key")


def test_embed_endpoint_prefers_department_then_common() -> None:
    env = LLM_ONLY_ENV | {
        "EMBED_BASE_URL": "http://common-embed/v1",
        "EMBED_API_KEY": "common-embed-key",
        "DATA_EMBED_BASE_URL": "http://data-embed/v1",
        "DATA_EMBED_API_KEY": "data-embed-key",
    }
    data = LLMSettings.from_env("data", env)
    assert (data.embed_url, data.embed_key) == ("http://data-embed/v1", "data-embed-key")
    memory = LLMSettings.from_env("memory", env)
    assert (memory.embed_url, memory.embed_key) == ("http://common-embed/v1", "common-embed-key")
    # 문장 생성 설정은 그대로다
    assert (data.base_url, data.api_key, data.model) == ("http://llm/v1", "llm-key", "chat-model")
    # 주소만 다르고 임베딩 키가 없으면 LLM 키를 그 주소로 쓰지 않는다
    only_url = LLMSettings.from_env("data", LLM_ONLY_ENV | {"EMBED_BASE_URL": "http://e/v1"})
    assert (only_url.embed_url, only_url.embed_key) == ("http://e/v1", None)
    assert only_url.embed_key_missing is True
    # 임베딩 주소가 LLM 주소와 같으면(끝 `/`만 달라도) LLM 키를 그대로 쓴다
    same = LLMSettings.from_env("data", LLM_ONLY_ENV | {"EMBED_BASE_URL": "http://llm/v1/"})
    assert (same.embed_key, same.embed_key_missing) == ("llm-key", False)


class _Answer(BaseModel):
    ok: bool


def _record_requests(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """임베딩·문장 생성 요청의 (주소, 인증 헤더)를 모두 기록하는 가짜 서버."""
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler)),
    )
    return seen


@pytest.mark.asyncio
async def test_without_embed_endpoint_both_calls_go_to_llm_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_requests(monkeypatch)
    client = LLMClient("data", LLMSettings.from_env("data", LLM_ONLY_ENV))
    assert await client.embed(["a"]) == [[1.0, 0.0]]
    assert (await client.structured("p", _Answer)).ok is True
    assert seen == [
        ("http://llm/v1/embeddings", "Bearer llm-key"),
        ("http://llm/v1/chat/completions", "Bearer llm-key"),
    ]


@pytest.mark.asyncio
async def test_embed_endpoint_moves_only_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_requests(monkeypatch)
    env = LLM_ONLY_ENV | {
        "DATA_EMBED_BASE_URL": "http://embed/v1/",
        "DATA_EMBED_API_KEY": "embed-key",
    }
    client = LLMClient("data", LLMSettings.from_env("data", env))
    assert await client.embed(["a"]) == [[1.0, 0.0]]
    assert (await client.structured("p", _Answer)).ok is True
    assert seen == [
        ("http://embed/v1/embeddings", "Bearer embed-key"),
        ("http://llm/v1/chat/completions", "Bearer llm-key"),
    ]


@pytest.mark.asyncio
async def test_embed_endpoint_works_without_llm_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_requests(monkeypatch)
    env = {"EMBED_MODEL": "m", "EMBED_BASE_URL": "http://embed/v1"}
    assert await LLMClient("memory", LLMSettings.from_env("memory", env)).embed(["a"]) == [
        [1.0, 0.0]
    ]
    assert seen == [("http://embed/v1/embeddings", None)]


# ── 임베딩 주소가 다른데 임베딩 키가 없으면 LLM 키를 보내지 않는다 ──


@pytest.mark.asyncio
async def test_different_embed_url_without_embed_key_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _record_requests(monkeypatch)
    env = LLM_ONLY_ENV | {"DATA_EMBED_BASE_URL": "http://embed/v1"}
    client = LLMClient("data", LLMSettings.from_env("data", env))
    with caplog.at_level(logging.WARNING, logger="ax.llm"):
        with pytest.raises(LLMNotConfiguredError, match="DATA_EMBED_API_KEY 또는 EMBED_API_KEY"):
            await client.embed(["a"])
    assert seen == []  # 요청을 보내지 않았다
    logs = " ".join(r.getMessage() for r in caplog.records)
    assert "DATA_EMBED_API_KEY 또는 EMBED_API_KEY" in logs
    # 로그에는 빠진 이름만 있고 주소·키 값은 없다
    assert "llm-key" not in logs and "http://embed" not in logs and "http://llm" not in logs
    # 문장 생성은 영향받지 않는다
    assert (await client.structured("p", _Answer)).ok is True
    assert seen == [("http://llm/v1/chat/completions", "Bearer llm-key")]


@pytest.mark.asyncio
async def test_different_embed_url_with_common_embed_key_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_requests(monkeypatch)
    env = LLM_ONLY_ENV | {"EMBED_BASE_URL": "http://embed/v1", "EMBED_API_KEY": "embed-key"}
    assert await LLMClient("data", LLMSettings.from_env("data", env)).embed(["a"]) == [[1.0, 0.0]]
    assert seen == [("http://embed/v1/embeddings", "Bearer embed-key")]


@pytest.mark.asyncio
async def test_same_embed_url_without_embed_key_uses_llm_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_requests(monkeypatch)
    env = LLM_ONLY_ENV | {"EMBED_BASE_URL": "http://llm/v1/"}
    assert await LLMClient("data", LLMSettings.from_env("data", env)).embed(["a"]) == [[1.0, 0.0]]
    assert seen == [("http://llm/v1/embeddings", "Bearer llm-key")]

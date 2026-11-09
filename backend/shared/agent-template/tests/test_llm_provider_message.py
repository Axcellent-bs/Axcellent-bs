"""LLM HTTP 오류의 공급자 메시지: 앞 200자만, 키·토큰 모양은 가린다 (가짜 응답, 실제 호출 없음)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import BaseModel

from ax_agent_template.llm import LLMClient, LLMResponseError, LLMSettings, mask_secrets

SECRET = "sk-test-not-a-real-key-9f2c"
GOOGLE_KEY = "AIzaSyFAKE0000000000000000000000000000"


class Answer(BaseModel):
    x: int


def _call(body: object, status: int = 400) -> LLMResponseError:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    settings = LLMSettings(model="m", base_url="https://llm.example.com/v1", api_key=SECRET)
    client = LLMClient("test", settings=settings, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMResponseError) as caught:
        asyncio.run(client.structured("q", Answer))
    return caught.value


def test_openai_style_error_message_is_kept() -> None:
    err = _call({"error": {"message": "Invalid schema: $defs not allowed"}})
    assert err.status_code == 400 and err.provider_message == "Invalid schema: $defs not allowed"


def test_gemini_list_style_error_message_is_kept() -> None:
    err = _call([{"error": {"code": 400, "message": "Unknown name \"$ref\"", "status": "X"}}])
    assert err.provider_message == "Unknown name \"$ref\""


def test_message_is_cut_to_200_and_secrets_are_masked() -> None:
    text = f"bad key={GOOGLE_KEY} Authorization: Bearer {SECRET} token: abc " + "y" * 500
    err = _call({"error": {"message": text}})
    assert err.provider_message is not None and len(err.provider_message) <= 200
    for secret in (GOOGLE_KEY, SECRET, "abc "):
        assert secret not in err.provider_message
    assert SECRET not in str(err)


def test_no_message_when_the_body_is_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="<html>bad</html>")

    settings = LLMSettings(model="m", base_url="https://llm.example.com/v1", api_key=SECRET)
    client = LLMClient("test", settings=settings, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMResponseError) as caught:
        asyncio.run(client.structured("q", Answer))
    assert caught.value.provider_message is None


@pytest.mark.parametrize("text", [
    f"api_key={GOOGLE_KEY}", f"Bearer {SECRET}", f"secret: {SECRET}",
    "x" + "a1B2c3D4" * 6,  # 긴 토큰 모양 (40자 넘는 영숫자)
])  # fmt: skip
def test_mask_secrets_patterns(text: str) -> None:
    masked = mask_secrets(text)
    assert "***" in masked and GOOGLE_KEY not in masked and SECRET not in masked

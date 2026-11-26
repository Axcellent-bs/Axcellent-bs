"""Anthropic strict 스키마 변환과 한도 (11/26). 실제 LLM은 부르지 않는다 (가짜 서버만).

문서 https://platform.claude.com/docs/en/build-with-claude/structured-outputs:
required에 없는 칸 24·유니온 칸 16이 요청 하나의 한도이고 넘으면 400.
minimum·maxLength 같은 제약은 받지 않는다."""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from pydantic import BaseModel, Field

from ax_agent_template.llm import (
    STRICT_OPTIONAL_MAX,
    STRICT_UNION_MAX,
    LLMClient,
    LLMInputError,
    LLMSettings,
    response_payload,
    strict_complexity,
    strict_schema,
)

ANTHROPIC = "https://api.anthropic.com/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"


class Inner(BaseModel):
    ratio: float = Field(default=0.0, ge=0, le=1)
    tag: str | None = None


class Outer(BaseModel):
    name: str = Field(default="", max_length=60)
    when: str = Field(default="", json_schema_extra={"format": "date-time"})
    link: str = Field(default="", json_schema_extra={"format": "uri-reference"})
    items: list[str] = Field(default_factory=list, min_length=1, max_length=5)
    pair: list[int] = Field(default_factory=list, min_length=2)
    format: str = ""  # 칸 이름이 키워드와 같아도 칸이다
    first: Inner = Field(default_factory=Inner)
    second: Inner | None = None


def _objects(node: object) -> list[dict]:
    out: list[dict] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            out.append(node)
        for key, value in node.items():
            if key in ("properties", "$defs"):
                for v in value.values():
                    out += _objects(v)
            else:
                out += _objects(value)
    elif isinstance(node, list):
        for v in node:
            out += _objects(v)
    return out


def test_every_object_is_closed_and_fully_required() -> None:
    raw = Outer.model_json_schema()
    before = copy.deepcopy(raw)
    strict = strict_schema(raw)
    assert raw == before  # 원본 dict는 그대로 (복사본에 바꾼다)
    objs = _objects(strict)
    assert objs and all(o["additionalProperties"] is False for o in objs)
    assert all(set(o["required"]) == set(o["properties"]) for o in objs)
    assert strict_complexity(strict)[0] == 0
    assert strict_complexity(raw)[0] > 0


def test_unsupported_constraints_move_into_the_description() -> None:
    strict = strict_schema(Outer.model_json_schema())
    props, inner = strict["properties"], strict["$defs"]["Inner"]["properties"]
    assert "maximum" not in inner["ratio"] and "minimum" not in inner["ratio"]
    assert inner["ratio"]["description"].endswith("(0~1)")
    assert "maxLength" not in props["name"] and "(0~60자)" in props["name"]["description"]
    assert props["when"]["format"] == "date-time"  # 지원하는 format은 그대로
    assert "format" not in props["link"] and "형식 uri-reference" in props["link"]["description"]
    assert props["items"]["minItems"] == 1 and "maxItems" not in props["items"]  # 0·1은 그대로
    assert "minItems" not in props["pair"] and "(2~개)" in props["pair"]["description"]
    assert props["format"]["type"] == "string"  # 칸 이름 "format"은 지우지 않는다


def test_refs_count_at_every_use() -> None:
    raw = Outer.model_json_schema()
    optional, unions = strict_complexity(raw)
    # Inner(칸 2개)를 first·second 두 곳에서 쓰므로 두 번 센다. 유니온: tag ×2 + second
    assert unions == 3
    assert optional == 8 + 2 * 2


def _schema_with(unions: int) -> dict:
    props = {f"u{i}": {"anyOf": [{"type": "string"}, {"type": "null"}]} for i in range(unions)}
    return {"type": "object", "properties": props}


def test_over_the_limit_fails_before_the_request() -> None:
    assert STRICT_OPTIONAL_MAX == 24 and STRICT_UNION_MAX == 16
    response_payload(ANTHROPIC, "m", [], "Ok", _schema_with(16))  # 한도 안
    with pytest.raises(LLMInputError, match="optional 0, union 17"):
        response_payload(ANTHROPIC, "m", [], "Big", _schema_with(17))
    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": {"message": "Schema is too complex"}})

    class Big(BaseModel):
        model_config = {"json_schema_extra": _schema_with(17)}

    client = LLMClient("planning", settings=LLMSettings(model="m", base_url=ANTHROPIC),
                       transport=httpx.MockTransport(reply))  # fmt: skip
    with pytest.raises(LLMInputError):
        asyncio.run(client.structured("p", Big))
    assert seen == []  # 400을 받으러 가지 않는다


def test_other_addresses_send_the_schema_as_before() -> None:
    raw = Outer.model_json_schema()
    p = response_payload(GEMINI, "g", [], "Out", raw)
    assert p["response_format"]["json_schema"] == {"name": "Out", "schema": raw}
    assert "required" not in p["response_format"]["json_schema"]["schema"]
    assert p["temperature"] == 0


def test_answers_are_still_checked_with_the_original_model() -> None:
    sent: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        content = json.dumps({"name": "a", "items": ["x"], "first": {"ratio": 0.5}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = LLMClient("planning", settings=LLMSettings(model="m", base_url=ANTHROPIC),
                       transport=httpx.MockTransport(reply))  # fmt: skip
    got = asyncio.run(client.structured("p", Outer))
    assert got.first.ratio == 0.5 and got.second is None  # 빠진 칸은 pydantic 기본값
    schema = sent[0]["response_format"]["json_schema"]
    assert schema["strict"] is True and set(schema["schema"]["required"]) == set(Outer.model_fields)

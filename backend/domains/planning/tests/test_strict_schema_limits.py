"""기획 LLM 스키마가 Anthropic strict 한도 안인지 (11/26, 콘티 포즈 400).

콘티 포즈 스키마는 required가 하나도 없어(start·end 두 번) 선택 칸이 38개라 한도 24를 넘었다.
변환 뒤에는 모든 칸이 required라 0개. 실제 LLM은 부르지 않는다 (스키마 계산·가짜 서버만)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ax_agent_template.llm import (
    STRICT_OPTIONAL_MAX,
    STRICT_UNION_MAX,
    LLMClient,
    LLMSettings,
    strict_complexity,
    strict_schema,
)
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")
ANTHROPIC = "https://api.anthropic.com/v1"


def _objects(node: object) -> list[dict]:
    out: list[dict] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            out.append(node)
        for key, value in node.items():
            for v in (value.values() if key in ("properties", "$defs") else [value]):
                out += _objects(v)
    elif isinstance(node, list):
        for v in node:
            out += _objects(v)
    return out


@pytest.mark.parametrize("model", [sb._PoseOut, vp._PlanOut])  # noqa: SLF001
def test_planning_schemas_fit_the_strict_limits(model: type) -> None:
    raw = model.model_json_schema()
    strict = strict_schema(raw)
    optional, unions = strict_complexity(strict)
    assert optional == 0 and unions <= STRICT_UNION_MAX
    objs = _objects(strict)
    assert all(o["additionalProperties"] is False and set(o["required"]) == set(o["properties"])
               for o in objs)  # fmt: skip


def test_pose_schema_was_over_the_limit_before() -> None:
    assert strict_complexity(sb._PoseOut.model_json_schema())[0] > STRICT_OPTIONAL_MAX  # noqa: SLF001


def test_plan_answer_is_checked_with_the_original_model() -> None:
    sent: list[dict] = []
    scenes = [{"visual": f"장면 {i}", "prompt": "A traveler", "people": 1} for i in range(4)]
    answer = {"concept": "겨울 여행", "script": "훅", "scenes": scenes}

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    client = LLMClient("planning", settings=LLMSettings(model="m", base_url=ANTHROPIC),
                       transport=httpx.MockTransport(reply))  # fmt: skip
    got = asyncio.run(client.structured("p", vp._PlanOut))  # noqa: SLF001
    assert got.scenes[0].caption == "" and got.request_rules == []  # 빠진 칸은 기본값
    schema = sent[0]["response_format"]["json_schema"]["schema"]
    assert "minLength" not in json.dumps(schema) and "minItems" in json.dumps(schema)  # 1은 그대로

"""출하 단가표 + 실제 Gemini 이미지 어댑터(가짜 전송) + 장면 템플릿 경로 전체.

`cat_bookstore_image` 템플릿은 quality="medium"을 고정하는데, 단가표의 gemini 이미지 항목에는
quality가 없다. 이 조합이 단가 조회에서 거절되면 템플릿 경로로는 gemini 이미지를 만들 수 없다.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, ExecuteDecision
from marketing_agent import handlers
from marketing_agent.media import registry, skill
from marketing_agent.media.gemini_image import GeminiImageProvider
from marketing_agent.media.ledger import MediaLedger

JPG = b"\xff\xd8\xff\xe0fake-image-bytes"
FIELDS = {"season": "spring", "color_theme": "soft pastel", "category": "그림책"}
# 장당 1,120토큰, 출력 30.00 USD/백만 토큰, 입력 1.50 USD/백만 토큰(단가표 값 그대로)
OUT_USD = 1120 * 30 / 1_000_000


class Gate:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append(params)
        return ExecuteDecision(execution_id="exe_g1", grade="B", status="executed")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[httpx.Request]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    for name, value in {
        "MARKETING_IMAGE_PROVIDER": "gemini",
        "MARKETING_IMAGE_MODEL": "gemini-nano-banana-2.1",
        "MARKETING_IMAGE_API_KEY": "test-key-not-real",
        "MARKETING_FX_USD_KRW": "1500",
        "MARKETING_FX_AS_OF": datetime.now(UTC).date().isoformat(),
        "MARKETING_FX_MAX_AGE_DAYS": "7",
        "MARKETING_COST_SAFETY_MARGIN_PCT": "10",
        "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM": "1000",
        "MARKETING_MEDIA_MONTHLY_BUDGET_KRW": "100000",
        "MARKETING_MEDIA_DIR": str(tmp_path / "media"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(skill, "execute", Gate())
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = {
            "id": "interaction_1",
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "image",
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(JPG).decode(),
                        }
                    ],
                }
            ],
            # 실제 응답엔 total_thought_tokens가 따로 있으나 어댑터는 읽지 않는다
            "usage": {"total_input_tokens": 120, "total_output_tokens": 1120},
        }
        return httpx.Response(200, json=body)

    monkeypatch.setitem(
        registry.ADAPTERS,
        "gemini",
        lambda: GeminiImageProvider(transport=httpx.MockTransport(handler)),
    )
    return seen


def _request(**over: object) -> AgentRequest:
    input_ = {
        "post_draft_id": "DRAFT-1",
        "media_kind": "image",
        "scene_template_id": "cat_bookstore_image",
        "fields": FIELDS,
    } | over
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": "ctx_gemini_tpl_0001",
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": input_,
        }
    )


@pytest.mark.asyncio
async def test_template_path_generates_gemini_image_with_shipped_price_table(
    env: list[httpx.Request],
) -> None:
    result = await skill.generate_media(
        _request(), ledger=MediaLedger(), from_decision=handlers._from_decision
    )
    assert result.outcome == "generated", result.output
    out = result.output
    assert out is not None
    assert out["provider"] == "gemini" and out["resolution"] == "1024x1024"
    assert out["usage_source"] == "actual"
    assert len(env) == 1
    sent = json.loads(env[0].content)
    assert sent["response_format"]["aspect_ratio"] == "1:1"
    # 단가는 표 값 그대로: 출력 1,120토큰 x 30 USD/백만 토큰 (+ 입력 토큰)
    assert float(out["usd_actual"]) >= OUT_USD
    assert out["cost_est_krw"] >= out["cost_krw"] > 0


@pytest.mark.asyncio
async def test_unpriced_quality_still_refused_when_entries_define_quality(
    env: list[httpx.Request], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """quality가 정의된 항목끼리는 정확히 비교한다: 표에 없는 quality는 호출 전에 거부."""
    prices = tmp_path / "prices.toml"
    prices.write_text(
        """
version = 1

[[price]]
provider = "gemini"
model = "gemini-nano-banana-2.1"
kind = "image"
resolution = "1024x1024"
quality = "low"
input_usd_per_mtok = 1.50
output_usd_per_mtok = 30.00
output_tokens = 1120
source = "https://example.test/pricing"
checked_on = "2026-11-11"
""",
        encoding="utf-8",
    )
    result = await skill.generate_media(
        _request(),
        ledger=MediaLedger(),
        from_decision=handlers._from_decision,
        prices_path=prices,
    )
    assert result.outcome == "price_not_found" and env == []

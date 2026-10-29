"""설명 문장을 템플릿으로 대체한 이유 (`fallback_reason`).

- `llm_unavailable`: 모델 설정 없음 / `llm_error`: 호출 실패 / `format_error`: 형식 불일치·빈 문장 /
  `number_invalid`: 계산에 없는 숫자 (걸린 숫자는 `fallback_numbers`)
- 이유는 skill 근거(evidence)와 A2A 결과(`AgentResult.fallback_reason`)에 남고, 템플릿이 공통 로그
  `result_sent`에 옮긴다. LLM 문장 원문은 남기지 않는다.
- data `/health`에 `llm_configured`
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from ax_agent_template.llm import (
    LLMClient,
    LLMFormatError,
    LLMResponseError,
    LLMSettings,
    MockLLMClient,
)
from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.schemas.data import ImpactAnalysisInput
from data_agent import handlers
from data_agent.explain import (
    FALLBACK_REASONS,
    FORMAT_ERROR,
    LLM_ERROR,
    LLM_UNAVAILABLE,
    NUMBER_INVALID,
    _Summary,
    explain,
)
from data_agent.main import build_app, llm_configured
from data_agent.skills import impact_analysis as impact_skill

IMPACT = {"failed_order_count": 12, "affected_customer_count": 9, "damage_amount": 1234000}
LLM_ENV = ("DATA_LLM_MODEL", "LLM_MODEL", "DATA_LLM_BASE_URL", "LLM_BASE_URL")


class _Raise(LLMClient):
    """설정은 있지만 부르면 `error`를 던지는 LLM."""

    def __init__(self, error: Exception) -> None:
        super().__init__("data", settings=LLMSettings(model="m", base_url="http://llm"))
        self.error = error

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        raise self.error


def _say(text: str) -> MockLLMClient:
    return MockLLMClient(responses={_Summary: _Summary(summary=text)})


def _validation_error() -> ValidationError:
    class _Shape(BaseModel):
        summary: str

    try:
        _Shape.model_validate({})
    except ValidationError as error:
        return error
    raise AssertionError("검증 오류가 나야 한다")


# ---------- 이유 4가지 ----------


def test_there_are_four_reasons() -> None:
    assert set(FALLBACK_REASONS) == {
        "llm_unavailable",
        "llm_error",
        "format_error",
        "number_invalid",
    }


@pytest.mark.asyncio
async def test_no_model_setting_is_llm_unavailable() -> None:
    result = await explain("impact", IMPACT, LLMClient("data", settings=LLMSettings()))
    assert (result.fallback, result.reason, result.bad_numbers) == (True, LLM_UNAVAILABLE, ())
    assert result.summary.startswith("장애 구간 실패 주문 12건")  # 템플릿


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [TimeoutError("시간 초과"), LLMResponseError("LLM API가 503를 돌려줬다"), OSError("연결")],
    ids=["timeout", "http", "network"],
)
async def test_call_failure_is_llm_error(error: Exception) -> None:
    result = await explain("impact", IMPACT, _Raise(error))
    assert (result.fallback, result.reason) == (True, LLM_ERROR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm",
    [
        _Raise(LLMFormatError("응답이 스키마와 맞지 않는다 — 2번 요청했다")),
        _Raise(_validation_error()),
        _say("   "),  # 빈 문장
    ],
    ids=["schema-after-retries", "validation", "empty"],
)
async def test_wrong_shape_is_format_error(llm: LLMClient) -> None:
    result = await explain("impact", IMPACT, llm)
    assert (result.fallback, result.reason) == (True, FORMAT_ERROR)


@pytest.mark.asyncio
async def test_made_up_numbers_are_listed() -> None:
    text = "실패 주문 12건, 영향 고객 999명, 추정 손실 9,876,543원입니다."
    result = await explain("impact", IMPACT, _say(text))
    assert (result.fallback, result.reason) == (True, NUMBER_INVALID)
    assert result.bad_numbers == (999.0, 9876543.0)  # 12는 계산에 있어 빠진다
    note = result.note()
    assert note == (
        "설명 문장을 템플릿으로 대체함: number_invalid (계산에 없는 숫자: 999, 9876543)"
    )
    assert "추정 손실" not in note  # LLM 문장 원문은 남기지 않는다


@pytest.mark.asyncio
async def test_kept_sentence_has_no_reason() -> None:
    result = await explain("impact", IMPACT, _say("실패 주문 12건입니다."))
    assert (result.fallback, result.reason, result.note()) == (False, None, None)


# ---------- skill 근거와 A2A 결과 ----------

START = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
WINDOW = ImpactAnalysisInput(window_from=START, window_to=START + timedelta(minutes=30))


class _Shop:
    """구간 실패 주문 2건만 주는 shop (지난 7일 결제 수는 주지 않음 → 초과분 없음)."""

    async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        orders = [
            FailedOrder(
                order_id=f"o{i}",
                buyer_id=f"b{i}",
                amount=10000,
                category="book",
                failed_at=START + timedelta(minutes=i),
                reason="PG_UNAVAILABLE",
            )
            for i in range(2)
        ]
        return ShopFailedOrdersResult(orders=[o for o in orders if query.from_ <= o.failed_at])


def _post(monkeypatch: pytest.MonkeyPatch, llm: LLMClient | None) -> dict:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    if llm is not None:
        monkeypatch.setattr(handlers, "get_llm", lambda: llm)
    monkeypatch.setattr(impact_skill, "get_shop_client", lambda env=None: _Shop())
    body = {
        "meta": {
            "context_id": "ctx_inc_fallback",
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "impact_analysis",
        "input": WINDOW.model_dump(mode="json"),
    }
    return TestClient(build_app()).post("/a2a", json=body).json()


def test_handler_reports_made_up_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _post(monkeypatch, _say("실패 주문 2건, 추정 손실 9,876,543원입니다."))
    assert result["status"] == "completed"
    assert result["fallback"] is True
    assert result["fallback_reason"] == "number_invalid"
    assert result["fallback_numbers"] == [9876543.0]
    summary = result["evidence"]["data_summary"]
    assert "설명 문장을 템플릿으로 대체함: number_invalid (계산에 없는 숫자: 9876543)" in summary
    assert "추정 손실" not in summary  # LLM 문장 원문은 근거에도 남기지 않는다


def test_handler_without_model_setting_is_llm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    result = _post(monkeypatch, None)  # 실제 get_llm (설정 없음)
    assert (result["fallback"], result["fallback_reason"]) == (True, "llm_unavailable")
    assert result["fallback_numbers"] == []
    assert "설명 문장을 템플릿으로 대체함: llm_unavailable" in result["evidence"]["data_summary"]


def test_handler_without_fallback_has_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _post(monkeypatch, _say("실패 주문 2건입니다."))
    assert (result["fallback"], result["fallback_reason"]) == (False, None)
    assert "템플릿으로 대체" not in result["evidence"]["data_summary"]


# ---------- health: llm_configured ----------


def test_llm_configured_needs_model_and_address() -> None:
    assert llm_configured({}) is False
    assert llm_configured({"LLM_MODEL": "m"}) is False  # 주소 없음
    assert llm_configured({"DATA_LLM_MODEL": "m", "LLM_BASE_URL": "http://llm"}) is True
    assert llm_configured({"LLM_MODEL": "m", "LLM_BASE_URL": "http://llm"}) is True


@pytest.mark.parametrize("configured", [False, True])
def test_health_reports_llm_configured(monkeypatch: pytest.MonkeyPatch, configured: bool) -> None:
    for name in LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    if configured:
        monkeypatch.setenv("DATA_LLM_MODEL", "m")
        monkeypatch.setenv("DATA_LLM_BASE_URL", "http://llm")
    body = TestClient(build_app()).get("/health").json()
    assert body == {"data": {"status": "ok", "llm_configured": configured}, "meta": {}}

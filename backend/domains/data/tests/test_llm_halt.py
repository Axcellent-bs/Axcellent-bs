"""data LLM 오류 원인 분류와 호출 중단 (`llm_halt`). 실제 LLM은 부르지 않는다 (가짜 응답).

- 401·403 → key_error: 그날(한국 0시까지) 호출을 멈춘다
- 429 → provider_limit: 1시간 뒤 다시 시도, 3번 연속 429면 그날 멈춘다 (성공하면 연속 수 초기화)
- 우리 하루 상한(llm_budget) → our_cap (상한 셈 방식은 그대로)
- 멈춘 동안은 HTTP 요청을 아예 보내지 않는다 (실패 호출로 횟수가 차지 않게)
- 상태: key_source(키 이름만), model, base_url 호스트만. 키 값은 어디에도 남기지 않는다
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import BaseModel

from ax_agent_template.llm import LLMNotConfiguredError, LLMResponseError, LLMSettings
from data_agent import llm_budget, llm_halt
from data_agent.handlers import TimedLLMClient

SECRET = "sk-test-not-a-real-key-9f2c"  # 가짜 키 (값이 새지 않는지 본다)
T0 = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)  # 한국 12:00
MIDNIGHT = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # 다음 한국 0시


class Answer(BaseModel):
    x: int


class FakeLLM:
    """가짜 공급자: 정해 둔 상태 코드를 차례로 돌려주고, 받은 요청을 센다."""

    def __init__(self, *codes: int) -> None:
        self.codes = list(codes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        code = self.codes.pop(0) if self.codes else 200
        if code != 200:
            return httpx.Response(code, json={"error": {"message": "x"}})
        body = {"choices": [{"message": {"content": json.dumps({"x": 1})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}  # fmt: skip
        return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> dict:
    """중단 상태·하루 셈은 테스트마다 새로 (메모리), 시계는 고정."""
    clock = {"now": T0}
    monkeypatch.setattr(llm_halt, "HALT", llm_halt.HaltStore(None))
    monkeypatch.setattr(llm_budget, "BUDGET", llm_budget.Budget(None))
    monkeypatch.setattr(llm_halt, "_now", lambda: clock["now"])
    return clock


def _client(fake: FakeLLM, base_url: str = "https://llm.example.com/v1",
            model: str = "test-model") -> TimedLLMClient:  # fmt: skip
    settings = LLMSettings(model=model, base_url=base_url, api_key=SECRET)
    return TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(fake))


def _ask(client: TimedLLMClient) -> Answer:
    return asyncio.run(client.structured("질문", Answer))


@pytest.mark.parametrize("code", [401, 403])
def test_key_error_stops_until_midnight_without_calling(code: int) -> None:
    fake = FakeLLM(code)
    client = _client(fake)
    with pytest.raises(LLMResponseError):
        _ask(client)
    halt = llm_halt.HALT.active()
    assert halt is not None and halt.reason == "key_error" and halt.until == MIDNIGHT
    with pytest.raises(llm_halt.LLMHalted):  # 멈춘 동안: HTTP 요청 0회
        _ask(client)
    assert len(fake.requests) == 1
    assert isinstance(llm_halt.LLMHalted("x"), LLMNotConfiguredError)  # 설정 없음과 같은 대체 길


def test_429_waits_an_hour_then_three_in_a_row_stops_for_the_day(fresh: dict) -> None:
    fake = FakeLLM(429, 429, 429)
    client = _client(fake)
    with pytest.raises(LLMResponseError):
        _ask(client)
    halt = llm_halt.HALT.active()
    assert (halt.reason, halt.until, halt.strikes) == ("provider_limit", T0 + timedelta(hours=1), 1)
    with pytest.raises(llm_halt.LLMHalted):
        _ask(client)
    assert len(fake.requests) == 1  # 1시간 안에는 다시 부르지 않음
    fresh["now"] = T0 + timedelta(hours=1, minutes=1)
    with pytest.raises(LLMResponseError):
        _ask(client)  # 2번째 429
    fresh["now"] = T0 + timedelta(hours=2, minutes=2)
    with pytest.raises(LLMResponseError):
        _ask(client)  # 3번째 429 → 그날 멈춤
    halt = llm_halt.HALT.active()
    assert (halt.reason, halt.strikes, halt.until) == ("provider_limit", 3, MIDNIGHT)
    fresh["now"] = T0 + timedelta(hours=5)
    with pytest.raises(llm_halt.LLMHalted):
        _ask(client)
    assert len(fake.requests) == 3


def test_a_success_resets_the_429_count(fresh: dict) -> None:
    fake = FakeLLM(429, 200, 429)
    client = _client(fake)
    with pytest.raises(LLMResponseError):
        _ask(client)
    fresh["now"] = T0 + timedelta(hours=1, minutes=1)
    assert _ask(client).x == 1
    assert llm_halt.HALT.active() is None
    fresh["now"] = T0 + timedelta(hours=1, minutes=2)
    with pytest.raises(LLMResponseError):
        _ask(client)
    assert llm_halt.HALT.active().strikes == 1  # 연속이 아니라 다시 1번째


def test_our_cap_is_its_own_cause_and_stops_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_LLM_DAILY_CALLS", "3")
    llm_budget.BUDGET.add(3, 1200)
    fake = FakeLLM()
    with pytest.raises(llm_budget.LLMDailyCapReached):
        _ask(_client(fake))
    assert fake.requests == []
    status = llm_halt.status({"DATA_LLM_DAILY_CALLS": "3", "DATA_LLM_API_KEY": SECRET})
    assert (status.state, status.tone, status.title) == ("our_cap", "blue", "우리 하루 상한 도달")
    assert status.detail == "정상 호출 3/3회 · 토큰 1,200/400,000 · 0시에 초기화됩니다."


def test_status_texts_for_each_cause() -> None:
    gemini = "https://generativelanguage.googleapis.com/v1beta/openai/"
    env = {"DATA_LLM_API_KEY": SECRET, "DATA_LLM_MODEL": "gemini-2.5-flash",
           "DATA_LLM_BASE_URL": gemini}  # fmt: skip
    ok = llm_halt.status(env)
    assert ok.state == "ok" and ok.model == "gemini-2.5-flash"
    assert ok.base_host == "generativelanguage.googleapis.com"
    llm_halt.HALT.record_error(401)
    key = llm_halt.status(env)
    assert (key.state, key.tone, key.title) == ("key_error", "red", "LLM 키 오류 (401·403)")
    assert key.detail == ("쓰는 키: DATA_LLM_API_KEY · 오늘은 호출을 멈췄습니다."
                          " 키를 넣고 data를 재시작하세요.")  # fmt: skip
    llm_halt.HALT = llm_halt.HaltStore(None)
    llm_halt.HALT.record_error(429)
    limit = llm_halt.status(env)
    assert (limit.state, limit.tone, limit.title) == (
        "provider_limit", "yellow", "LLM 공급자 한도 초과 (429)")  # fmt: skip
    assert limit.detail == ("공급자 쪽 하루 한도에 닿았습니다. 1시간 뒤 다시 시도하고,"
                            " 3번 연속이면 오늘은 멈춥니다.")  # fmt: skip


@pytest.mark.parametrize(("env", "source"), [
    ({"DATA_LLM_API_KEY": SECRET, "LLM_API_KEY": "other"}, "DATA_LLM_API_KEY"),
    ({"LLM_API_KEY": SECRET}, "공통 LLM_API_KEY(폴백)"),
    ({}, "키 없음"),
])  # fmt: skip
def test_key_source_is_the_name_only(env: dict, source: str) -> None:
    status = llm_halt.status(env)
    assert status.key_source == source
    assert SECRET not in status.model_dump_json() and "other" not in status.model_dump_json()


def test_no_key_value_in_status_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeLLM(401)
    with pytest.raises(LLMResponseError):
        _ask(_client(fake))
    status = llm_halt.status({"DATA_LLM_API_KEY": SECRET})
    text = status.model_dump_json() + "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in text
    assert "key_error" in "\n".join(r.getMessage() for r in caplog.records)


def test_gemini_openai_compatible_address_works_with_this_client() -> None:
    """Gemini OpenAI 호환 주소·모델로 넣었을 때 요청 모양 (가짜 응답, 실제 키·호출 없음)."""
    fake = FakeLLM()
    client = _client(fake, base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                     model="gemini-2.5-flash")  # fmt: skip
    assert _ask(client).x == 1
    (req,) = fake.requests
    assert str(req.url) == ("https://generativelanguage.googleapis.com/v1beta/openai"
                            "/chat/completions")  # fmt: skip
    assert req.headers["authorization"].startswith("Bearer ")
    body = json.loads(req.content)
    assert body["model"] == "gemini-2.5-flash" and body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert llm_budget.BUDGET.usage().calls == 1 and llm_budget.BUDGET.usage().tokens == 12


def test_sources_usage_carries_the_status() -> None:
    from data_agent.skills.data_sources import llm_usage_today

    llm_halt.HALT.record_error(429)
    usage = llm_usage_today({"LLM_API_KEY": SECRET})
    assert usage.status is not None and usage.status.state == "provider_limit"
    assert usage.status.key_source == "공통 LLM_API_KEY(폴백)"
    assert SECRET not in usage.model_dump_json()


# ---------------------------------------------------------------- 400 요청 형식 오류


class Other(BaseModel):
    y: str


GOOGLE_KEY = "AIzaSyFAKE0000000000000000000000000000"  # 가짜 키 모양 (가려져야 함)


def _bad_request(message: str):  # noqa: ANN202
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json=[{"error": {"code": 400, "message": message,
                                                    "status": "INVALID_ARGUMENT"}}])  # fmt: skip

    return calls, handler


def test_400_stops_only_the_same_request_until_midnight() -> None:
    calls, handler = _bad_request("Invalid JSON payload received. Unknown name \"$defs\"")
    settings = LLMSettings(model="gemini-3.5-flash", base_url="https://llm.example.com/v1",
                           api_key=SECRET)  # fmt: skip
    client = TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMResponseError):
        _ask(client)
    halt = llm_halt.HALT.active()
    assert halt is not None and (halt.reason, halt.until) == ("request_error", MIDNIGHT)
    with pytest.raises(llm_halt.LLMHalted):  # 같은 요청(같은 스키마·모델): HTTP 0회
        _ask(client)
    assert len(calls) == 1
    with pytest.raises(LLMResponseError):  # 다른 스키마는 계속 보낸다
        asyncio.run(client.structured("질문", Other))
    assert len(calls) == 2


def test_400_status_text_and_masked_provider_message() -> None:
    long = f"Bad schema; key={GOOGLE_KEY} Authorization: Bearer {SECRET} " + "x" * 400
    _, handler = _bad_request(long)
    settings = LLMSettings(model="gemini-3.5-flash", base_url="https://llm.example.com/v1",
                           api_key=SECRET)  # fmt: skip
    client = TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMResponseError):
        _ask(client)
    status = llm_halt.status({"DATA_LLM_API_KEY": SECRET, "DATA_LLM_MODEL": "gemini-3.5-flash"})
    assert (status.state, status.tone, status.title) == (
        "request_error", "red", "LLM 요청 형식 오류 (400)")  # fmt: skip
    assert status.detail == ("공급자가 응답 형식(스키마)을 받지 않았습니다. 모델: gemini-3.5-flash"
                             " · 같은 요청은 오늘 다시 보내지 않습니다."
                             " 모델·주소 설정을 확인하세요.")  # fmt: skip
    assert status.request == "Answer" and status.error_message is not None
    assert len(status.error_message) <= 200 and status.error_message.startswith("Bad schema")
    dumped = status.model_dump_json()
    assert GOOGLE_KEY not in dumped and SECRET not in dumped


def test_key_error_still_comes_first() -> None:
    llm_halt.HALT.record_error(400, request_key="m|Answer", message="bad", model="m")
    llm_halt.HALT.record_error(401)
    assert llm_halt.status({}).state == "key_error"


def test_gemini_35_openai_compatible_request_shape() -> None:
    """gemini-3.5-flash + Gemini OpenAI 호환 주소: 주소·본문 (가짜 응답, 실제 키·호출 없음)."""
    fake = FakeLLM()
    client = _client(fake, base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                     model="gemini-3.5-flash")  # fmt: skip
    assert _ask(client).x == 1
    (req,) = fake.requests
    assert req.url.path == "/v1beta/openai/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "gemini-3.5-flash" and body["temperature"] == 0
    block = body["response_format"]["json_schema"]
    assert block["name"] == "Answer" and "strict" not in block  # Gemini는 strict 없이 (예전 그대로)

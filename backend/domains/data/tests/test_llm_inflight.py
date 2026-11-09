"""같은 LLM 요청 중복 막기 (`llm_inflight`). 실제 LLM은 부르지 않는다 (가짜 서버).

- 같은 요청이 동시에 오면 LLM 1회, 하루 셈도 1회.
  다른 요청·끝난 뒤 따로 온 같은 요청은 그대로 부른다
- 먼저 부른 쪽이 실패하면 기다리던 쪽 하나가 대신 1번
- 만료된 표시는 넘겨받고, 살아 있는 표시는 기다린다
- SQLite 파일을 같이 쓰는 두 저장소(서버·수집기 프로세스 자리)도 1회
- 수집기 새 숏츠 형식 분류는 장부에 fresh_shorts
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from ax_agent_template.llm import LLMResponseError, LLMSettings
from data_agent import llm_budget, llm_inflight
from data_agent.handlers import TimedLLMClient
from data_agent.stage_timing import recording


class Answer(BaseModel):
    x: int


class FakeLLM:
    """가짜 공급자: `latency`초 뒤 답한다. `fail_first`면 첫 요청에 500. 받은 요청을 센다."""

    def __init__(self, latency: float = 0.2, fail_first: bool = False, body: dict | None = None):
        self.latency, self.fail_first, self.calls = latency, fail_first, 0
        self.body = body if body is not None else {"x": 7}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        first = self.calls == 1
        await asyncio.sleep(self.latency)
        if self.fail_first and first:
            return httpx.Response(500, json={"error": {"message": "x"}})
        message = {"message": {"content": json.dumps(self.body)}}
        usage = {"prompt_tokens": 10, "completion_tokens": 2}
        return httpx.Response(200, json={"choices": [message], "usage": usage})


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> llm_budget.Budget:
    fresh = llm_budget.Budget(None)
    monkeypatch.setattr(llm_budget, "BUDGET", fresh)
    return fresh


def _client(fake: FakeLLM) -> TimedLLMClient:
    settings = LLMSettings(model="test-model", base_url="https://llm.example.com/v1", api_key=None)
    return TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(fake))


async def _ask(client: TimedLLMClient, prompt: str = "분류", delay: float = 0.0) -> object:
    await asyncio.sleep(delay)
    with recording("trend_popular", emit=lambda line: None):
        try:
            return (await client.structured(prompt, Answer)).x
        except LLMResponseError:
            return "error"


async def _gather(*coros) -> list:  # noqa: ANN002
    return list(await asyncio.gather(*coros))


def test_same_request_three_at_once_calls_once(budget: llm_budget.Budget) -> None:
    fake = FakeLLM()
    client = _client(fake)
    got = asyncio.run(_gather(_ask(client), _ask(client), _ask(client)))
    assert got == [7, 7, 7] and fake.calls == 1
    assert budget.usage().calls == 1  # 기다린 요청은 하루 셈에 안 더함


def test_different_requests_and_later_same_request_still_call() -> None:
    fake = FakeLLM()
    client = _client(fake)
    assert asyncio.run(_gather(_ask(client, "A"), _ask(client, "B"))) == [7, 7]
    assert fake.calls == 2
    asyncio.run(_ask(client, "A"))  # 끝난 뒤 따로 온 같은 요청: 예전처럼 새로 부른다
    assert fake.calls == 3


def test_owner_fails_one_waiter_takes_over() -> None:
    fake = FakeLLM(latency=0.3, fail_first=True)
    client = _client(fake)
    got = asyncio.run(_gather(_ask(client), _ask(client, delay=0.05), _ask(client, delay=0.05)))
    assert got == ["error", 7, 7] and fake.calls == 2


def test_expired_mark_is_taken_over_and_live_mark_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    store = llm_inflight.Inflight(None, lease=0.6, poll=0.05)
    monkeypatch.setattr(llm_inflight, "INFLIGHT", store)
    key = llm_inflight.request_key("test-model", Answer, "분류", None)
    assert store.claim(key) is not None  # 죽은 프로세스가 남긴 표시
    assert store.claim(key) is None  # 살아 있는 동안은 못 가져감
    fake = FakeLLM(latency=0.0)
    t = time.monotonic()
    assert asyncio.run(_ask(_client(fake))) == 7
    assert time.monotonic() - t >= 0.5 and fake.calls == 1  # 만료까지 기다린 뒤 1번
    assert store.claim(key, now=time.time() + 1.0) is not None  # 만료된 표시는 넘겨받는다


def test_two_stores_on_one_file_call_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """서버·수집기 자리: 같은 SQLite 파일을 여는 저장소 둘이 같은 요청 → 1회."""
    path = tmp_path / "budget.db"
    server, collector = llm_inflight.Inflight(path), llm_inflight.Inflight(path)
    fake = FakeLLM(latency=0.4)
    client = _client(fake)

    async def via(store: llm_inflight.Inflight, delay: float) -> object:
        await asyncio.sleep(delay)
        key = llm_inflight.request_key("test-model", Answer, "분류", None)
        with recording("trend_popular", emit=lambda line: None):
            got = await store.run(
                key,
                lambda: client._structured("분류", Answer),
                dump=lambda r: r.model_dump(),
                load=Answer.model_validate,
            )
        return got.x

    assert asyncio.run(_gather(via(server, 0.0), via(collector, 0.1))) == [7, 7]
    assert fake.calls == 1


def test_lease_default_and_setting() -> None:
    assert llm_inflight.lease_seconds({}) == 120.0
    assert llm_inflight.lease_seconds({llm_inflight.LEASE_ENV: "30"}) == 30.0
    assert llm_inflight.lease_seconds({llm_inflight.LEASE_ENV: "x"}) == 120.0


def test_fresh_shorts_classification_is_booked_as_fresh_shorts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: llm_budget.Budget
) -> None:  # fmt: skip
    from data_agent import fresh_shorts, handlers
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV

    client = _client(FakeLLM(latency=0.0, body={"items": []}))
    monkeypatch.setattr(handlers, "get_llm", lambda: client)
    stats = [SimpleNamespace(video_id=f"v{i}", title="t", channel_title="c", view_count=1,
                             tags=[]) for i in range(2)]  # fmt: skip
    asyncio.run(fresh_shorts._llm_classify(stats, {SHORTS_DB_ENV: str(tmp_path / "s.db")}))
    assert {skill for skill, _, _, _ in budget.by_task()} == {"fresh_shorts"}

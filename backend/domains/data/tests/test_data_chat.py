"""데이터분석 AI 직접 대화 (`data_chat`).

실제 LLM은 부르지 않는다 (가짜 서버 + 실제 TimedLLMClient).

- 보내기는 저장하고 answering으로 바로, 답은 뒤에서 붙고 talking. 마지막 줄 "근거: …"는 서버가 붙임
- 답 숫자가 재료에 없으면 고정 문장. 명령은 LLM 없이 거절, 못 고르면 안내
- LLM 설정 없음 → llm_not_configured, 대화 상한 → chat_limit, 남의 대화는 요약만·쓰기 forbidden
- 재료는 저장된 결과만 읽는다 (응답 캐시·브리프 파일은 tmp에 만든 가짜, 실제 저장소 안 씀)
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_agent_template.llm import LLMSettings
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import TrendInsight, TrendInsightBriefOutput
from data_agent import data_chat as dc
from data_agent import handlers, llm_budget
from data_agent.data_chat_store import DataChatStore
from data_agent.handlers import TimedLLMClient

NOW = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)  # 한국 09:00
PART = {"name": "영상 성과", "basis": "영상 성과 · 11월 8일 14:16",
        "facts": {"3일 조회": 123251, "3일 잰 시각": "11월 8일 14:16"}}  # fmt: skip


class FakeLLM:
    def __init__(self, reply: str = "3일 조회는 123,251회예요.") -> None:
        self.reply, self.calls = reply, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        content = json.dumps({"reply": self.reply, "summary": "V5 성과를 물음"}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                          "usage": {"prompt_tokens": 900,
                                                    "completion_tokens": 60}})  # fmt: skip


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> llm_budget.Budget:
    fresh = llm_budget.Budget(None)
    monkeypatch.setattr(llm_budget, "BUDGET", fresh)
    return fresh


def _client(fake: FakeLLM) -> TimedLLMClient:
    settings = LLMSettings(model="test-model", base_url="https://llm.example.com/v1", api_key=None)
    return TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(fake))


def _service(fake: FakeLLM | None, material: dict | None = None) -> dc.DataChatService:
    mat = material if material is not None else dc.build_material([PART])
    llm = (lambda: _client(fake)) if fake is not None else (lambda: None)
    return dc.DataChatService(DataChatStore(":memory:"), material=lambda b: mat, llm=llm)


def _talk(service: dc.DataChatService, message: str, who: str = "재현") -> dict:
    async def run() -> dict:
        th = service.send(who, None, message)
        assert th["status"] == "answering"
        await service.wait_idle()
        return service.get(who, th["id"])

    return asyncio.run(run())


def test_send_answers_in_the_background_with_the_server_basis_line(
    budget: llm_budget.Budget,
) -> None:
    fake = FakeLLM()
    th = _talk(_service(fake), "V5 시험 영상 어떻게 됐어?")
    assert th["status"] == "talking" and th["summary"] == "V5 성과를 물음"
    want = "3일 조회는 123,251회예요.\n근거: 영상 성과 · 11월 8일 14:16"
    assert th["messages"][-1]["text"] == want
    assert fake.calls == 1
    assert {s for s, _, _, _ in budget.by_task()} == {"data_chat"}  # unknown 아님


def test_numbers_outside_the_material_become_the_fixed_sentence() -> None:
    th = _talk(_service(FakeLLM("3일 조회는 130,000회예요.")), "V5 성과 알려줘")
    assert th["messages"][-1]["text"] == (
        "숫자를 확인하지 못했어요. 영상 성과 화면에서 보세요.\n근거: 영상 성과 · 11월 8일 14:16")


def test_commands_are_refused_without_llm_and_unknown_questions_get_the_guide() -> None:
    fake = FakeLLM()
    service = _service(fake)
    assert _talk(service, "책추천 캠페인 시작해줘")["messages"][-1]["text"] == dc.NO_CHAT_ACTIONS
    assert _talk(service, "오늘 날씨 어때?")["messages"][-1]["text"] == dc.GUIDE
    assert fake.calls == 0


def test_nothing_stored_means_no_llm() -> None:
    fake = FakeLLM()
    th = _talk(_service(fake, material={"bundles": [], "missing": ["performance"]}), "V5 성과?")
    want = "아직 계산된 결과가 없어요. 영상 성과 화면에서 확인해 주세요."
    assert th["messages"][-1]["text"] == want
    assert fake.calls == 0


def test_no_llm_settings_and_the_chat_cap_refuse(budget: llm_budget.Budget) -> None:
    async def send(service: dc.DataChatService) -> str:
        try:
            service.send("재현", None, "V5 성과 알려줘")
        except dc.ChatRefused as refused:
            return refused.outcome
        return "sent"

    assert asyncio.run(send(_service(None))) == "llm_not_configured"
    budget.add(dc.CHAT_CALLS, 1000, skill="data_chat", task="문장")
    assert asyncio.run(send(_service(FakeLLM()))) == "chat_limit"


def test_other_peoples_chats_are_summary_only_and_not_writable() -> None:
    service = _service(FakeLLM())
    th = _talk(service, "V5 성과 알려줘", who="재현")
    other = service.get("태윤", th["id"])
    assert other["restricted"] is True and other["messages"] == [] and other["message_count"] == 2
    with pytest.raises(dc.ChatRefused) as refused:
        service.close("태윤", th["id"])
    assert refused.value.outcome == "forbidden"
    assert service.close("재현", th["id"])["status"] == "closed"


def test_daily_llm_cap_during_the_answer_says_the_limit() -> None:
    class Capped:
        async def structured(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise llm_budget.LLMDailyCapReached("상한")

    service = dc.DataChatService(
        DataChatStore(":memory:"), material=lambda b: dc.build_material([PART]), llm=Capped
    )
    assert _talk(service, "V5 성과 알려줘")["messages"][-1]["text"] == dc.LIMIT_TEXT


# ---------------------------------------------------------------- 재료 (저장된 결과만)


def _env(tmp_path: Path) -> dict[str, str]:
    from data_agent import campaign_update, fresh_shorts, response_cache, youtube_store
    from data_agent.skills import trend_insight_brief, video_performance

    return {response_cache.DB_ENV: str(tmp_path / "responses.db"),
            trend_insight_brief.DB_ENV: str(tmp_path / "brief.db"),
            video_performance.DB_ENV: str(tmp_path / "perf.db"),
            fresh_shorts.DB_ENV: str(tmp_path / "fresh.db"),
            youtube_store.DB_ENV: str(tmp_path / "yt.db"),
            campaign_update.DB_ENV: str(tmp_path / "sends.db")}  # fmt: skip


def test_detect_reads_the_last_saved_screen_default_only(tmp_path: Path) -> None:
    from data_agent.response_cache import ResponseCache

    env = _env(tmp_path)
    cache = ResponseCache(env["DATA_TREND_RESPONSES_DB"])
    other = {"category": "게임", "period": "1d", "length": "short", "sort": "views", "cells": []}
    default = {"category": None, "period": "7d", "length": "short", "sort": "views",
               "computed_at": "2026-11-08T23:30:00+00:00",
               "cells": [{"category": "게임", "videos": [
                   {"title": "피파 숏츠", "views": 120000,
                    "published_at": "2026-11-08T05:00:00+00:00"}]}]}  # fmt: skip
    cache.put("k1", ["ok", default, {}], "video_explorer")
    cache.put("k2", ["ok", other, {}], "video_explorer")  # 더 최근이지만 화면 기본 조건이 아님
    part = dc.read_detect(env, NOW)
    assert part is not None and part["basis"] == "영상 탐색·지금 인기 · 11월 9일 08:30"
    top = part["facts"]["카테고리별 7일 숏츠 조회 상위"]["게임"][0]
    assert top == {"제목": "피파 숏츠", "조회": 120000, "올린 시각": "11월 8일 14:00"}


def test_insight_reads_the_last_saved_brief_with_the_past_band(tmp_path: Path) -> None:
    from data_agent.skills.trend_insight_brief import BriefStore

    env = _env(tmp_path)
    out = TrendInsightBriefOutput(
        status="known", generated_at=NOW - timedelta(hours=1),
        insights=[TrendInsight(insight_id="i1", rank=1, topic="책추천", line="검색 3.2배")],
    )  # fmt: skip
    store = BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    store.put(NOW.date(), "r1", out, NOW - timedelta(hours=1))
    store.close()
    part = dc.read_insight(env, NOW)
    assert part is not None and part["basis"] == "인사이트 브리프 · 11월 9일 08:00"
    first = part["facts"]["인사이트"][0]
    assert first["주제"] == "책추천" and first["한 줄"] == "검색 3.2배"
    assert first["지난번 결과"] == "지난번 결과 없음 · 이 주제로 한 캠페인이 아직 없습니다"


def test_missing_files_are_not_created_and_mean_no_result(tmp_path: Path) -> None:
    env = _env(tmp_path)
    got = dc.read_material(["detect", "insight", "performance"], env, NOW)
    assert got == {"bundles": [], "missing": ["detect", "insight", "performance"]}
    assert list(tmp_path.iterdir()) == []  # 읽기 전용: 파일을 만들지 않는다


def test_collect_reads_the_ledgers(tmp_path: Path, budget: llm_budget.Budget) -> None:
    budget.add(10, 5000, skill="data_chat", task="문장")
    part = dc.read_collect(_env(tmp_path), NOW)
    assert part is not None and part["facts"]["대화 오늘 사용"] == "10/30회 · 5,000/80,000토큰"
    assert part["facts"]["수집기 마지막 실행"] == "기록 없음"


# ---------------------------------------------------------------- 핸들러 (A2A)


def _request(skill: str, payload: dict) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_test_datachat", token="t",
                       deadline=datetime.now(UTC) + timedelta(minutes=1))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input=payload)


def test_handlers_send_and_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "_CHAT", _service(FakeLLM()))

    async def run() -> tuple:
        sent = await handlers.HANDLERS["data_chat_send"](
            _request("data_chat_send", {"who": "재현", "message": "V5 성과 알려줘"}))
        await handlers._CHAT.wait_idle()
        got = await handlers.HANDLERS["data_chat_get"](
            _request("data_chat_get", {"who": "재현", "thread_id": sent.output["thread"]["id"]}))
        missing = await handlers.HANDLERS["data_chat_get"](
            _request("data_chat_get", {"who": "재현", "thread_id": "datachat-99"}))
        return sent, got, missing

    sent, got, missing = asyncio.run(run())
    assert sent.status == TaskStatus.COMPLETED and sent.output["thread"]["status"] == "answering"
    assert sent.output["schema_version"] == "data_chat.v1"
    assert got.output["thread"]["status"] == "talking"
    assert (missing.status, missing.outcome) == (TaskStatus.FAILED, "not_found")

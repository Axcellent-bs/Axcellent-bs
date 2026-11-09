"""데이터분석 AI 직접 대화 평가 정의(`eval/data_chat/`)의 약속.

- 묶음 고르기(질문 20개), 답 숫자 ⊆ facts(날짜·시각·쉼표·배수·% 통과, 바깥 숫자는 고정 문장)
- 상태 변경 명령은 LLM 없이 거절, 질문은 통과
- 저장 결과가 없으면 계산·LLM·YouTube 0회
- 재료 4,000자·지난 대화 6개, 하루 30회·80,000토큰(먼저 닿는 쪽), 80% 넘어도 허용·100%에서 멈춤,
  장부 이름 data_chat, 개인정보 묶음 없음
- LLM은 가짜 서버(httpx MockTransport)로 실제 TimedLLMClient를 거친다. 실제 키는 부르지 않는다
- 관문 켬 (평가 정의 PR #840은 관찰만: dev 모두 0.0)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "data_chat"
NOW = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"data_chat_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_rules() -> None:
    cases = make.cases()
    assert sum(c["kind"] == "pick" for c in cases) == 20
    by = {c["name"]: c for c in cases}
    assert by["pick_08"]["expected"] == ["collect"]  # "새 숏츠 추적"은 감지가 아니라 수집 상태
    assert by["pick_09"]["expected"] == ["performance"]  # "기획에 보냈어?"는 성과(보내기 상태)
    assert by["number_percent_rate"]["expected"] == "keep"
    assert by["number_outside_ratio"]["expected"] == "fixed"
    assert by["action_09"]["expected"]["refused"] is False  # 질문
    assert by["limit_day_85_percent"]["expected"] == "ok"
    assert make.cases() == cases


def test_the_scorer() -> None:
    cases = make.cases()
    perfect: dict[str, object] = {}
    fixed = {
        "empty": {"reply": make.NO_RESULT_PREFIX + " 성과 탭", "llm_calls": 0, "computed": 0},
        "material": {"chars": 3000, "basis_ok": True},
        "history": {"history": 6},
        "privacy": {"personal_bundles": 0},
    }
    for c in cases:
        want, kind = c["expected"], c["kind"]
        if kind == "action":
            perfect[c["name"]] = {"refused": want["refused"], "llm_calls": 0}
        else:
            perfect[c["name"]] = fixed.get(kind, want)
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


# ---------------------------------------------------------------- 실행 (모듈이 없으면 None = 0점)


class FakeLLM:
    def __init__(self, reply: str = "저장된 값을 보면 그래요.") -> None:
        self.reply, self.calls, self.untrusted = reply, 0, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content)
        self.untrusted.append(body["messages"][-1]["content"])
        content = json.dumps({"reply": self.reply, "summary": "요약"}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                          "usage": {"prompt_tokens": 1000,
                                                    "completion_tokens": 100}})  # fmt: skip


def _client(fake: FakeLLM):  # noqa: ANN202
    from ax_agent_template.llm import LLMSettings
    from data_agent.handlers import TimedLLMClient

    settings = LLMSettings(model="eval-model", base_url="https://llm.eval.invalid/v1", api_key=None)
    return TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(fake))


PART = {"name": "성과", "basis": "영상 성과 · 11월 9일 08:30", "facts": {"3일 조회": 123251}}


def _service(dc: ModuleType, store_mod: ModuleType, fake: FakeLLM, material=None):  # noqa: ANN001, ANN202
    store = store_mod.DataChatStore(":memory:")
    return dc.DataChatService(store, material or (lambda bundles: dc.build_material([PART])),
                              llm=lambda: _client(fake))  # fmt: skip


async def _ask(
    service, message: str, thread_id: str | None = None
) -> tuple[dict | None, str | None]:  # noqa: ANN001
    try:
        th = service.send("tester", thread_id, message)
    except Exception as error:  # noqa: BLE001 - 거절(ChatRefused)이면 outcome만 본다
        return None, getattr(error, "outcome", type(error).__name__)
    await service.wait_idle()
    return service.get("tester", th["id"]), None


def _empty_env(tmp: Path) -> dict[str, str]:
    from data_agent import campaign_update, fresh_shorts, response_cache, youtube_store
    from data_agent.skills import shorts_stats, trend_insight_brief, video_performance

    return {
        response_cache.DB_ENV: str(tmp / "responses.db"),
        trend_insight_brief.DB_ENV: str(tmp / "brief.db"),
        video_performance.DB_ENV: str(tmp / "perf.db"),
        fresh_shorts.DB_ENV: str(tmp / "fresh.db"),
        youtube_store.DB_ENV: str(tmp / "yt.db"),
        campaign_update.DB_ENV: str(tmp / "sends.db"),
        shorts_stats.SHORTS_DB_ENV: str(tmp / "shorts.db"),
    }


def _run(case: dict, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> object | None:
    try:
        from data_agent import data_chat as dc
        from data_agent import data_chat_store as store_mod
    except ImportError:
        return None
    from data_agent import handlers, llm_budget

    budget = llm_budget.Budget(None)
    monkeypatch.setattr(llm_budget, "BUDGET", budget)
    kind = case["kind"]
    if kind == "pick":
        return sorted(dc.pick_bundles(case["question"]))
    if kind == "number":
        got = dc.verified_reply(case["reply"], case["facts"], ["performance"])
        return (
            "keep"
            if got == case["reply"]
            else ("fixed" if got.startswith(make.NUMBER_FIXED_PREFIX) else "other")
        )
    if kind == "action":
        fake = FakeLLM()
        service = _service(dc, store_mod, fake)
        th, refused = asyncio.run(_ask(service, case["message"]))
        reply = th["messages"][-1]["text"] if th else ""
        return {"refused": reply == make.REFUSAL, "llm_calls": fake.calls}
    if kind == "empty":
        computed = {"n": 0}

        def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
            computed["n"] += 1
            raise AssertionError("대화 중 계산·YouTube 호출")

        for name in (
            "get_youtube",
            "video_explorer",
            "trend_popular",
            "trend_insight_brief",
            "video_performance",
            "campaign_result",
            "shorts_stats",
            "trend_scan",
        ):
            if hasattr(handlers, name):
                monkeypatch.setattr(handlers, name, boom)
        from data_agent.connectors import youtube

        monkeypatch.setattr(youtube.YouTubeConnector, "__init__", boom)
        env = _empty_env(tmp)
        fake = FakeLLM()
        def stored(bundles: list[str]) -> dict:
            return dc.read_material(bundles, env, NOW)

        service = _service(dc, store_mod, fake, material=stored)
        th, refused = asyncio.run(_ask(service, case["question"]))
        reply = th["messages"][-1]["text"] if th else str(refused)
        return {"reply": reply, "llm_calls": fake.calls, "computed": computed["n"]}
    if kind == "material":
        big = {"name": "감지", "basis": "영상 탐색 · 11월 9일 08:00",
               "facts": {"videos": [{"title": f"영상 {i} " + "가" * 40, "views": i * 1000}
                                    for i in range(300)]}}  # fmt: skip
        out = dc.build_material([big, PART])
        bundles = out.get("bundles", [])
        return {"chars": len(json.dumps(out, ensure_ascii=False)),
                "basis_ok": bool(bundles) and all(b.get("basis") for b in bundles)}  # fmt: skip
    if kind == "history":
        fake = FakeLLM()
        service = _service(dc, store_mod, fake)

        async def talk() -> None:
            th, _ = await _ask(service, "성과 알려줘")
            for i in range(10):
                await _ask(service, f"성과 {i} 알려줘", th["id"] if th else None)

        asyncio.run(talk())
        from ax_agent_template.llm import UNTRUSTED_CLOSE, UNTRUSTED_OPEN

        text = fake.untrusted[-1] if fake.untrusted else ""
        block = text.split(UNTRUSTED_OPEN, 1)[-1].rsplit(UNTRUSTED_CLOSE, 1)[0]
        last = json.loads(block) if UNTRUSTED_OPEN in text else {}
        convo = _find(last, "conversation")
        return {"history": len(convo) if isinstance(convo, list) else 10**9}
    if kind == "limit":
        (cc, ct), (oc, ot) = case["chat"], case["other"]
        if cc or ct:
            budget.add(cc, ct, skill="data_chat", task="문장(대화)")
        if oc or ot:
            budget.add(oc, ot, skill="trend_popular", task="분류(주제·영상)")
        fake = FakeLLM()
        service = _service(dc, store_mod, fake)
        th, refused = asyncio.run(_ask(service, "성과 알려줘"))
        if refused:
            return refused
        last = th["messages"][-1] if th else {}
        return "ok" if last.get("kind") == "text" and fake.calls == 1 else "answer_failed"
    if kind == "ledger":
        fake = FakeLLM()
        asyncio.run(_ask(_service(dc, store_mod, fake), "성과 알려줘"))
        return sorted({skill for skill, _, _, _ in budget.by_task()})
    if kind == "privacy":
        picks = dc.pick_bundles(case["question"]) + list(dc.BUNDLES)
        bad = [b for b in picks if any(p in b.lower() for p in make.PERSONAL)]
        return {"personal_bundles": len(bad)}
    return None


def _find(node: object, key: str) -> object:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            if (got := _find(v, key)) is not None:
                return got
    if isinstance(node, list):
        for v in node:
            if (got := _find(v, key)) is not None:
                return got
    return None


def observe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, float], dict]:
    cases = make.cases()
    got = {}
    for c in cases:
        with monkeypatch.context() as m:
            got[c["name"]] = _run(c, tmp_path / c["name"], m)
    return score.score(cases, got), got


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (평가 정의 PR #840 dev: 모두 0.0)


def test_data_chat_passes_the_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metrics, got = observe(tmp_path, monkeypatch)
    print("data_chat eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
    wrong = {k: v for k, v in got.items() if not score._ok(
        next(c for c in make.cases() if c["name"] == k), v)}  # fmt: skip
    for name, gate in GATES.items():
        assert metrics[name] >= gate, (name, metrics[name], wrong)

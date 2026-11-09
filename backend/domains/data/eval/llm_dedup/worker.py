"""LLM 중복 막기 평가용 프로세스 하나 (서버 또는 수집기 역할). 실제 LLM은 부르지 않는다.

    python worker.py <spec.json>

환경변수는 부모가 준다: `DATA_LLM_BUDGET_DB`(장부·"계산 중" 표시를 같이 쓰는 파일),
`DATA_LLM_INFLIGHT_SECONDS`(표시 만료). 가짜 LLM 서버는 받은 요청마다 `calls_file`에 한 줄을 남기고
`latency`초 뒤 답한다(`fail_first`면 그 파일의 첫 요청에 500).

spec:
- mode "ask"(기본): `requests`를 동시에 보낸다 (각자 `delay`초 뒤).
  `start_at`(epoch초)이 있으면 그때까지 기다렸다 시작
- mode "crash": 첫 요청의 "계산 중" 표시만 남기고 바로 죽는다 (`llm_inflight`가 없으면 unsupported)
- mode "fresh": 수집기의 새 숏츠 형식 분류(`fresh_shorts._llm_classify`)를 단계 기록 없이 부른다
출력(마지막 줄 JSON): answers(값 또는 "error:<예외>"), waits(초), skills(장부 skill 이름)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from pydantic import BaseModel

ANSWER = 7
MODEL = "eval-model"


class Answer(BaseModel):
    x: int


def _transport(spec: dict) -> httpx.MockTransport:
    calls = Path(spec["calls_file"])
    latency = float(spec.get("latency") or 0.0)

    async def handle(request: httpx.Request) -> httpx.Response:
        fd = os.open(calls, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            os.write(fd, b"call\n")
        finally:
            os.close(fd)
        first = calls.read_text().count("call") == 1
        await asyncio.sleep(latency)
        if spec.get("fail_first") and first:
            return httpx.Response(500, json={"error": {"message": "eval"}})
        content = json.dumps({"items": []} if spec.get("mode") == "fresh" else {"x": ANSWER})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                          "usage": {"prompt_tokens": 100,
                                                    "completion_tokens": 10}})  # fmt: skip

    return httpx.MockTransport(handle)


def _client(spec: dict):  # noqa: ANN202
    from ax_agent_template.llm import LLMSettings
    from data_agent.handlers import TimedLLMClient

    settings = LLMSettings(model=MODEL, base_url="https://llm.eval.invalid/v1", api_key=None)
    return TimedLLMClient("data", settings=settings, transport=_transport(spec))


async def _ask(spec: dict) -> dict:
    from data_agent.stage_timing import recording

    client = _client(spec)
    skill = spec.get("skill") or "trend_popular"

    async def one(req: dict) -> tuple[object, float]:
        await asyncio.sleep(float(req.get("delay") or 0.0))
        t = time.monotonic()
        with recording(skill, emit=lambda line: None):
            try:
                got: object = (await client.structured(req["prompt"], Answer)).x
            except Exception as error:  # noqa: BLE001 - 평가는 예외 이름만 본다
                got = f"error:{type(error).__name__}"
        return got, time.monotonic() - t

    if spec.get("start_at"):
        await asyncio.sleep(max(0.0, float(spec["start_at"]) - time.time()))
    done = await asyncio.gather(*(one(r) for r in spec["requests"]))
    return {"answers": [a for a, _ in done], "waits": [round(w, 3) for _, w in done]}


def _crash(spec: dict) -> dict | None:
    try:
        from data_agent import llm_inflight
    except ImportError:
        return {"unsupported": True}
    key = llm_inflight.request_key(MODEL, Answer, spec["requests"][0]["prompt"], None)
    llm_inflight.INFLIGHT.claim(key)
    print(json.dumps({"claimed": True}), flush=True)
    os._exit(0)  # 표시를 지우지 않고 죽는다


async def _fresh(spec: dict) -> dict:
    from data_agent import fresh_shorts, handlers
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV

    client = _client(spec)
    handlers.get_llm = lambda: client  # 수집기 프로세스의 get_llm 자리에 가짜 서버 클라이언트
    stats = [SimpleNamespace(video_id=f"v{i}", title=f"영상 {i}", channel_title="채널",
                             view_count=100, tags=[]) for i in range(3)]  # fmt: skip
    await fresh_shorts._llm_classify(stats, {SHORTS_DB_ENV: spec["shorts_db"]})
    return {}


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    mode = spec.get("mode") or "ask"
    if mode == "crash":
        out = _crash(spec)
    elif mode == "fresh":
        out = asyncio.run(_fresh(spec))
    else:
        out = asyncio.run(_ask(spec))
    if out is not None and not out.get("unsupported"):
        from data_agent import llm_budget

        out["skills"] = sorted({skill for skill, _, _, _ in llm_budget.BUDGET.by_task()})
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

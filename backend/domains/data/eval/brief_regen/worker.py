"""다시 추천 평가용 프로세스 하나 (서버 또는 수집기 역할). LLM은 부르지 않는다(가짜 만들기).

    python worker.py <spec.json>

부모가 `DATA_INSIGHT_BRIEF_DB`(브리프·판·잠금 파일)를 준다. `start_at`(epoch초)까지 기다렸다가
다시 추천을 시작하고, 만들기는 1초 걸린 뒤 판 하나를 저장한다. 출력(마지막 줄 JSON): status.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


async def main(spec: dict) -> dict:
    try:
        from data_agent import brief_versions as bv
    except ImportError:
        return {"unsupported": True}
    from ax_contracts.schemas.data import TrendInsight, TrendInsightBriefOutput

    async def build(ctx) -> None:  # noqa: ANN001
        await asyncio.sleep(1.0)
        out = TrendInsightBriefOutput(
            status="known", generated_at=datetime.now(UTC),
            insights=[TrendInsight(insight_id="i-x", rank=1, topic=spec["name"], line="x")],
        )  # fmt: skip
        ctx.save(out, tokens=1000)

    await asyncio.sleep(max(0.0, float(spec["start_at"]) - time.time()))
    detect = bv.Detect(as_of=datetime.now(UTC), key=f"k-{spec['name']}")
    got = bv.start({**spec["env"]}, spec["name"], build, llm_share=0.1, detect=detect)
    await bv.wait_all()
    return {"status": got["status"], "blocked": (got.get("blocked") or {}).get("code")}


if __name__ == "__main__":
    spec = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    print(json.dumps(asyncio.run(main(spec)), ensure_ascii=False), flush=True)

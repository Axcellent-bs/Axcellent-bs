"""레포 고르기 정확도(dev_agent.repo_routing) 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 4순위).

무엇을 재나
- "정답 레포를 고른 비율". `repo_routing.py`(PR을 어느 연결에 올릴지 고르는 production 모듈,
  규칙 점수가 기준선이고 LLM이 있으면 LLM 판단을 쓴다)를 그대로 돌린다. 평가 데이터(연결 2개
  이상 + 정답 표시)가 없어 새로 만들었다(`repo_routing_cases.json`, 8건).
- 비고로 "규칙 점수만 쓸 때와 차이"를 같이 낸다 — `score_rules`(규칙만)와 `decide`(규칙 +
  LLM, LLM이 틀린 id를 고르거나 실패하면 규칙으로 돌아가는 production 그대로의 길)를 같은
  케이스에 돌려 정확도를 나란히 비교한다.

어떻게 재나
- 모든 호출은 production 함수(`score_rules`·`decide`) 그대로다. 새로 만든 것은 합성 케이스뿐.
- 작업당 LLM은 공유해도 되지만(연결·세션 상태를 쌓지 않는 순수 함수라 서비스를 새로 띄울
  필요가 없다), 매 케이스 `repo_routing.set_llm`으로 같은 클라이언트를 끼운다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(HERE))


def load_env() -> dict[str, str]:
    """backend/.env를 읽어 os.environ에 없는 값만 보탠다 (DEV_LLM_* 모델 설정)."""
    env = dict(os.environ)
    path = BACKEND / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #")[0].strip().strip('"')
            if value:
                env[key.strip()] = value
    return env


os.environ.update(load_env())

from dev_agent import repo_routing  # noqa: E402
from dev_agent.repo_routing import Candidate, RouteInput  # noqa: E402


def _candidates(raw: list[dict]) -> list[Candidate]:
    return [
        Candidate(
            connection_id=c["connection_id"],
            repo=c["repo"],
            description=c.get("description", ""),
            paths=tuple(c.get("paths", ())),
        )
        for c in raw
    ]


async def run_case(llm: Any, case: dict) -> dict:
    inp = RouteInput(
        title=case["input"].get("title", ""),
        text=case["input"].get("text", ""),
        changed_paths=tuple(case["input"].get("changed_paths", ())),
        request_title=case["input"].get("request_title", ""),
        area=case["input"].get("area", ""),
    )
    cands = _candidates(case["candidates"])
    correct = case["correct_connection_id"]

    rules = repo_routing.score_rules(inp, cands)
    repo_routing.set_llm(llm)
    try:
        judged = await repo_routing.decide(inp, cands)
    finally:
        repo_routing.reset_llm()

    return {
        "id": case["id"],
        "rules_connection_id": rules.connection_id,
        "rules_correct": rules.connection_id == correct,
        "judged_connection_id": judged.connection_id,
        "judged_correct": judged.connection_id == correct,
        "judged_by": judged.by,
        "confidence": judged.confidence,
    }


async def run_all(cases: list[dict]) -> list[dict]:
    from ax_agent_template.llm import LLMClient
    from dev_agent import llm_env, llm_key

    settings = llm_key.apply(llm_env.settings())
    if not settings.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, DEV_LLM_MODEL 또는 LLM_MODEL)")
    llm = LLMClient(llm_env.DEPT, settings=settings, timeout=60.0)
    return [await run_case(llm, case) for case in cases]


def score(rows: list[dict]) -> dict:
    n = len(rows)
    rules_correct = sum(1 for r in rows if r["rules_correct"])
    judged_correct = sum(1 for r in rows if r["judged_correct"])
    used_llm = sum(1 for r in rows if r["judged_by"] == "llm")
    return {
        "n": n,
        "repo_pick_accuracy": judged_correct / n if n else None,  # 정답 레포를 고른 비율 (최종)
        "rule_only_accuracy": rules_correct / n if n else None,  # 비고: 규칙 점수만 쓸 때
        "accuracy_delta": (judged_correct - rules_correct) / n if n else None,
        "llm_used_rate": used_llm / n if n else None,  # LLM 판단이 실제로 채택된 비율
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "repo_routing_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "repo_routing_cases.json").read_text(encoding="utf-8"))["cases"]
    rows = asyncio.run(run_all(cases))
    result = {
        "model": os.environ.get("DEV_LLM_MODEL") or os.environ.get("LLM_MODEL"),
        "rows": rows,
        "metrics": score(rows),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()

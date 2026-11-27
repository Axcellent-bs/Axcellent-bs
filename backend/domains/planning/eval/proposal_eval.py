"""제안 초안(proposals.write) LLM 모델 비교 평가 (예진 제안, #소통 2026-11-27 15:46, 4순위).

인사이트 한 건(제목·요약·근거 facts)마다 실제 planning_agent.proposals.write()를 그대로 부른다.
write()는 production 파이프라인 전체다: LLM(`ProposalWriter.draft`) → 실패·시간 초과면 규칙
초안(`rule_draft`) → 두 경로 모두 숫자 검사(`number_warnings`, 근거·목표에 없는 숫자를 경고).
이 평가는 그 결과(written_by·warnings)만 읽어 지표로 바꾼다. 검사 로직은 하나도 새로 만들지
않았다 — 기존 holdout.py(domains/planning/eval/holdout.py)는 LLM을 꺼서 규칙 경로만 쟀다
("규칙 경로만 holdout 1400/1400, LLM 미측정"). 이 평가가 LLM 경로를 처음 잰다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(HERE))


def load_env() -> dict[str, str]:
    """backend/.env를 읽어 os.environ에 없는 값만 보탠다 (domains끼리 import하지 않는다 규칙이라
    domains/archive/eval/run_eval.py의 load_env와 같은 내용을 여기도 둔다)."""
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

from ax_agent_template.llm import LLMClient, LLMSettings  # noqa: E402
from ax_contracts.schemas.planning import InsightFact, ReceiveInsightInput  # noqa: E402
from planning_agent.proposals import ProposalWriter, write  # noqa: E402


def _input(case: dict) -> ReceiveInsightInput:
    return ReceiveInsightInput(
        insight_id=case["insight_id"],
        title=case["title"],
        summary=case["summary"],
        skill=case["skill"],
        facts=[InsightFact(**f) for f in case["facts"]],
        products=case.get("products", []),
        audience=case.get("audience"),
        shorts_format=case.get("shorts_format"),
    )


async def run_case(writer: ProposalWriter, case: dict) -> dict:
    inp = _input(case)
    draft = await write(writer, inp, f"ctx_req_{case['insight_id']}")
    return {
        "id": case["insight_id"],
        "written_by": draft.written_by,
        "warnings": draft.warnings,
        "n_warnings": len(draft.warnings),
    }


async def run_all(cases: list[dict]) -> list[dict]:
    settings = LLMSettings.from_env("planning")
    if not settings.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, PLANNING_LLM_MODEL 또는 LLM_MODEL)")
    writer = ProposalWriter(LLMClient("planning", settings))
    return [await run_case(writer, case) for case in cases]


def score(rows: list[dict]) -> dict:
    n = len(rows)
    llm_rows = [r for r in rows if r["written_by"] == "llm"]
    return {
        "n": n,
        "format_pass_rate": len(llm_rows) / n if n else None,  # written_by == "llm" 비율
        "llm_zero_warning_rate": (
            sum(1 for r in llm_rows if r["n_warnings"] == 0) / len(llm_rows) if llm_rows else None
        ),
        "zero_warning_rate": sum(1 for r in rows if r["n_warnings"] == 0) / n if n else None,
        "total_warnings": sum(r["n_warnings"] for r in rows),
        "avg_warnings": sum(r["n_warnings"] for r in rows) / n if n else None,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "proposal_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "proposal_cases.json").read_text(encoding="utf-8"))["cases"]
    rows = asyncio.run(run_all(cases))
    result = {
        "model": os.environ.get("PLANNING_LLM_MODEL") or os.environ.get("LLM_MODEL"),
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

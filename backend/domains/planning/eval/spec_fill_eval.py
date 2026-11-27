"""자유 작성 → 필수 칸 채우기(spec_fill) LLM 모델 비교 평가 (예진 제안, #소통 2026-11-27
15:46, 5순위).

기획서 자유 작성(body_md) 한 건마다 실제 planning_agent.spec_fill.fill()을 그대로 부른다.
fill()은 production 파이프라인 전체다: LLM(`SpecFiller.read`)이 먼저 읽고, 비어 둔 칸만
소제목 읽기(`parse`, 규칙)로 보탠다. 이 평가는 그 결과(Filled)만 읽어 지표로 바꾼다.

- 칸 채움률·남은 질문 수는 fill()이 돌려준 Filled를 그대로 센 것이다(새 판정 없음).
- "지어낸 값 0"은 production에 아직 강제 검사가 없어(프롬프트 지시만 있음) 이 평가 전용으로
  두 가지를 본다(투명하게 적는다, production 코드 아님):
  1. invented_number: 채운 칸에 나온 숫자가 자유 작성 어디에도 없으면 센다
  2. hallucinated_answer: 골든셋에 "자유 작성에 답이 없다"고 표시한 질문인데 answer를 채우면
     센다(실제로 없는 답을 지어낸 것)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
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
from planning_agent import spec_template  # noqa: E402
from planning_agent.spec_fill import Filled, SpecFiller, fill  # noqa: E402

TEMPLATE = "plan"
APPLICABLE_EXTRA = tuple(
    s.key for s in spec_template.sections(TEMPLATE) if s.source == "extra"
)  # plan 양식에 실제 있는 extra 칸만 (scope·risks)
NUMBER = re.compile(r"\d{2,}")


def _numbers(text: str) -> set[str]:
    return set(NUMBER.findall(text or ""))


def filled_fields(got: Filled) -> dict[str, bool]:
    """plan 양식 기준 칸마다 채워졌는지 (새 판정 아님 - Filled 값을 그대로 bool로만 본다)."""
    out = {
        "problem": bool(got.problem),
        "goal": bool(got.goal),
        "requirements": bool(got.reqs),
        "done_criteria": bool(got.done),
    }
    for key in APPLICABLE_EXTRA:
        out[key] = bool(got.extra.get(key))
    return out


def invented_numbers(got: Filled, md: str) -> list[str]:
    texts = [got.problem, got.goal, *got.reqs, *got.done, *got.extra.values(), got.answer]
    joined = " ".join(texts)
    return sorted(_numbers(joined) - _numbers(md))


async def run_case(filler: SpecFiller, case: dict) -> dict:
    got = await fill(
        case["md"], title=case["title"], current={}, question=case.get("question", ""),
        context_id=f"ctx_req_{case['id']}", filler=filler, template=TEMPLATE,
    )  # fmt: skip
    fields = filled_fields(got)
    row = {
        "id": case["id"],
        "by": got.by,
        "fields": fields,
        "n_filled": sum(fields.values()),
        "n_applicable": len(fields),
        "invented_numbers": invented_numbers(got, case["md"]),
        "has_question": bool(case.get("question")),
        "expect_answer": case.get("expect_answer"),
        "answer_filled": bool(got.answer),
    }
    row["hallucinated_answer"] = bool(
        row["has_question"] and row["expect_answer"] is False and row["answer_filled"]
    )
    row["missed_answer"] = bool(
        row["has_question"] and row["expect_answer"] is True and not row["answer_filled"]
    )
    return row


async def run_all(cases: list[dict]) -> list[dict]:
    settings = LLMSettings.from_env("planning")
    if not settings.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, PLANNING_LLM_MODEL 또는 LLM_MODEL)")
    filler = SpecFiller(LLMClient("planning", settings))
    return [await run_case(filler, case) for case in cases]


def score(rows: list[dict]) -> dict:
    n = len(rows)
    total_filled = sum(r["n_filled"] for r in rows)
    total_applicable = sum(r["n_applicable"] for r in rows)
    with_question = [r for r in rows if r["has_question"]]
    answerable = [r for r in with_question if r["expect_answer"] is True]
    return {
        "n": n,
        "field_fill_rate": total_filled / total_applicable if total_applicable else None,
        "questions_total": len(with_question),
        "remaining_question_count": sum(1 for r in with_question if not r["answer_filled"]),
        "answerable_answered_rate": (
            sum(1 for r in answerable if r["answer_filled"]) / len(answerable)
            if answerable
            else None
        ),
        "hallucinated_answer_count": sum(1 for r in rows if r["hallucinated_answer"]),
        "invented_number_hits": sum(1 for r in rows if r["invented_numbers"]),
        "llm_rate": sum(1 for r in rows if r["by"] in ("llm", "llm+rule")) / n if n else None,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "spec_fill_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "spec_fill_cases.json").read_text(encoding="utf-8"))["cases"]
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

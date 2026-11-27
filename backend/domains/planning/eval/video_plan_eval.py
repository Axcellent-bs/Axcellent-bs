"""영상 제작안(`video_plan.write`) LLM 모델 비교.

예진 제안(#소통 2026-11-27 15:46) 2순위. `video_plan_cases.json`(합성 제안 10건)마다 실제
`video_plan.write(llm, p, context_id)`를 실제 LLM으로 돌린다. 채점 로직은 새로 만들지 않고,
`video_plan.py` 자신이 이미 계산해 `VideoPlan`에 남기는 값(`written_by`·`check_log`·
`fallback_reason`·`fallback_detail`)을 그대로 읽는다 — 금칙어·지어낸 숫자·실명 검사
(`reject_reason`·`text_problems`·`invented_number`·`has_real_name`)는 이 스크립트가 다시
구현하지 않고 `write()` 안에서 이미 돈 결과만 센다.

실행: `cd backend && uv run python domains/planning/eval/video_plan_eval.py [--out 경로]`

- 모델은 `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`(`LLMSettings.from_env`와 동일)을 쓴다.
- 케이스마다 제작안 LLM 호출(최대 2회, 다시 쓰기 포함) + 장면 콘티 SVG 호출 1회가 든다.
  10건이면 모델 하나당 최대 30회 호출.
- `results/` 파일을 덮어쓰니 모델 비교는 `model_compare_video_plan.py`로.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(HERE))


def load_env() -> dict[str, str]:
    """프로세스 환경에 backend/.env 의 값을 얹는다. 파일 안의 값은 출력하지 않는다.

    `domains/archive/eval/run_eval.py`의 같은 이름 함수와 내용이 같다(둘 다 독립된 작은
    평가용 헬퍼라, 부서끼리 import하지 않는다는 규칙(CLAUDE.md)을 지키려고 복사해 둔다).
    """
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
from ax_workflow.models import Proposal  # noqa: E402
from planning_agent import video_plan as vp  # noqa: E402

# 금칙어·지어낸 숫자·실명 검사가 걸리면 write()가 남기는 fallback_detail 코드
WATCHED_DETAILS = ("marketing_word", "invented_offer_number", "real_name_in_scene")


def _proposal(case: dict) -> Proposal:
    return Proposal.model_validate(
        {
            "id": case["id"],
            "title": case["title"],
            "from": "data",
            "skill": case["skill"],
            "insight": case["insight"],
            "idea": case["idea"],
            "why": case["why"],
            "products": case["products"],
            "format": case["format"],
            "content_category": case["content_category"],
            "angle": case["angle"],
            "facts": case.get("facts", []),
            "created_at": datetime(2026, 11, 27, tzinfo=UTC),
        }
    )


async def run_case(llm: LLMClient, case: dict) -> dict:
    p = _proposal(case)
    plan = await vp.write(llm, p, context_id=f"ctx_req_{case['id']}")
    attempts = plan.check_log
    first_pass = attempts[0].passed if attempts else None
    second_pass = attempts[1].passed if len(attempts) >= 2 else None
    return {
        "id": case["id"],
        "written_by": plan.written_by,
        # "" · no_llm · timeout · error · bad_format · rejected
        "fallback_reason": plan.fallback_reason,
        # rejected일 때 자세한 코드 (marketing_word 등)
        "fallback_detail": plan.fallback_detail,
        "first_pass": first_pass,
        "rewrote": len(attempts) >= 2,
        "second_pass": second_pass,
    }


async def run_all(cases: list[dict]) -> list[dict]:
    hs = LLMSettings.from_env("planning")
    if not hs.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, PLANNING_LLM_MODEL 또는 LLM_MODEL)")
    llm = LLMClient("planning", hs)
    rows = []
    for case in cases:
        rows.append(await run_case(llm, case))
    return rows


def score(rows: list[dict]) -> dict:
    n = len(rows)
    llm_written = sum(1 for r in rows if r["written_by"] == "llm")
    first_pass = sum(1 for r in rows if r["first_pass"] is True)
    rewrote = sum(1 for r in rows if r["rewrote"])
    second_pass = sum(1 for r in rows if r["second_pass"] is True)
    watched_hits = {
        d: sum(1 for r in rows if r["fallback_detail"] == d or r["fallback_detail"].startswith(d))
        for d in WATCHED_DETAILS
    }
    return {
        "n": n,
        "llm_plan_rate": llm_written / n if n else None,  # 최종적으로 LLM 제작안을 쓴 비율
        "first_draft_pass_rate": first_pass / n if n else None,  # 1판에서 바로 통과한 비율
        "rewrite_rate": rewrote / n if n else None,  # 2판(다시 쓰기)까지 간 비율
        "second_draft_pass_rate": second_pass / rewrote if rewrote else None,
        "rule_fallback_rate": 1 - (llm_written / n) if n else None,
        "watched_check_hits": watched_hits,  # 금칙어·지어낸 숫자·실명 검사가 실제로 걸린 횟수
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "video_plan_eval.json"))
    args = parser.parse_args()

    cases = json.loads((HERE / "video_plan_cases.json").read_text(encoding="utf-8"))["cases"]
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

"""요청 관계 판단(same·conflict·update·related) LLM 평가.

예진 제안(#소통 2026-11-27 15:46) 1순위. `related_cases.json`(합성 골든셋, 28건)의
각 케이스를 실제 `ax_workflow.related.Relater._judge`로 판단시켜(진짜 LLM 호출,
JUDGE_PROMPT·스키마 그대로 재사용), 사람이 표시한 정답과 비교한다. 채점 로직은 새로
만들지 않고 이 스크립트 안에서만 계산한다(비교 대상이 될 "정답 규칙"이 없으므로).

실행: `cd backend && uv run python domains/planning/eval/related_eval.py [--out 경로]`

- 모델은 `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`(backend/.env, `LLMSettings.from_env`
  와 동일한 규칙)을 그대로 쓴다. 모델을 바꿔 비교하려면 환경변수만 바꿔 다시 실행한다
  (`model_compare_related.py` 참고).
- 케이스마다 `new` 요청 1개 + 후보(candidate) 1개를 실제 `Request`/`State` 객체로 만들어
  `Relater(llm=실제 LLMClient)._judge()`를 호출한다. 검색(임베딩) 단계는 건너뛴다 — 이
  평가는 "후보가 주어졌을 때 관계를 얼마나 맞히는가"만 잰다(검색 자체는 다른 비고 항목).
- `results/related_eval.json`에 케이스별 원본 결과 + 요약 지표를 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(HERE))  # ax_workflow 등은 uv sync로 이미 깔려 있다(같은 workspace)


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
from ax_workflow.models import AutoPolicy, Request, State  # noqa: E402
from ax_workflow.related import Relater  # noqa: E402

RELATIONS = ("same", "conflict", "update", "related")  # 4분류. none(관계없음)은 분류 대상 아님


def _state() -> State:
    return State(auto_policy=AutoPolicy(level=0, paths=""))


def _request(case_id: str, which: str, title: str, evidence: str, req_state: str) -> Request:
    return Request(
        id=f"REQ-{case_id}-{which}",
        context_id=f"ctx_req_{case_id}",
        from_="support",
        via="eval",
        title=title,
        evidence=evidence,
        state=req_state,
    )


async def run_case(relater: Relater, case: dict) -> dict:
    state = _state()
    new = _request(case["id"], "new", case["new"]["title"], case["new"]["evidence"], "접수")
    cand = _request(
        case["id"],
        "cand",
        case["candidate"]["title"],
        case["candidate"]["evidence"],
        case["candidate"]["state"],
    )
    # 검색(임베딩)은 건너뛰고 판단만 잰다
    judged = await relater._judge(state, new, [(cand, 0.8)])  # noqa: SLF001
    got = judged.get(cand.id) if judged else None
    # LLM이 실패하면 _judge가 None을 주고, 여기선 none(관계없음)으로 취급한다
    predicted = got.relation if got else "none"
    return {
        "id": case["id"],
        "expect": case["relation"],
        "predicted": predicted,
        "reason": got.reason if got else "",
        "llm_failed": judged is None,
    }


async def run_all(cases: list[dict]) -> list[dict]:
    hs = LLMSettings.from_env("planning")
    if not hs.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, PLANNING_LLM_MODEL 또는 LLM_MODEL)")
    llm = LLMClient("planning", hs)
    relater = Relater(llm=llm)
    rows = []
    for case in cases:
        rows.append(await run_case(relater, case))
    return rows


def score(rows: list[dict]) -> dict:
    same_tp = sum(1 for r in rows if r["expect"] == "same" and r["predicted"] == "same")
    same_fp = sum(1 for r in rows if r["expect"] != "same" and r["predicted"] == "same")
    same_fn = sum(1 for r in rows if r["expect"] == "same" and r["predicted"] != "same")
    precision = same_tp / (same_tp + same_fp) if (same_tp + same_fp) else None
    recall = same_tp / (same_tp + same_fn) if (same_tp + same_fn) else None

    four = [r for r in rows if r["expect"] in RELATIONS]
    four_acc = sum(1 for r in four if r["predicted"] == r["expect"]) / len(four) if four else None

    none_rows = [r for r in rows if r["expect"] == "none"]
    none_acc = (
        sum(1 for r in none_rows if r["predicted"] == "none") / len(none_rows)
        if none_rows
        else None
    )
    confusion = Counter((r["expect"], r["predicted"]) for r in rows)
    return {
        "same": {
            "precision": precision,
            "recall": recall,
            "tp": same_tp,
            "fp": same_fp,
            "fn": same_fn,
        },
        "four_class_accuracy": four_acc,
        "none_accuracy": none_acc,
        "llm_failures": sum(1 for r in rows if r["llm_failed"]),
        "confusion": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "related_eval.json"))
    args = parser.parse_args()

    cases = json.loads((HERE / "related_cases.json").read_text(encoding="utf-8"))["cases"]
    rows = asyncio.run(run_all(cases))
    result = {"model": os.environ.get("PLANNING_LLM_MODEL") or os.environ.get("LLM_MODEL"),
              "rows": rows, "metrics": score(rows)}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()

"""`collect_similar_decision_embed.py`가 만든
`results/similar_decision_embed_sims/sims_<라벨>.json`을 채점하고, 글자 2-그램 기준선
(`similar_decision_bigram_score.py`)과 한 표로 비교한다.

임계값을 고르지 않는다 — 이 기능은 "가장 비슷한 것을 보여주는" 검색이라, 분류처럼 막을지
말지를 정하는 임계값이 없다. top-1(가장 비슷하다고 고른 것이 정답과 같은가)·top-3(정답이
상위 3개 안에 있는가) 정확도만 본다.

사용법:
    cd backend
    uv run python shared/safety/eval/score_similar_decision_embed.py gemini-embedding-001 ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from similar_decision_bigram_score import evaluate as bigram_evaluate  # noqa: E402


def evaluate(cases: dict, sims: dict[str, dict[str, float]], split: str) -> dict[str, float | None]:
    queries = [q for q in cases["queries"] if q["split"] == split]
    if not queries:
        return {"n": 0, "top1": None, "top3": None}
    top1_hit = top3_hit = 0
    for q in queries:
        row = sims.get(q["id"])
        if not row:
            continue
        ranked = sorted(row.items(), key=lambda kv: kv[1], reverse=True)
        ids = [i for i, _ in ranked]
        if ids and ids[0] == q["expected"]:
            top1_hit += 1
        if q["expected"] in ids[:3]:
            top3_hit += 1
    n = len(queries)
    return {"n": n, "top1": top1_hit / n, "top3": top3_hit / n}


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    labels = argv[1:]
    if not labels:
        print(
            "사용법: score_similar_decision_embed.py <라벨1> <라벨2> ...  "
            "(collect_similar_decision_embed.py와 같은 라벨)"
        )
        return 2

    cases = json.loads((HERE / "similar_decision_cases.json").read_text(encoding="utf-8"))

    print("| 방식 | 집합 | 표본 | top-1 정확도 | top-3 정확도 |")
    print("| --- | --- | --- | --- | --- |")
    for split in ("dev", "holdout"):
        bm = bigram_evaluate(cases, split)
        print(
            f"| 글자 2-그램 (기준선) | {split} | {bm['n']}"
            f" | {_fmt(bm['top1'])} | {_fmt(bm['top3'])} |"
        )

    for label in labels:
        path = HERE / "results" / "similar_decision_embed_sims" / f"sims_{label}.json"
        if not path.exists():
            note = f"(결과 없음: 먼저 collect_similar_decision_embed.py {label} 실행)"
            print(f"| 임베딩 {label} | - | {note} | | |")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for split in ("dev", "holdout"):
            m = evaluate(cases, data["sims"], split)
            print(
                f"| 임베딩 {label} ({data['model']}) | {split} | {m['n']}"
                f" | {_fmt(m['top1'])} | {_fmt(m['top3'])} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

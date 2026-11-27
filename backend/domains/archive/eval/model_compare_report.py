"""카드 검색(9번) 임베딩 모델 비교 — 채점/표.

사용: python model_compare_report.py <라벨1> [라벨2 ...]

`model_compare_search.py`로 모은 `results/model_compare/search_<라벨>.json`
파일들을 모아, 병환 님 `embed-compare`와 같은 모양의 markdown 표(모델 ·
test 재현율@1·@5 · MRR · 오연결률)로 보여준다. 오연결률은
`1 - no_answer_accuracy`(정답 없는 질의에 뭔가를 돌려준 비율)다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.4f}"


def report(labels: list[str]) -> None:
    print("| 모델 | 카드 | test 재현율@1 | test 재현율@5 | MRR | 오연결률 |")
    print("| --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"search_{label}.json"
        if not path.exists():
            print(
                f"| {label} | - | - | - | - | "
                f"(결과 없음: model_compare_search.py {label} 먼저 실행) |"
            )
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        t = d["test"]
        no_answer = t.get("no_answer_accuracy")
        bad = None if no_answer is None else round(1 - no_answer, 4)
        print(
            f"| {label} | {d['cards']} | {fmt(t['recall_at_1'])} "
            f"| {fmt(t['recall_at_5'])} | {fmt(t['mrr'])} | {fmt(bad)} |"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python model_compare_report.py <라벨1> [라벨2 ...]")
    report(sys.argv[1:])

"""model_compare_repo_routing.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_repo_routing_report.py <라벨1> <라벨2> ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fmt(v: float | None) -> str:
    return f"{v:.4f}" if v is not None else "-"


def main() -> None:
    labels = sys.argv[1:]
    if not labels:
        raise SystemExit("사용: python model_compare_repo_routing_report.py <라벨1> <라벨2> ...")
    header = "| 모델 | 레포 고르기 정확도 | 규칙 점수만 쓸 때 | 차이 | LLM 판단 채택률 |"
    print(header)
    print("| --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"repo_routing_{label}.json"
        if not path.exists():
            note = f"model_compare_repo_routing.py {label} 먼저 실행"
            print(f"| {label} | - | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['repo_pick_accuracy'])} | {fmt(m['rule_only_accuracy'])} "
            f"| {fmt(m['accuracy_delta'])} | {fmt(m['llm_used_rate'])} |"
        )


if __name__ == "__main__":
    main()

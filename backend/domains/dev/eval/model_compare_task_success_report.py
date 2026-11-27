"""model_compare_task_success.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_task_success_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_task_success_report.py <라벨1> <라벨2> ...")
    header = "| 모델 | 작업 성공률 | 머지율 | 머지 중 완료 기준 충족 | 평균 재시도 횟수 |"
    print(header)
    print("| --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"task_success_{label}.json"
        if not path.exists():
            note = f"model_compare_task_success.py {label} 먼저 실행"
            print(f"| {label} | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['task_success_rate'])} | {fmt(m['merge_rate'])} "
            f"| {fmt(m['criteria_met_given_merged'])} | {fmt(m['avg_attempts'])} |"
        )


if __name__ == "__main__":
    main()

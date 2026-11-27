"""model_compare_proposal.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_proposal_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_proposal_report.py <라벨1> <라벨2> ...")
    header = (
        "| 모델 | 형식 통과율 | LLM 경로 숫자 경고 0 비율 "
        "| 전체 숫자 경고 0 비율 | 평균 경고 수 |"
    )
    print(header)
    print("| --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"proposal_{label}.json"
        if not path.exists():
            note = f"model_compare_proposal.py {label} 먼저 실행"
            print(f"| {label} | - | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['format_pass_rate'])} | {fmt(m['llm_zero_warning_rate'])} "
            f"| {fmt(m['zero_warning_rate'])} | {fmt(m['avg_warnings'])} |"
        )


if __name__ == "__main__":
    main()

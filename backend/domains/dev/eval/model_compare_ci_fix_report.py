"""model_compare_ci_fix.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_ci_fix_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_ci_fix_report.py <라벨1> <라벨2> ...")
    header = "| 모델 | CI 자동 수정 성공률 | 고치려 시도한 비율 | 시도 중 성공 비율 |"
    print(header)
    print("| --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"ci_fix_{label}.json"
        if not path.exists():
            note = f"model_compare_ci_fix.py {label} 먼저 실행"
            print(f"| {label} | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['ci_fix_success_rate'])} | {fmt(m['fix_attempt_rate'])} "
            f"| {fmt(m['success_given_attempted'])} |"
        )


if __name__ == "__main__":
    main()

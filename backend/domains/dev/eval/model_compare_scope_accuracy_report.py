"""model_compare_scope_accuracy.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_scope_accuracy_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_scope_accuracy_report.py <라벨1> <라벨2> ...")
    header = (
        "| 모델 | 범위 정확도 | 머지율 | 파일 범위 지킨 비율 | 줄 범위 지킨 비율 "
        "| 미끼 파일 수 | 범위 밖 줄 수 |"
    )
    print(header)
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"scope_accuracy_{label}.json"
        if not path.exists():
            note = f"model_compare_scope_accuracy.py {label} 먼저 실행"
            print(f"| {label} | - | - | - | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['scope_accuracy'])} | {fmt(m['merge_rate'])} "
            f"| {fmt(m['file_scope_rate'])} | {fmt(m['line_scope_rate'])} "
            f"| {m['extra_files_total']} | {m['extra_lines_total']} |"
        )


if __name__ == "__main__":
    main()

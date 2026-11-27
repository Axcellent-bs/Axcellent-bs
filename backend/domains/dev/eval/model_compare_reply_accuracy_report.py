"""model_compare_reply_accuracy.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_reply_accuracy_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_reply_accuracy_report.py <라벨1> <라벨2> ...")
    header = (
        "| 모델 | 바꿀 파일·완료 기준 정확도 | 파일만 | 완료 기준만 "
        "| CI 원인 파일·줄 정확도 | 파일만 | 줄만 |"
    )
    print(header)
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"reply_accuracy_{label}.json"
        if not path.exists():
            note = f"model_compare_reply_accuracy.py {label} 먼저 실행"
            print(f"| {label} | - | - | - | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['files_done_accuracy'])} | {fmt(m['paths_accuracy'])} "
            f"| {fmt(m['done_accuracy'])} | {fmt(m['ci_cause_accuracy'])} "
            f"| {fmt(m['ci_file_accuracy'])} | {fmt(m['ci_line_accuracy'])} |"
        )


if __name__ == "__main__":
    main()

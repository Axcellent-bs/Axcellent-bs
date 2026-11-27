"""model_compare_spec_fill.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_spec_fill_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_spec_fill_report.py <라벨1> <라벨2> ...")
    header = (
        "| 모델 | 칸 채움률 | 답할 수 있는 질문 답변율 | 남은 질문 수 "
        "| 없는 답 지어낸 수 | 지어낸 숫자 걸린 건수 |"
    )
    print(header)
    print("| --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"spec_fill_{label}.json"
        if not path.exists():
            note = f"model_compare_spec_fill.py {label} 먼저 실행"
            print(f"| {label} | - | - | - | - (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['field_fill_rate'])} | {fmt(m['answerable_answered_rate'])} "
            f"| {m['remaining_question_count']} | {m['hallucinated_answer_count']} "
            f"| {m['invented_number_hits']} |"
        )


if __name__ == "__main__":
    main()

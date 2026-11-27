"""model_compare_spec.py가 모아둔 라벨별 결과를 표로 비교한다.

사용: uv run python model_compare_spec_report.py <라벨1> <라벨2> ...
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
        raise SystemExit("사용: python model_compare_spec_report.py <라벨1> <라벨2> ...")
    header = (
        "| 모델 | 초안 성공률 | 시간 초과 비율 | 요구사항 정리 통과율 "
        "| 평균 뺀 문장 수 | 걸림(숫자/범위/화면용어) |"
    )
    print(header)
    print("| --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"spec_{label}.json"
        if not path.exists():
            note = f"model_compare_spec.py {label} 먼저 실행"
            print(f"| {label} | - | - | - | - | (결과 없음: {note}) |")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        keys = ("invented_number_hits", "scope_widening_hits", "axcellent_term_hits")
        hits_s = " / ".join(str(m[k]) for k in keys)
        print(
            f"| {label} | {fmt(m['draft_rate'])} | {fmt(m['timeout_rate'])} "
            f"| {fmt(m['clean_requirement_rate'])} | {fmt(m['avg_dropped_sentences'])} | {hits_s} |"
        )


if __name__ == "__main__":
    main()

"""요청 관계 판단(same·conflict·update·related) LLM 모델 비교 — 채점/표.

사용: python model_compare_related_report.py <라벨1> [라벨2 ...]

`model_compare_related.py`로 모은 `results/model_compare/related_<라벨>.json` 파일들의
`metrics`(이미 `related_eval.py`가 계산한 것)를 모아 모델별 비교표를 보여준다. 채점을
다시 하지 않고 저장된 metrics를 그대로 읽는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.4f}"


def report(labels: list[str]) -> None:
    print("| 모델 | same 정밀도 | same 재현율 | 4분류 정확도 | none 정확도 | LLM 실패 |")
    print("| --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"related_{label}.json"
        if not path.exists():
            print(
                f"| {label} | - | - | - | - | "
                f"(결과 없음: model_compare_related.py {label} 먼저 실행) |"
            )
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        print(
            f"| {label} | {fmt(m['same']['precision'])} | {fmt(m['same']['recall'])} "
            f"| {fmt(m['four_class_accuracy'])} | {fmt(m['none_accuracy'])} "
            f"| {m['llm_failures']} |"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python model_compare_related_report.py <라벨1> [라벨2 ...]")
    report(sys.argv[1:])

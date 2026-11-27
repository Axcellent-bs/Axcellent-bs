"""카드 연결(15번, 중복 판단) 임베딩 모델 비교 — 채점/표.

사용: python model_compare_link_report.py <라벨1> [라벨2 ...]

`model_compare_link.py`로 모은 `results/model_compare/link_<라벨>.json`
파일들을 모아 모델별 비교표(모델 · test 정확도 · 정밀도 · 재현율 · 잘못
병합)를 보여준다. `test1.v2`(결정 문장 임베딩, 문턱값 기준)와
`test2.wrong_auto_merge`(LLM 판정까지 거친 뒤 자동 병합된 건 중 틀린
것)를 쓴다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.4f}"


def report(labels: list[str]) -> None:
    print("| 모델 | 정확도 | 정밀도 | 재현율 | 잘못 병합 |")
    print("| --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"link_{label}.json"
        if not path.exists():
            print(
                f"| {label} | - | - | - | "
                f"(결과 없음: model_compare_link.py {label} 먼저 실행) |"
            )
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        v2 = d["test1"]["v2"]
        wrong = d.get("test2", {}).get("wrong_auto_merge")
        wrong_s = str(wrong) if wrong is not None else "-"
        print(
            f"| {label} | {fmt(v2['accuracy'])} | {fmt(v2['precision'])} "
            f"| {fmt(v2['recall'])} | {wrong_s} |"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python model_compare_link_report.py <라벨1> [라벨2 ...]")
    report(sys.argv[1:])

"""영상 제작안 LLM 모델 비교 — 채점/표.

사용: python model_compare_video_plan_report.py <라벨1> [라벨2 ...]

`model_compare_video_plan.py`로 모은 `results/model_compare/video_plan_<라벨>.json`의
`metrics`(`video_plan_eval.py`가 이미 계산한 것)를 모아 모델별 비교표를 보여준다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.4f}"


def report(labels: list[str]) -> None:
    print(
        "| 모델 | 1판 통과율 | 다시 쓰기 비율 | 2판 통과율 | 최종 LLM 제작안 비율 "
        "| 금칙어/지어낸숫자/실명 걸림 |"
    )
    print("| --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"video_plan_{label}.json"
        if not path.exists():
            print(
                f"| {label} | - | - | - | - | "
                f"(결과 없음: model_compare_video_plan.py {label} 먼저 실행) |"
            )
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        m = d["metrics"]
        hits = m["watched_check_hits"]
        keys = ("marketing_word", "invented_offer_number", "real_name_in_scene")
        hits_s = " / ".join(str(hits[k]) for k in keys)
        print(
            f"| {label} | {fmt(m['first_draft_pass_rate'])} | {fmt(m['rewrite_rate'])} "
            f"| {fmt(m['second_draft_pass_rate'])} | {fmt(m['llm_plan_rate'])} | {hits_s} |"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python model_compare_video_plan_report.py <라벨1> [라벨2 ...]")
    report(sys.argv[1:])

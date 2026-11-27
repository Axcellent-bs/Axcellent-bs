"""총괄 의뢰 분류(20번) LLM 모델 비교 — 채점/표.

사용: python model_compare_request_report.py <라벨1> [라벨2 ...]

`model_compare_request.py`로 모은
`results/model_compare/request_<라벨>.json` 파일들(`eval_request_classifier.py`가
쓴 문장별 원본 행)을 모아, 같은 스크립트의 `verdict()` 채점 로직을
그대로 재사용해 모델별 비교표(정답·안전하게 빗나감·엉뚱하게 보냄·일
의뢰 적중)를 보여준다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from eval_request_classifier import verdict  # noqa: E402


def report(labels: list[str]) -> None:
    print("| 모델 | 정답 | 안전 | 엉뚱 | 일 의뢰 적중 |")
    print("| --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "results" / "model_compare" / f"request_{label}.json"
        if not path.exists():
            print(
                f"| {label} | - | - | - | "
                f"(결과 없음: model_compare_request.py {label} 먼저 실행) |"
            )
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        counts = {"ok": 0, "safe": 0, "wrong": 0}
        tasks = [r for r in rows if r["expect"] == "task"]
        hit = 0
        for r in rows:
            v = verdict(r, r["outcome"], r["got_task"])
            counts[v] += 1
            if r["expect"] == "task" and v == "ok":
                hit += 1
        print(
            f"| {label} | {counts['ok']}/{len(rows)} | {counts['safe']} "
            f"| {counts['wrong']} | {hit}/{len(tasks)} |"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python model_compare_request_report.py <라벨1> [라벨2 ...]")
    report(sys.argv[1:])

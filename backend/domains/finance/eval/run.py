"""재무 AI 계산 정확도 평가: uv run python domains/finance/eval/run.py [--n 1000] [--seed N]

정답 계산·무작위 장부 만들기는 `finance_agent.accuracy`에 있다(자기 테스트도 같은 것을 쓴다).
결과: 영역별 정확도·틀린 케이스 예시를 출력하고 `--out` 파일(JSON)에 저장한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finance_agent.accuracy import evaluate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261109)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    summary = evaluate(args.n, args.seed)
    results, total, total_ok = summary["areas"], summary["n"], summary["correct"]
    print(f"재무 AI 계산 정확도  seed={args.seed}  표본 {total}개  기준일 {summary['today']}")
    print(f"{'영역':<44}{'표본':>6}{'정답':>6}{'정확도':>9}")
    for r in results.values():
        print(f"{r['label']:<44}{r['n']:>6}{r['correct']:>6}{r['accuracy']:>9.1%}")
    print(f"{'전체':<44}{total:>6}{total_ok:>6}{total_ok / total:>9.1%}")
    for area, r in results.items():
        for w in r["wrong"][:3]:
            print(f"[틀림] {area}: {json.dumps(w, ensure_ascii=False, default=str)[:400]}")
    if args.out:
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

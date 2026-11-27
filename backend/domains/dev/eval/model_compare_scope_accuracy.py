"""범위 정확도 모델 비교: 라벨 하나로 scope_accuracy_eval.py를 돌리고 결과를 모델별 파일로 남긴다.

사용: DEV_LLM_MODEL=<모델> uv run python model_compare_scope_accuracy.py <라벨>
결과: results/model_compare/scope_accuracy_<라벨>.json
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("사용: python model_compare_scope_accuracy.py <라벨>")
    label = sys.argv[1]
    out = HERE / "results" / "model_compare" / f"scope_accuracy_{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(HERE / "scope_accuracy_eval.py"), "--out", str(out)], cwd=HERE
    )
    if result.returncode != 0:
        raise SystemExit(f"scope_accuracy_eval.py 실패 (종료 코드 {result.returncode})")


if __name__ == "__main__":
    main()

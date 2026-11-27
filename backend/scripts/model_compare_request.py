"""총괄 의뢰 분류(20번) LLM 모델 비교 — 수집.

사용: python model_compare_request.py <라벨> [--env-file 경로]

`eval_request_classifier.py`를 서브프로세스로 그대로 돌린다(새
골든셋·채점 로직을 만들지 않는다). 모델은 평소와 같이
`ORCHESTRATOR_LLM_MODEL` 등 process env(또는 `--env-file`)로 정하고,
결과를 `scripts/results/model_compare/request_<라벨>.json`에 라벨을
붙여 쌓는다.

- 문장 30개마다 실제 LLM을 한 번씩 부르므로 모델 하나 돌릴 때마다 과금된다.
- 이 세션(클라우드 샌드박스)에는 키가 없어 실행만 확인했다(배선 확인). 실제
  비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def collect(label: str, env_file: str | None) -> Path:
    out_dir = HERE / "results" / "model_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"request_{label}.json"
    cmd = [sys.executable, str(HERE / "eval_request_classifier.py"), "--out", str(out_path)]
    if env_file:
        cmd += ["--env-file", env_file]
    proc = subprocess.run(cmd, cwd=HERE, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"eval_request_classifier.py 실패 (종료 코드 {proc.returncode})")
    print(f"저장: {out_path.relative_to(HERE.parent)} (라벨: {label})")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    collect(args.label, args.env_file)

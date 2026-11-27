"""영상 제작안 LLM 모델 비교 — 수집.

사용: python model_compare_video_plan.py <라벨> [--env-file 경로]

`video_plan_eval.py`를 서브프로세스로 그대로 돌린다(새 골든셋·채점 로직을 만들지 않는다).
모델은 평소와 같이 `PLANNING_LLM_MODEL` 등 process env(또는 `--env-file`)로 정하고,
결과를 `results/model_compare/video_plan_<라벨>.json`에 라벨을 붙여 쌓는다.

- 케이스 10개마다 제작안 LLM 호출(최대 2회) + 콘티 SVG 호출 1회가 들어 모델 하나당 최대
  30회 호출. 과금된다.
- 이 세션(클라우드 샌드박스)에는 키가 없어 실행만 확인했다(배선 확인). 실제 비교 숫자는
  키가 있는 로컬 PC에서 위 명령으로 내야 한다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def collect(label: str, env_file: str | None) -> Path:
    out_dir = HERE / "results" / "model_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"video_plan_{label}.json"
    cmd = [sys.executable, str(HERE / "video_plan_eval.py"), "--out", str(out_path)]
    env = None
    if env_file:
        env = dict(os.environ)
        for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #")[0].strip().strip('"')
            if value:
                env[key.strip()] = value
    proc = subprocess.run(cmd, cwd=HERE, env=env, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"video_plan_eval.py 실패 (종료 코드 {proc.returncode})")
    print(f"저장: {out_path.relative_to(HERE.parent)} (라벨: {label})")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    collect(args.label, args.env_file)

"""카드 연결(15번, 중복 판단) 임베딩 모델 비교 — 수집.

사용: python model_compare_link.py <라벨>

`link_eval_v2.py`를 서브프로세스로 그대로 돌린다(새 평가 데이터·채점
로직을 만들지 않는다). 그 스크립트의 고정 출력(`results/link_eval_v2.json`)을
`results/model_compare/link_<라벨>.json`으로 복사해 모델별로 쌓는다.

- 모델은 `backend/.env`(또는 process env)의 MEMORY_EMBED_MODEL 등 지금
  설정된 것을 그대로 쓴다(`run_eval.py`의 `dense` 방식과 같은 설정).
- `link_eval_v2.py`는 `run_eval.py`의 `dense-mock`과 달리 네트워크 없는
  배선 확인 경로가 없다(임베딩과 Claude 중복 판정 호출이 모두 실제
  호출). 이 세션(클라우드 샌드박스)에는 키가 없어 이 스크립트는 "서브
  프로세스가 실패를 제대로 전달하는지"만 확인했고, 실제 비교 숫자는
  키가 있는 로컬 PC에서 내야 한다.
- 모델 하나 돌릴 때마다 중복 판정 LLM 호출(최대 60회)과 제목 제안 호출이
  들어가므로 과금된다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def collect(label: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "link_eval_v2.py"],
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"link_eval_v2.py 실패 (종료 코드 {proc.returncode})")
    src = HERE / "results" / "link_eval_v2.json"
    out_dir = HERE / "results" / "model_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"link_{label}.json"
    shutil.copyfile(src, out_path)
    print(f"저장: {out_path.relative_to(HERE)} (라벨: {label})")
    return json.loads(out_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용: python model_compare_link.py <라벨>")
    collect(sys.argv[1])

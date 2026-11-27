"""카드 검색(9번) 임베딩 모델 비교 — 수집.

사용: python model_compare_search.py <라벨> [카드 수] [방식]

- `run_eval.py`의 `dense` 방식을 그대로 재사용한다. 새 평가 데이터·새 채점 로직을
  만들지 않고, `backend/.env`(또는 process env)의 MEMORY_EMBED_MODEL(없으면
  EMBED_MODEL)로 지금 설정된 모델 하나를 돌려
  `results/model_compare/search_<라벨>.json`에 저장한다.
- 벡터 캐시(`backend/.data/eval_vectors.db`)는 글 해시와 모델 이름을 함께
  저장하므로, 모델을 바꿔 가며 여러 번 돌려도 서로 섞이지 않는다.
- 모델마다 이 스크립트를 한 번씩 돌린 뒤
  `model_compare_report.py <라벨1> <라벨2> ...`로 비교표를 뽑는다.
- 방식에 `dense-mock`을 주면 네트워크·키 없이 배선만 확인한다(실제 비교
  숫자가 아니다).
- 고객지원(병환) `embed-compare`의 `collect_variant.py`/`score_variant.py`와
  같은 구조다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from run_eval import main as run_main

HERE = Path(__file__).parent


def collect(label: str, size: int = 620, method: str = "dense") -> dict:
    result = run_main(method, size)
    out_dir = HERE / "results" / "model_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"search_{label}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rel = out_path.relative_to(HERE)
    print(f"저장: {rel} (라벨: {label}, 방식: {method}, 카드 {size}장)")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "사용: python model_compare_search.py <라벨> [카드 수] [방식=dense|dense-mock]"
        )
    label = sys.argv[1]
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 620
    method = sys.argv[3] if len(sys.argv) > 3 else "dense"
    collect(label, size, method)

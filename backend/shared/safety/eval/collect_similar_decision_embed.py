"""안전 로그 "비슷한 과거 결정 띄우기"(태윤 제안, 아직 없는 새 기능): 글자 2-그램 유사도 대신
임베딩 유사도로 검색했을 때를 재는 1단계.

- `similar_decision_cases.json`의 과거 결정(`corpus`)과 지금 들어온 요청 글(`queries`)을 모두
  임베딩하고, query마다 corpus 전체와의 코사인 유사도를 남긴다(item 1~3과 같은 수집·채점 분리
  방식). 2단계 `score_similar_decision_embed.py`가 순위를 매겨 top-1·top-3 정확도를 잰다.

사용법 (.env에 실제 EMBED_* 키가 있는 로컬에서):
    cd backend
    EMBED_MODEL=gemini-embedding-001 \
      uv run python shared/safety/eval/collect_similar_decision_embed.py gemini-embedding-001

라벨(인자)은 출력 파일 이름에만 쓴다: results/similar_decision_embed_sims/sims_<라벨>.json
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND / "shared" / "agent-template" / "src"))

from ax_agent_template.llm import LLMClient  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("사용법: collect_similar_decision_embed.py <결과 파일에 쓸 라벨>")
        return 2
    label = sys.argv[1]

    data = json.loads((HERE / "similar_decision_cases.json").read_text(encoding="utf-8"))
    corpus, queries = data["corpus"], data["queries"]

    unique = sorted({*(c["text"] for c in corpus), *(q["text"] for q in queries)})

    llm = LLMClient("safety")  # SAFETY_EMBED_MODEL 우선, 없으면 EMBED_MODEL
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(unique), 50):
        chunk = unique[start : start + 50]
        for text, vec in zip(chunk, await llm.embed(chunk), strict=True):
            vectors[text] = vec

    model = llm.settings.embed_model
    sims: dict[str, dict[str, float]] = {}
    for q in queries:
        sims[q["id"]] = {
            c["id"]: round(cosine(vectors[q["text"]], vectors[c["text"]]), 4) for c in corpus
        }

    out = {
        "model": model,
        "label": label,
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sims": sims,
    }
    out_dir = HERE / "results" / "similar_decision_embed_sims"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sims_{label}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_path} 에 저장 (model={model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

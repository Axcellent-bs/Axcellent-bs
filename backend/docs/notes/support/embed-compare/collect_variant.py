"""임베딩 모델 비교: data 부서의 기존 보정셋(`domains/data/eval/product_link/`)을
**읽기만** 해서, 다른 임베딩 모델로 다시 재는 스크립트.

왜 이렇게 하나:
- data 부서가 이미 만든 golden set(정답은 질의를 만든 "방식"에서만 정했고, 유사도를 보고
  정하지 않았다 — README 참고)을 그대로 재사용하면 "객관적" 비교가 된다. 모델마다 새로
  정답을 매기지 않는다.
- `collect.py`(data 부서 소유)는 매번 인기 검색어·YouTube 피드를 새로 불러오기 때문에,
  모델별로 따로 돌리면 데이터셋 자체가 날마다 달라져 비교가 안 된다. 그래서 이미 저장된
  `topics.json`·`catalog.json`(고정값)만 읽고, basic 글 모양만 쓴다(rich는 근거 글이
  저장되어 있지 않아 재현 불가능).
- data 부서 폴더에는 아무것도 쓰지 않는다. 출력은 이 폴더(`support` 담당 notes) 아래
  `sims/`에만 쓴다.

사용법 (.env에 실제 EMBED_* 키가 있는 로컬에서):

    cd backend
    EMBED_MODEL=gemini-embedding-001 \
      uv run python docs/notes/support/embed-compare/collect_variant.py gemini-embedding-001

    EMBED_MODEL=text-embedding-3-large EMBED_BASE_URL=https://api.openai.com/v1 EMBED_API_KEY=... \
      uv run python docs/notes/support/embed-compare/collect_variant.py text-embedding-3-large

    EMBED_MODEL=text-embedding-3-small EMBED_BASE_URL=https://api.openai.com/v1 EMBED_API_KEY=... \
      uv run python docs/notes/support/embed-compare/collect_variant.py text-embedding-3-small

    EMBED_MODEL=solar-embedding-1-large EMBED_BASE_URL=https://api.upstage.ai/v1/solar EMBED_API_KEY=... \
      uv run python docs/notes/support/embed-compare/collect_variant.py solar-embedding-1-large

라벨(두 번째 인자)은 출력 파일 이름에만 쓴다: sims/basic_<라벨>.json
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_EVAL = HERE.parent.parent.parent.parent / "domains" / "data" / "eval" / "product_link"
sys.path.insert(0, str(DATA_EVAL))  # rules.py만 읽기용으로 가져온다 (읽기, import는 수정이 아님)

from rules import basic_product_text  # noqa: E402

sys.path.insert(0, str(HERE.parent.parent.parent.parent / "shared" / "agent-template" / "src"))
from ax_agent_template.llm import LLMClient  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("사용법: collect_variant.py <결과 파일에 쓸 라벨, 예: text-embedding-3-large>")
        return 2
    label = sys.argv[1]

    topics = json.loads((DATA_EVAL / "topics.json").read_text(encoding="utf-8"))["topics"]
    catalog = json.loads((DATA_EVAL / "catalog.json").read_text(encoding="utf-8"))["products"]

    topic_texts = [t["text"] for t in topics]
    product_texts = [basic_product_text(p) for p in catalog]
    unique = sorted({*topic_texts, *product_texts})

    llm = LLMClient("data")  # EMBED_MODEL 등은 환경변수에서 (DATA_EMBED_* 우선, 없으면 EMBED_*)
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(unique), 50):
        chunk = unique[start : start + 50]
        for text, vec in zip(chunk, await llm.embed(chunk), strict=True):
            vectors[text] = vec

    model = llm.settings.embed_model
    out = {
        "form": "basic",
        "model": model,
        "label": label,
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "products": [p["product_id"] for p in catalog],
        "sims": {
            t["id"]: [round(cosine(vectors[tt], vectors[pt]), 4) for pt in product_texts]
            for t, tt in zip(topics, topic_texts, strict=True)
        },
    }
    (HERE / "sims").mkdir(exist_ok=True)
    out_path = HERE / "sims" / f"basic_{label}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_path} 에 저장 (model={model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
